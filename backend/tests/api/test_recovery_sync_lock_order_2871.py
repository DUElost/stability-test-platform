"""#2871 — `recovery_sync` 的 job 行锁集合内部也要全序（#2796 同形第三处）。

`sync_agent_recovery` 原先按 **payload 上报顺序**逐条 `SELECT … FOR UPDATE`，且整函数
只在末尾提交一次——一次请求在上报期间持有全部 job 行锁。对侧 `extend_leases_batch`
按 `ORDER BY JobInstance.id` 全序取锁，两者交错（payload 降序 vs id 升序）即可成环，
与 #2796 / #2787 / #2635 属同一加锁顺序家族。

判据用阻塞法定死（形态沿用 #2796 / #2015）：另一会话占住**升序第一条**应被锁定的
行，payload 故意按**降序**给出 ⇒

* 修复后：recovery 先在该行排队，集合中其余行**尚未被锁**（NOWAIT 试锁成功）；
* 修复前：recovery 按 payload 序先锁了高 id 行才排队（NOWAIT 试锁失败）。

需要 PostgreSQL：SQLite 既无行锁冲突检测，也观察不到锁等待。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import sqlalchemy.exc
from sqlalchemy import select, text

from backend.api.routes.agent_api import (
    _RecoverySyncIn,
    recovery_sync,
)
from backend.services.agent_recovery import _ActiveJobEntry
from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
from backend.models.device_lease import DeviceLease
from backend.models.enums import (
    HostStatus,
    JobStatus,
    LeaseStatus,
    LeaseType,
    PlanRunStatus,
)
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun, PlanRunHost

PIPELINE_DEF = {"lifecycle": {"init": [], "teardown": []}}


def _seed_two() -> dict:
    """host(+boot_id) + 2×(device + RUNNING job + ACTIVE 租约) + 1 run/prh。"""
    suffix = uuid4().hex[:8]
    host_id = f"rlo-{suffix}"
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{suffix}",
            status=HostStatus.ONLINE.value, created_at=now,
            boot_id=f"boot-{suffix}",  # 与 payload 相同 → boot_matches
        )
        plan = Plan(name=f"rlo-{suffix}", description="recovery lock order (#2871)",
                    created_by="pytest")
        db.add_all([host, plan])
        db.flush()

        run = PlanRun(
            plan_id=plan.id, status=PlanRunStatus.RUNNING.value,
            plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL", triggered_by="pytest", started_at=now,
            total_job_count=2,
        )
        db.add(run)
        db.flush()
        prh = PlanRunHost(
            plan_run_id=run.id, host_id=host_id, device_count=2,
            status="RUNNING", coordinator_epoch=1, admitted_at=now,
        )
        db.add(prh)
        db.flush()

        device_ids, job_ids, tokens = [], [], []
        for i in range(2):
            device = Device(
                serial=f"RLO-{suffix}-{i}", host_id=host_id, status="ONLINE",
                tags=[], created_at=now, adb_connected=True, adb_state="device",
            )
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
            lease = DeviceLease(
                device_id=device.id, job_id=job.id, host_id=host_id,
                lease_type=LeaseType.JOB.value,
                status=LeaseStatus.ACTIVE.value,
                fencing_token=f"{device.id}:1", lease_generation=1,
                agent_instance_id=host_id,  # 与 payload 同实例 → 同实例 NOOP
                acquired_at=now, renewed_at=now,
                expires_at=now + timedelta(seconds=600),
            )
            db.add(lease)
            db.flush()
            device_ids.append(device.id)
            job_ids.append(job.id)
            tokens.append(lease.fencing_token)

        seed = {
            "host_id": host_id,
            "boot_id": host.boot_id,
            "plan_id": plan.id,
            "plan_run_id": run.id,
            "prh_id": prh.id,
            "device_ids": device_ids,
            "job_ids": job_ids,
            "tokens": tokens,
        }
        db.commit()
        return seed
    finally:
        db.close()


def _cleanup(seed: dict) -> None:
    db = SessionLocal()
    try:
        db.execute(text("SET statement_timeout = '15s'"))
        db.query(DeviceLease).filter(DeviceLease.job_id.in_(seed["job_ids"])).delete(
            synchronize_session=False
        )
        db.query(JobInstance).filter(JobInstance.id.in_(seed["job_ids"])).delete(
            synchronize_session=False
        )
        db.query(PlanRunHost).filter(PlanRunHost.id == seed["prh_id"]).delete()
        db.query(PlanRun).filter(PlanRun.id == seed["plan_run_id"]).delete()
        db.query(Plan).filter(Plan.id == seed["plan_id"]).delete()
        db.query(Device).filter(Device.id.in_(seed["device_ids"])).delete(
            synchronize_session=False
        )
        db.query(Host).filter(Host.id == seed["host_id"]).delete()
        db.commit()
    finally:
        db.close()


def _payload(seed: dict) -> _RecoverySyncIn:
    """active_jobs 故意**降序**：修复前先锁高 id 行 ⇒ 与 extend_leases_batch 成环。"""
    return _RecoverySyncIn(
        host_id=seed["host_id"],
        agent_instance_id=seed["host_id"],
        boot_id=seed["boot_id"],
        active_jobs=[
            _ActiveJobEntry(
                job_id=seed["job_ids"][1], device_id=seed["device_ids"][1],
                fencing_token=seed["tokens"][1],
            ),
            _ActiveJobEntry(
                job_id=seed["job_ids"][0], device_id=seed["device_ids"][0],
                fencing_token=seed["tokens"][0],
            ),
        ],
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


async def _probe_locked(observer, stmt) -> Exception | None:
    try:
        await observer.execute(stmt)
    except sqlalchemy.exc.DBAPIError as exc:  # 55P03 lock_not_available
        return exc
    finally:
        await observer.rollback()
    return None


async def test_recovery_sync_locks_jobs_in_id_order():
    """job 行锁集合内部按 id 升序：阻塞在低 id 行时，高 id 行不得已被锁。"""
    seed = _seed_two()
    job_lo, job_hi = seed["job_ids"]
    try:
        await async_engine.dispose()
        async with (
            AsyncSessionLocal() as db_blocker,
            AsyncSessionLocal() as db_observer,
            AsyncSessionLocal() as db_ep,
        ):
            await db_blocker.execute(
                select(JobInstance)
                .where(JobInstance.id == job_lo)
                .with_for_update()
            )

            ep_pid = await _session_pid(db_ep)
            task = asyncio.create_task(recovery_sync(
                payload=_payload(seed),
                db=db_ep, _=None,
            ))
            try:
                assert await _wait_until_blocked(db_observer, ep_pid), (
                    "recovery_sync 应先在低 id job 行（id 升序第一条）排队"
                )
                hi_lock_error = await _probe_locked(db_observer, (
                    select(JobInstance)
                    .where(JobInstance.id == job_hi)
                    .with_for_update(nowait=True)
                ))
                assert hi_lock_error is None, (
                    "recovery_sync 在等待低 id job 行时已持有高 id job 行锁 —— job 行锁"
                    f"集合内部仍是 payload 序（#2871）：{hi_lock_error}"
                )
            finally:
                await db_blocker.rollback()
                await asyncio.wait_for(task, timeout=30)

            # 释放阻塞后两条 job 都必须被处理（顺序修复不改判定）
            states = (await db_observer.execute(
                select(JobInstance.status).where(JobInstance.id.in_([job_lo, job_hi]))
            )).scalars().all()
            assert set(states) == {JobStatus.RUNNING.value}
    finally:
        _cleanup(seed)
