"""#3723 §3.4.4 / I5：设备返还路径 Job → Device → Lease 全序交错回归。

四组真实入口（隔离 PG、两独立 Session、有界等待）：
  a. 初始化 × 紧急释放（两种先后）——复核 F3
  b. ``_reconcile_expired_leases`` × ``acquire_lease``
  c. ``sync_agent_recovery`` × ``apply_discharge_sync``
  d. completion UNKNOWN 恢复分支 × ``init_device_return_sync``

修复前（释放路径 Lease→Device）与 Device→Lease 入口交错为 40P01；
修复后两侧完成、无死锁。文件名含 lock_order，接入 ci.yml PR 路径。
"""
from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event, current_thread
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.core.database import AsyncSessionLocal, SessionLocal
from backend.models.device_lease import DeviceLease
from backend.models.enums import JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.scheduler.device_lease_reconciler import _reconcile_expired_leases
from backend.services import device_return as dr
from backend.services.agent_completion import _RunCompleteIn, complete_agent_job
from backend.services.agent_recovery import (
    _ActiveJobEntry,
    _RecoverySyncIn,
    sync_agent_recovery,
)
from backend.services.device_lease_emergency_release import release_terminal_job_lease
from backend.services.lease_manager import acquire_lease

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
IS_PG = TEST_DATABASE_URL.startswith("postgresql")
pytestmark = pytest.mark.skipif(not IS_PG, reason="lock-order interleaving needs PostgreSQL")

_NOW = datetime.now(timezone.utc)


