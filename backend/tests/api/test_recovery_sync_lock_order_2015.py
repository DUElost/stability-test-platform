"""#2015 — `recovery_sync` 必须先锁 job 行、再锁 lease 行（I1 共享行加锁全序）。

同一条 `(job_instance × device_leases)` 行组合上曾有两处相反顺序：

* **I1 基准**（`complete_job` / `extend_leases_batch` / `_reconcile_expired_leases` /
  `extend_job_lock`，见 `docs/notes/architecture/2026-09-14-shared-row-lock-table.md`）：
  **Job → Lease**。
* **`recovery_sync`**（修复前）：先 `SELECT device_leases … FOR UPDATE`（挡 Reconciler），
  再 `SELECT job_instance … FOR UPDATE`，即 **Lease → Job**。

Agent 重启后的 recovery 恰落在续租 tick 内时两侧交错即成环——09-15 生产 PG 日志里
复现 60 次的死锁环即此侧（此前 #1959/#1980/#1985/#2022 修的是其余各边）。

判据沿用 `#1959`/`#1980`：**不看返回值/异常**，用第三会话 `FOR UPDATE NOWAIT` 直接
探测「等待方是否已经持有 `device_leases` 行锁」。需要 PostgreSQL：SQLite 没有行锁
冲突检测，也观察不到锁等待。本仓 harness 总是提供 PG（conftest 的 testcontainers /
CI 的 PG service），无 PG 时在 conftest 阶段就报错——刻意不写「非 PG 就 skip」的
分支（#2116 同款理由）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import sqlalchemy.exc
from sqlalchemy import select, text

from backend.api.routes.agent_api import (
    _ActiveJobEntry,
    _RecoverySyncIn,
    recovery_sync,
)
from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType, PlanRunStatus
from backend.models.host import Device, Host
from backend.models.job import JobArtifact, JobInstance, StepTrace
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun, PlanRunHost

PIPELINE_DEF = {"lifecycle": {"init": [], "teardown": []}}


def _seed() -> dict:
    """host(+boot_id) + device + RUNNING job + ACTIVE 租约 + RUNNING PlanRun + PRH。"""
    suffix = uuid4().hex[:8]
    host_id = f"rsl-{suffix}"
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{suffix}",
            status=HostStatus.ONLINE.value, created_at=now,
            boot_id=f"boot-{suffix}",          # 与 recovery payload 相同 → boot_matches
        )
        plan = Plan(
            name=f"rsl-{suffix}", description="recovery sync lock order",
            failure_threshold=0.0, created_by="pytest",
        )
        db.add_all([host, plan])
        db.flush()

        device = Device(
            serial=f"RSL-{suffix}", host_id=host_id, status="ONLINE",
            tags=[], created_at=now, adb_connected=True, adb_state="device",
        )
        db.add(device)
        db.flush()

        run = PlanRun(
            plan_id=plan.id, status=PlanRunStatus.RUNNING.value,
            failure_threshold=0.0,
            plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL", triggered_by="pytest", started_at=now,
            total_job_count=1,
        )
        db.add(run)
        db.flush()

        prh = PlanRunHost(
            plan_run_id=run.id, host_id=host_id, device_count=1,
            status="RUNNING", coordinator_epoch=1, admitted_at=now,
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
            agent_instance_id=host_id,  # 与 payload.agent_instance_id 相同 → 同实例 NOOP
            acquired_at=now, renewed_at=now,
            expires_at=now + timedelta(seconds=600),
        )
        db.add(lease)
        db.flush()

        seed = {
            "host_id": host_id,
            "boot_id": host.boot_id,
            "device_id": device.id,
            "job_id": job.id,
            "lease_id": lease.id,
            "plan_id": plan.id,
            "plan_run_id": run.id,
            "prh_id": prh.id,
            "token": lease.fencing_token,
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


def _payload(seed: dict) -> _RecoverySyncIn:
    return _RecoverySyncIn(
        host_id=seed["host_id"],
        agent_instance_id=seed["host_id"],
        boot_id=seed["boot_id"],
        active_jobs=[_ActiveJobEntry(
            job_id=seed["job_id"],
            device_id=seed["device_id"],
            fencing_token=seed["token"],
        )],
        pending_outbox=[],
    )


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


@pytest.mark.asyncio(loop_scope="module")
async def test_recovery_sync_locks_job_before_lease():
    """另一会话持 Job 行锁时，recovery_sync 不得已持有 `device_leases` 行锁。

    修复前先锁 lease（挡 Reconciler）再锁 job，即 Lease → Job；与 I1 基准
    （complete / 批量续租 / 过期回收的 Job → Lease）交错即成环（#2015）。
    """
    seed = _seed()
    try:
        await async_engine.dispose()
        async with (
            AsyncSessionLocal() as db_blocker,
            AsyncSessionLocal() as db_observer,
            AsyncSessionLocal() as db_recovery,
        ):
            await db_blocker.execute(
                select(JobInstance)
                .where(JobInstance.id == seed["job_id"])
                .with_for_update()
            )

            recovery_pid = await _session_pid(db_recovery)
            task = asyncio.create_task(recovery_sync(
                payload=_payload(seed),
                db=db_recovery, _=None,
            ))

            assert await _wait_until_blocked(db_observer, recovery_pid), (
                "recovery_sync 应先在 Job 行锁上排队（该行被另一会话持有）"
            )

            lease_lock_error = None
            try:
                await db_observer.execute(
                    select(DeviceLease)
                    .where(DeviceLease.id == seed["lease_id"])
                    .with_for_update(nowait=True)
                )
            except sqlalchemy.exc.DBAPIError as exc:  # 55P03 lock_not_available
                lease_lock_error = exc
            finally:
                await db_observer.rollback()

            await db_blocker.rollback()
            result = await asyncio.wait_for(task, timeout=15)

            assert lease_lock_error is None, (
                "recovery_sync 在等待 Job 行锁时已持有 device_leases 行锁 —— "
                f"锁序仍是 Lease→Job（#2015 回归）：{lease_lock_error}"
            )
            actions = result.data["actions"]
            assert len(actions) == 1
            assert actions[0]["action"] == "NOOP", (
                f"锁放开后 recovery 应以同实例 NOOP 收敛：{actions}"
            )
            assert actions[0]["reason"] == "same_instance_worker_already_owned"

            lease = await db_recovery.get(DeviceLease, seed["lease_id"])
            assert lease is not None
            assert lease.status == LeaseStatus.ACTIVE.value, (
                "同实例 NOOP 路径不应触碰租约"
            )
    finally:
        _cleanup(seed)
