"""#1980 — 共享行加锁全序回归：coordinator-heartbeat 与 extend_lock。

`#1959` 统一了 **Job → Lease**（终态 `complete_job`、批量续租 `extend_leases_batch`、
过期回收 `_reconcile_expired_leases`）。按 #1960 新增的方法——**以共享行为单位枚举
全部加锁点**——对剩余共享行再枚举一遍，还有两处反向：

| 共享行组合 | 正序 | 反序（本单修复对象） |
|---|---|---|
| `job_instance` → `plan_run_host` | `complete_job` → `on_job_terminal` → `_bump_host_counters`（`job_terminalization.py`） | **`coordinator_heartbeat`**：先改 `PlanRunHost`（ORM 变更在后续语句 autoflush 落库），再 `UPDATE job_instance` |
| `job_instance` → `device_leases` | `complete_job` / `extend_leases_batch` / `_reconcile_expired_leases` | **`extend_job_lock`**：先 `extend_lease`（UPDATE `device_leases`），再 `job.updated_at`（UPDATE `job_instance`） |

判据沿用 `#1959` 的形态：**不能只看返回值/异常**——死锁的受害者可能把异常吞进通用
`except`，或被路由转成 409/500，只看返回码会漏判。这里用第三会话
`FOR UPDATE NOWAIT` 直接探测「等待方是否已经持有它不该持有的行锁」，再释放对方后核对
业务结果。

需要 PostgreSQL：SQLite 既没有行锁冲突检测，也观察不到锁等待。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import sqlalchemy.exc
from sqlalchemy import select, text

# #2116：需要 PostgreSQL 行锁（FOR UPDATE / NOWAIT）。本仓 harness 总是提供 PG（conftest 的
# testcontainers / CI 的 PG service），无 PG 时在 conftest 阶段就报错——**刻意不写**「非 PG
# 就 skip」的分支：它在 conftest 覆盖 DATABASE_URL 之后不可达，只会把环境问题变成静默跳过。

from backend.api.routes.agent_api import (
    _CoordinatorHeartbeatIn,
    _CoordinatorHeartbeatJob,
    _ExtendLockIn,
    coordinator_heartbeat,
    extend_job_lock,
)
from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobArtifact, JobInstance, StepTrace
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun, PlanRunHost

PIPELINE_DEF = {"lifecycle": {"init": [], "teardown": []}}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _seed(*, lease_seconds: float = 600.0) -> dict:
    """host + device + RUNNING job + ACTIVE 租约 + RUNNING PlanRun + PlanRunHost。"""
    suffix = uuid4().hex[:8]
    host_id = f"lso-{suffix}"
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{suffix}",
            status=HostStatus.ONLINE.value, created_at=now,
        )
        plan = Plan(
            name=f"lso-{suffix}", description="shared row lock order",
            failure_threshold=0.0, created_by="pytest",
        )
        db.add_all([host, plan])
        db.flush()

        device = Device(
            serial=f"LSO-{suffix}", host_id=host_id, status="ONLINE",
            tags=[], created_at=now, adb_connected=True, adb_state="device",
        )
        db.add(device)
        db.flush()

        run = PlanRun(
            plan_id=plan.id, status="RUNNING", failure_threshold=0.0,
            plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL", triggered_by="pytest", started_at=now,
            total_job_count=1,
        )
        db.add(run)
        db.flush()

        prh = PlanRunHost(
            plan_run_id=run.id, host_id=host_id, device_count=1,
            status="RUNNING", phase="PATROL", coordinator_epoch=3,
            admitted_at=now,
        )
        db.add(prh)
        db.flush()

        job = JobInstance(
            plan_run_id=run.id, plan_id=plan.id, device_id=device.id,
            host_id=host_id, status=JobStatus.RUNNING.value,
            pipeline_def=PIPELINE_DEF, created_at=now, updated_at=now,
            started_at=now,
        )
        db.add(job)
        db.flush()

        lease = DeviceLease(
            device_id=device.id, job_id=job.id, host_id=host_id,
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{device.id}:1", lease_generation=1,
            agent_instance_id=host_id, acquired_at=now, renewed_at=now,
            expires_at=now + timedelta(seconds=lease_seconds),
        )
        db.add(lease)
        db.flush()

        seed = {
            "host_id": host_id,
            "device_id": device.id,
            "job_id": job.id,
            "lease_id": lease.id,
            "plan_id": plan.id,
            "plan_run_id": run.id,
            "prh_id": prh.id,
            "token": lease.fencing_token,
            "lease_expires_at": lease.expires_at,
        }
        db.commit()
        return seed
    finally:
        db.close()


def _cleanup(seed: dict) -> None:
    db = SessionLocal()
    try:
        db.execute(text("SET statement_timeout = '15s'"))
        for model, column, key in (
            (DeviceLease, DeviceLease.job_id, "job_id"),
            (StepTrace, StepTrace.job_id, "job_id"),
            (JobArtifact, JobArtifact.job_id, "job_id"),
        ):
            db.query(model).filter(column == seed[key]).delete()
        db.query(DeviceLease).filter(DeviceLease.device_id == seed["device_id"]).delete()
        db.query(JobInstance).filter(JobInstance.id == seed["job_id"]).delete()
        db.query(PlanRunHost).filter(PlanRunHost.id == seed["prh_id"]).delete()
        db.query(PlanRun).filter(PlanRun.id == seed["plan_run_id"]).delete()
        db.query(PlanStep).filter(PlanStep.plan_id == seed["plan_id"]).delete()
        db.query(Plan).filter(Plan.id == seed["plan_id"]).delete()
        db.query(Device).filter(Device.id == seed["device_id"]).delete()
        db.query(Host).filter(Host.id == seed["host_id"]).delete()
        db.commit()
    finally:
        db.close()


async def _session_pid(db) -> int:
    return (await db.execute(text("SELECT pg_backend_pid()"))).scalar()


async def _wait_until_blocked(observer, pid: int, timeout: float = 15.0) -> bool:
    """等到 pid 这条后端因等待行锁而阻塞（``wait_event_type='Lock'``）。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        row = (await observer.execute(
            text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
            {"pid": pid},
        )).first()
        if row is not None and row[0] == "Lock":
            return True
        await asyncio.sleep(0.05)
    return False