def _ensure_open_source_index(db):
    db.execute(text(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_device_return_debt_open_source
        ON device_return_debt (
            device_id, family, source_kind, (COALESCE(source_job_id, 0))
        )
        WHERE state = 'open'
        """
    ))
    db.commit()


def _seed_terminal(db):
    _ensure_open_source_index(db)
    suffix = uuid4().hex[:8]
    host = Host(
        id=f"lo3-{suffix}", hostname=f"lo3-{suffix}",
        status="ONLINE", last_heartbeat=_NOW, created_at=_NOW,
        boot_id=f"boot-{suffix}",
    )
    device = Device(
        serial=f"LO3-{suffix}", host_id=host.id, status="ONLINE",
        tags=[], created_at=_NOW, last_seen=_NOW, lease_generation=1,
    )
    plan = Plan(name=f"lo3-{suffix}", description="lock-order", created_by="test")
    db.add_all([host, device, plan])
    db.flush()
    run = PlanRun(
        plan_id=plan.id, status="RUNNING",
        plan_snapshot={"name": plan.name}, run_type="MANUAL", triggered_by="test",
    )
    db.add(run)
    db.flush()
    job = JobInstance(
        plan_run_id=run.id, plan_id=plan.id, device_id=device.id, host_id=host.id,
        status=JobStatus.COMPLETED.value,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        ended_at=_NOW,
        disarm_results={"power-cycle": {"result": "not_executed", "reason": "test"}},
    )
    db.add(job)
    db.flush()
    lease = DeviceLease(
        device_id=device.id, job_id=job.id, host_id=host.id,
        lease_type=LeaseType.JOB.value, status=LeaseStatus.ACTIVE.value,
        fencing_token=f"{device.id}:1", lease_generation=1,
        agent_instance_id=f"agent-{suffix}",
        acquired_at=_NOW, renewed_at=_NOW, expires_at=_NOW + timedelta(hours=1),
    )
    db.add(lease)
    db.commit()
    db.refresh(device)
    db.refresh(job)
    db.refresh(lease)
    return device, job, lease, host


def _seed_running(db, *, expired=False, unknown=False):
    _ensure_open_source_index(db)
    suffix = uuid4().hex[:8]
    host = Host(
        id=f"lor-{suffix}", hostname=f"lor-{suffix}",
        status="ONLINE", last_heartbeat=_NOW, created_at=_NOW,
        boot_id=f"boot-{suffix}",
    )
    device = Device(
        serial=f"LOR-{suffix}", host_id=host.id, status="ONLINE",
        tags=[], created_at=_NOW, last_seen=_NOW, lease_generation=1,
        adb_connected=True, adb_state="device",
    )
    plan = Plan(name=f"lor-{suffix}", description="lock-order-run", created_by="test")
    db.add_all([host, device, plan])
    db.flush()
    run = PlanRun(
        plan_id=plan.id, status="RUNNING",
        plan_snapshot={"name": plan.name, "plan_id": plan.id},
        run_type="MANUAL", triggered_by="test", started_at=_NOW, total_job_count=1,
    )
    db.add(run)
    db.flush()
    status = JobStatus.UNKNOWN.value if unknown else JobStatus.RUNNING.value
    job = JobInstance(
        plan_run_id=run.id, plan_id=plan.id, device_id=device.id, host_id=host.id,
        status=status,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        created_at=_NOW, updated_at=_NOW, started_at=_NOW,
        ended_at=(_NOW - timedelta(seconds=30)) if unknown else None,
    )
    db.add(job)
    db.flush()
    expires = (_NOW - timedelta(seconds=30)) if expired else (_NOW + timedelta(hours=1))
    lease = DeviceLease(
        device_id=device.id, job_id=job.id, host_id=host.id,
        lease_type=LeaseType.JOB.value, status=LeaseStatus.ACTIVE.value,
        fencing_token=f"{device.id}:1", lease_generation=1,
        agent_instance_id=host.id,
        acquired_at=_NOW, renewed_at=_NOW, expires_at=expires,
    )
    db.add(lease)
    db.commit()
    db.refresh(device)
    db.refresh(job)
    db.refresh(lease)
    return device, job, lease, host


def _set_timeouts(db):
    db.execute(text("SET LOCAL deadlock_timeout = '200ms'"))
    db.execute(text("SET LOCAL lock_timeout = '5s'"))


def _collect_errors(results):
    errors = []
    for r in results:
        if isinstance(r, Exception):
            orig = getattr(r, "orig", r)
            errors.append((type(r).__name__, getattr(orig, "sqlstate", None), str(r)))
    return errors


# ── a. 初始化 × 紧急释放（F3；修前红 / 修后绿）────────────────────────


@pytest.mark.parametrize("init_first", [True, False], ids=["init_then_emergency", "emergency_then_init"])
def test_init_and_emergency_release_lock_order(db_session, monkeypatch, init_first):
    """真实 init × emergency；修前 Lease→Device 与 Device→Lease 成环 → 40P01。"""
    device, _job, lease, _ = _seed_terminal(db_session)
    did, lid = device.id, lease.id
    device_locked = Event()
    emergency_reached_device_helper = Event()

    real_lock = dr._lock_device_sync
    real_active = dr._active_lease_sync

    def lock_device(db, device_id):
        # 紧急释放统一入口仍会再调 helper（已持 Device 行锁时为重入）
        if current_thread().name.endswith("_1"):
            emergency_reached_device_helper.set()
        result = real_lock(db, device_id)
        if current_thread().name.endswith("_0"):
            device_locked.set()
        return result

    def active_lease(db, device_id):
        if current_thread().name.endswith("_0") and init_first:
            # 给紧急释放时间跑到 Job 之后；修前它会先拿 Lease 再进 helper
            emergency_reached_device_helper.wait(1.0)
        return real_active(db, device_id)

    monkeypatch.setattr(dr, "_lock_device_sync", lock_device)
    monkeypatch.setattr(dr, "_active_lease_sync", active_lease)

    def run_init():
        with SessionLocal() as db:
            _set_timeouts(db)
            try:
                return dr.init_device_return_sync(db, device_id=did, dry_run=True)
            finally:
                db.rollback()

    def run_emergency():
        if init_first:
            assert device_locked.wait(5), "initializer did not lock Device"
        else:
            # 紧急释放先跑一小段，再让 init 上场
            pass
        with SessionLocal() as db:
            _set_timeouts(db)
            try:
                return release_terminal_job_lease(
                    db, device_id=did, lease_id=lid,
                    reason="lock-order interleaving", user_id=None, username="tester",
                )
            finally:
                db.rollback()

    errors = []
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="u3-a") as pool:
        if init_first:
            f0 = pool.submit(run_init)
            f1 = pool.submit(run_emergency)
        else:
            f1 = pool.submit(run_emergency)
            assert device_locked.wait(0.05) or True  # 不阻塞；稍错开
            f0 = pool.submit(run_init)
        for future in (f0, f1):
            try:
                future.result(timeout=12)
            except Exception as exc:
                orig = getattr(exc, "orig", exc)
                errors.append((type(exc).__name__, getattr(orig, "sqlstate", None), str(exc)))
    assert not errors, errors


# ── b. reconciler × acquire_lease ──────────────────────────────────────


@pytest.mark.asyncio(loop_scope="module")
async def test_reconciler_and_acquire_lease_lock_order(db_session):
    device, _job, _lease, host = _seed_running(db_session, expired=True)
    did = device.id

    async def run_reconciler():
        async with AsyncSessionLocal() as db:
            await db.execute(text("SET LOCAL deadlock_timeout = '200ms'"))
            await db.execute(text("SET LOCAL lock_timeout = '5s'"))
            try:
                return await _reconcile_expired_leases(db)
            finally:
                await db.rollback()

    async def run_acquire():
        await asyncio.sleep(0.05)  # 让 reconciler 先进入 Job 锁
        async with AsyncSessionLocal() as db:
            await db.execute(text("SET LOCAL deadlock_timeout = '200ms'"))
            await db.execute(text("SET LOCAL lock_timeout = '5s'"))
            try:
                return await acquire_lease(
                    db,
                    device_id=did,
                    host_id=host.id,
                    lease_type=LeaseType.MAINTENANCE,
                    agent_instance_id="acquire-probe",
                    reason="lock-order",
                    holder="tester",
                )
            finally:
                await db.rollback()

    results = await asyncio.gather(
        run_reconciler(), run_acquire(), return_exceptions=True,
    )
    assert not _collect_errors(results), _collect_errors(results)


# ── c. recovery × discharge ────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="module")
async def test_recovery_and_discharge_lock_order(db_session):
    device, job, lease, host = _seed_running(db_session)
    did = device.id

    async def run_recovery():
        async with AsyncSessionLocal() as db:
            await db.execute(text("SET LOCAL deadlock_timeout = '200ms'"))
            await db.execute(text("SET LOCAL lock_timeout = '5s'"))
            try:
                payload = _RecoverySyncIn(
                    host_id=host.id,
                    agent_instance_id=host.id,
                    boot_id=host.boot_id or "",
                    active_jobs=[_ActiveJobEntry(
                        job_id=job.id, device_id=did, fencing_token=lease.fencing_token,
                    )],
                )
                return await sync_agent_recovery(db, payload)
            finally:
                await db.rollback()

    def run_discharge():
        with SessionLocal() as db:
            _set_timeouts(db)
            try:
                return dr.apply_discharge_sync(
                    db,
                    device_id=did,
                    fencing_token=lease.fencing_token,
                    family_results={"power-cycle": {"result": "confirmed"}},
                )
            finally:
                db.rollback()

    loop = asyncio.get_running_loop()
    results = await asyncio.gather(
        run_recovery(),
        loop.run_in_executor(None, run_discharge),
        return_exceptions=True,
    )
    assert not _collect_errors(results), _collect_errors(results)


# ── d. completion UNKNOWN × init ───────────────────────────────────────


@pytest.mark.asyncio(loop_scope="module")
async def test_completion_unknown_and_init_lock_order(db_session):
    device, job, lease, _host = _seed_running(db_session, unknown=True)
    did = device.id

    async def run_complete():
        async with AsyncSessionLocal() as db:
            await db.execute(text("SET LOCAL deadlock_timeout = '200ms'"))
            await db.execute(text("SET LOCAL lock_timeout = '5s'"))
            try:
                payload = _RunCompleteIn(
                    update={"status": "FINISHED", "exit_code": 0},
                    fencing_token=lease.fencing_token,
                    disarm_results={"power-cycle": {"result": "confirmed"}},
                )
                return await complete_agent_job(db, job.id, payload)
            finally:
                await db.rollback()

    def run_init():
        with SessionLocal() as db:
            _set_timeouts(db)
            try:
                return dr.init_device_return_sync(db, device_id=did, dry_run=True)
            finally:
                db.rollback()

    loop = asyncio.get_running_loop()
    results = await asyncio.gather(
        run_complete(),
        loop.run_in_executor(None, run_init),
        return_exceptions=True,
    )
    assert not _collect_errors(results), _collect_errors(results)
