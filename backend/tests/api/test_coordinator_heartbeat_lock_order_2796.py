"""#2796 — coordinator-heartbeat 的行锁集合内部也要全序（#1980 残余半边）。

`#1980` 只补齐了**方向**全序（先 `job_instance` 后 `plan_run_host`）；集合**内部**
的取锁顺序仍是 agent payload 序（= agent 侧字典插入序），与 `extend_leases_batch`
的 `ORDER BY id`（#992 全序约定）交错仍可成环。修复后两段循环各自按 id 升序取锁。

判据用阻塞法定死（形态沿用 `#1980` / `#1959`）：另一会话占住**升序第一条**应被
锁定的行，payload 故意按**降序**给出 ⇒

* 修复后：心跳先在该行排队，集合中其余行**尚未被锁**（NOWAIT 试锁成功）；
* 修复前：心跳按 payload 序先锁了后面的行才排队（NOWAIT 试锁失败）。

需要 PostgreSQL：SQLite 既无行锁冲突检测，也观察不到锁等待。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy.exc
from sqlalchemy import select, text

from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
from backend.models.enums import HostStatus, JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun, PlanRunHost
from backend.services.agent_coordinator_heartbeat import (
    _CoordinatorHeartbeatIn,
    _CoordinatorHeartbeatJob,
    record_agent_coordinator_heartbeat,
)

PIPELINE_DEF = {"lifecycle": {"init": [], "teardown": []}}


def _seed_two() -> dict:
    """host + 2×(run/device/job/prh) —— ``plan_run_host`` 有 ``(plan_run_id, host_id)``
    唯一约束，故两行必须在**两个 run** 上（同 host 多 run 是生产常态）。"""
    suffix = uuid4().hex[:8]
    host_id = f"hbo-{suffix}"
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        host = Host(id=host_id, hostname=f"h-{suffix}",
                    status=HostStatus.ONLINE.value, created_at=now)
        plan = Plan(name=f"hbo-{suffix}", description="lock order (#2796)",
                    created_by="pytest")
        db.add_all([host, plan])
        db.flush()

        prh_ids, job_ids = [], []
        for i in range(2):
            run = PlanRun(plan_id=plan.id, status="RUNNING", triggered_by="pytest",
                          plan_snapshot={"name": plan.name, "plan_id": plan.id},
                          run_type="MANUAL", started_at=now, total_job_count=1)
            db.add(run)
            db.flush()
            device = Device(serial=f"HBO-{suffix}-{i}", host_id=host_id,
                            status="ONLINE", tags=[], created_at=now,
                            adb_connected=True, adb_state="device")
            db.add(device)
            db.flush()
            job = JobInstance(
                plan_run_id=run.id, plan_id=plan.id, device_id=device.id,
                host_id=host_id, status=JobStatus.RUNNING.value,
                pipeline_def=PIPELINE_DEF, created_at=now, updated_at=now,
                started_at=now,
            )
            db.add(job)
            db.flush()
            job_ids.append(job.id)
            prh = PlanRunHost(
                plan_run_id=run.id, host_id=host_id, device_count=1,
                status="RUNNING", phase="PATROL", coordinator_epoch=3,
                admitted_at=now,
            )
            db.add(prh)
            db.flush()
            prh_ids.append((prh.id, run.id))
        db.commit()
        return {
            "host_id": host_id,
            "job_ids": job_ids,
            "prh_ids": [p for p, _ in prh_ids],
            "run_ids": [r for _, r in prh_ids],
        }
    finally:
        db.close()


async def _session_pid(db) -> int:
    return (await db.execute(text("SELECT pg_backend_pid()"))).scalar()


async def _wait_until_blocked(observer, pid: int, timeout: float = 15.0) -> bool:
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
    try:
        await observer.execute(stmt)
    except sqlalchemy.exc.DBAPIError as exc:  # 55P03 lock_not_available
        return exc
    finally:
        await observer.rollback()
    return None


async def test_coordinator_heartbeat_locks_jobs_in_id_order():
    """job 行锁集合内部按 id 升序：阻塞在低 id 行时，高 id 行不得已被锁。"""
    seed = _seed_two()
    job_lo, job_hi = seed["job_ids"]

    await async_engine.dispose()
    async with (
        AsyncSessionLocal() as db_blocker,
        AsyncSessionLocal() as db_observer,
        AsyncSessionLocal() as db_ep,
    ):
        await db_blocker.execute(
            select(JobInstance).where(JobInstance.id == job_lo).with_for_update()
        )

        payload = _CoordinatorHeartbeatIn(
            host_id=seed["host_id"],
            agent_instance_id=seed["host_id"],
            plan_run_hosts=[],
            # payload 故意**降序**：修复前先锁高 id 行 ⇒ 与 extend_leases_batch 交错成环。
            jobs=[
                _CoordinatorHeartbeatJob(job_id=job_hi, execution_state="EXECUTING_STEP"),
                _CoordinatorHeartbeatJob(job_id=job_lo, execution_state="EXECUTING_STEP"),
            ],
        )
        ep_pid = await _session_pid(db_ep)
        task = asyncio.create_task(record_agent_coordinator_heartbeat(db_ep, payload))
        try:
            assert await _wait_until_blocked(db_observer, ep_pid), (
                "coordinator_heartbeat 应先在低 id job 行（id 升序第一条）排队"
            )
            hi_lock_error = await _probe_locked(db_observer, (
                select(JobInstance)
                .where(JobInstance.id == job_hi)
                .with_for_update(nowait=True)
            ))
            assert hi_lock_error is None, (
                "心跳在等待低 id job 行时已持有高 id job 行锁 —— job 行锁集合内部"
                f"仍是 payload 序（#2796）：{hi_lock_error}"
            )
        finally:
            await db_blocker.rollback()
            await asyncio.wait_for(task, timeout=15)

        for job_id in (job_lo, job_hi):
            state = (await db_observer.execute(
                select(JobInstance.execution_state).where(JobInstance.id == job_id)
            )).scalar_one()
            assert state == "EXECUTING_STEP"


async def test_coordinator_heartbeat_locks_prh_in_id_order():
    """plan_run_host 行锁集合内部按 id 升序（同上；#1980 的方向序不变）。"""
    seed = _seed_two()
    prh_lo, prh_hi = seed["prh_ids"]

    await async_engine.dispose()
    async with (
        AsyncSessionLocal() as db_blocker,
        AsyncSessionLocal() as db_observer,
        AsyncSessionLocal() as db_ep,
    ):
        await db_blocker.execute(
            select(PlanRunHost).where(PlanRunHost.id == prh_lo).with_for_update()
        )

        entries = [
            {
                "id": prh_id, "plan_run_id": run_id,
                "host_id": seed["host_id"], "coordinator_epoch": 4,
                "phase": "PATROL",
            }
            for prh_id, run_id in zip(seed["prh_ids"], seed["run_ids"], strict=True)
        ]
        payload = _CoordinatorHeartbeatIn(
            host_id=seed["host_id"],
            agent_instance_id=seed["host_id"],
            plan_run_hosts=list(reversed(entries)),   # 降序 payload
            jobs=[],
        )
        ep_pid = await _session_pid(db_ep)
        task = asyncio.create_task(record_agent_coordinator_heartbeat(db_ep, payload))
        try:
            assert await _wait_until_blocked(db_observer, ep_pid), (
                "coordinator_heartbeat 应先在低 id plan_run_host 行排队"
            )
            hi_lock_error = await _probe_locked(db_observer, (
                select(PlanRunHost)
                .where(PlanRunHost.id == prh_hi)
                .with_for_update(nowait=True)
            ))
            assert hi_lock_error is None, (
                "心跳在等待低 id plan_run_host 行时已持有高 id 行锁 —— plan_run_host"
                f" 行锁集合内部仍是 payload 序（#2796）：{hi_lock_error}"
            )
        finally:
            await db_blocker.rollback()
            await asyncio.wait_for(task, timeout=15)

        for prh_id in (prh_lo, prh_hi):
            epoch = (await db_observer.execute(
                select(PlanRunHost.coordinator_epoch).where(PlanRunHost.id == prh_id)
            )).scalar_one()
            assert epoch == 4