async def _probe_locked(observer, stmt) -> Exception | None:
    """用 NOWAIT 试锁，返回异常表示「别人已持有该行锁」。"""
    try:
        await observer.execute(stmt)
    except sqlalchemy.exc.DBAPIError as exc:  # 55P03 lock_not_available
        return exc
    finally:
        await observer.rollback()
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 回归 1: extend_lock —— 必须 Job → Lease
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_extend_lock_locks_job_before_lease():
    """另一会话持 Job 行锁时，extend_lock 不得已持有 `device_leases` 行锁。

    旧实现先 `UPDATE device_leases`（`extend_lease`）再 `UPDATE job_instance`，
    即 Lease → Job；与 complete / 批量续租 / 回收器的 Job → Lease 交错即成环。
    """
    seed = _seed()
    try:
        await async_engine.dispose()
        async with (
            AsyncSessionLocal() as db_blocker,
            AsyncSessionLocal() as db_observer,
            AsyncSessionLocal() as db_ep,
        ):
            await db_blocker.execute(
                select(JobInstance)
                .where(JobInstance.id == seed["job_id"])
                .with_for_update()
            )

            ep_pid = await _session_pid(db_ep)
            task = asyncio.create_task(extend_job_lock(
                job_id=seed["job_id"],
                payload=_ExtendLockIn(fencing_token=seed["token"]),
                db=db_ep, _=None,
            ))

            assert await _wait_until_blocked(db_observer, ep_pid), (
                "extend_lock 应先在 Job 行锁上排队（该行被另一会话持有）"
            )

            lease_lock_error = await _probe_locked(db_observer, (
                select(DeviceLease)
                .where(DeviceLease.id == seed["lease_id"])
                .with_for_update(nowait=True)
            ))

            await db_blocker.rollback()
            result = await asyncio.wait_for(task, timeout=15)

            assert lease_lock_error is None, (
                "extend_lock 在等待 Job 行锁时已持有 device_leases 行锁 —— "
                f"锁序仍是 Lease→Job（#1980 回归）：{lease_lock_error}"
            )
            assert result.data["job_id"] == seed["job_id"]

            lease = await db_ep.get(DeviceLease, seed["lease_id"])
            assert lease is not None
            assert lease.expires_at > seed["lease_expires_at"], "Job 锁放开后应完成续租"
    finally:
        _cleanup(seed)


# ══════════════════════════════════════════════════════════════════════════════
# 回归 2: coordinator-heartbeat —— 必须 job_instance → plan_run_host
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_coordinator_heartbeat_locks_jobs_before_plan_run_host():
    """另一会话持 Job 行锁时，coordinator-heartbeat 不得已持有 plan_run_host 行锁。

    旧实现先改 `PlanRunHost`（ORM 变更在后续语句 autoflush 时落库），再
    `UPDATE job_instance`，即 plan_run_host → job_instance；终态化路径是
    job_instance → plan_run_host（`on_job_terminal` → `_bump_host_counters`），
    两者交错即成环。
    """
    seed = _seed()
    try:
        await async_engine.dispose()
        async with (
            AsyncSessionLocal() as db_blocker,
            AsyncSessionLocal() as db_observer,
            AsyncSessionLocal() as db_ep,
        ):
            await db_blocker.execute(
                select(JobInstance)
                .where(JobInstance.id == seed["job_id"])
                .with_for_update()
            )

            payload = _CoordinatorHeartbeatIn(
                host_id=seed["host_id"],
                agent_instance_id=seed["host_id"],
                plan_run_hosts=[{
                    "id": seed["prh_id"],
                    "plan_run_id": seed["plan_run_id"],
                    "host_id": seed["host_id"],
                    "coordinator_epoch": 4,
                    "phase": "PATROL",
                }],
                jobs=[_CoordinatorHeartbeatJob(
                    job_id=seed["job_id"], execution_state="EXECUTING_STEP",
                )],
            )
            ep_pid = await _session_pid(db_ep)
            task = asyncio.create_task(coordinator_heartbeat(
                payload=payload, db=db_ep, _=None,
            ))

            assert await _wait_until_blocked(db_observer, ep_pid), (
                "coordinator_heartbeat 应先在 Job 行锁上排队（该行被另一会话持有）"
            )

            prh_lock_error = await _probe_locked(db_observer, (
                select(PlanRunHost)
                .where(PlanRunHost.id == seed["prh_id"])
                .with_for_update(nowait=True)
            ))

            await db_blocker.rollback()
            result = await asyncio.wait_for(task, timeout=15)

            assert prh_lock_error is None, (
                "coordinator_heartbeat 在等待 Job 行锁时已持有 plan_run_host 行锁 —— "
                f"锁序仍是 plan_run_host→job_instance（#1980 回归）：{prh_lock_error}"
            )
            assert result.data.accepted is True
            assert result.data.stale_plan_run_host_ids == []

            job = await db_ep.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.execution_state == "EXECUTING_STEP"
            prh = await db_ep.get(PlanRunHost, seed["prh_id"])
            assert prh is not None
            assert prh.coordinator_epoch == 4
            assert prh.coordinator_heartbeat_at is not None
    finally:
        _cleanup(seed)
