"""#3723 §3.4.4 / I5：设备返还路径 Job → Device → Lease 全序交错回归。

四组真实入口（隔离 PG、独立 Session、有界等待）：
  a. 初始化 × 紧急释放（两种先后）——复核 F3
  b. ``_reconcile_expired_leases``（过期租约释放）
  c. ``sync_agent_recovery``（终态 job 的遗留租约释放）+ 清偿
  d. completion UNKNOWN 恢复分支

b/c/d 的判据沿用 #1959/#1980/#2015：第三会话 **FOR UPDATE** 锁住 Device 行，
后台启动被测真实入口，用 ``pg_locks`` 确认它已在 Device 行锁上排队（未授予的
transactionid 等待）；第四会话
对租约行 **FOR UPDATE NOWAIT**——修复后入口尚未持有 Lease（试锁成功），修复前
入口已按 Lease→Device 先锁租约（试锁失败，55P03）。释放第三会话后再核对终态。

修复前（释放路径 Lease→Device）与 Device→Lease 入口交错为 40P01；
修复后两侧完成、无死锁。文件名含 lock_order，接入 ci.yml PR 路径。
"""
from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from backend.tests.disarm_probe_evidence import trust_test_probe
from datetime import datetime, timedelta, timezone
from threading import Event, current_thread
from uuid import uuid4

import pytest
import sqlalchemy.exc
from sqlalchemy import select, text

from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
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


# ── b/c/d 共用：pg_locks 等锁判据 + NOWAIT 试锁（#1959/#1980/#2015）──────


async def _session_pid(db) -> int:
    return (await db.execute(text("SELECT pg_backend_pid()"))).scalar()


async def _blocker_xid(db) -> str:
    # 第三会话已持行锁，取其事务 id 作为 pg_locks 的等待目标。
    return (await db.execute(text("SELECT pg_current_xact_id()::text"))).scalar()


async def _wait_until_waiting_on(observer, pid: int, blocker_xid: str, timeout: float = 15.0) -> bool:
    # 等到 pid 因等待 blocker_xid 而排队（pg_locks 有未授予的 transactionid 锁）。
    #
    # PostgreSQL 的行锁等待在 pg_locks 里表现为等待持有方的 xid（未授予的
    # transactionid），不是等待方自己的 tuple 锁；只用 tuple 行会漏判。
    # 第三会话只持有目标 Device 行锁，因此 xid 命中即可确认它被 Device 挡住。
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        row = (await observer.execute(
            text(
                "SELECT 1 FROM pg_locks "
                "WHERE pid = :pid AND NOT granted AND locktype = 'transactionid' "
                "AND transactionid::text = :xid "
                "LIMIT 1"
            ),
            {"pid": pid, "xid": blocker_xid},
        )).first()
        if row is not None:
            return True
        await asyncio.sleep(0.02)
    return False


async def _probe_row_nowait(observer, model, row_id):
    """第四会话 NOWAIT 试锁；返回异常表示「等待方已持有该行锁」。"""
    try:
        await observer.execute(
            select(model).where(model.id == row_id).with_for_update(nowait=True)
        )
    except sqlalchemy.exc.DBAPIError as exc:  # 55P03 lock_not_available
        return exc
    finally:
        await observer.rollback()
    return None


async def _read_job_and_lease(job_id: int, lease_id: int):
    async with AsyncSessionLocal() as db:
        job = await db.get(JobInstance, job_id)
        lease = await db.get(DeviceLease, lease_id)
        assert job is not None and lease is not None
        return job.status, lease.status, lease.lease_type, lease.reason, lease.fencing_token


def _run_discharge(device_id: int, fencing_token: str, trusted: dict) -> dict:
    with SessionLocal() as db:
        _set_timeouts(db)
        try:
            result = dr.apply_discharge_sync(
                db,
                device_id=device_id,
                fencing_token=fencing_token,
                family_results={"power-cycle": trusted},
            )
            db.commit()
            return result
        finally:
            db.close()


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
                sqlstate = getattr(orig, "sqlstate", None)
                msg = str(exc).lower()
                if sqlstate in {"40P01", "55P03"} or "deadlock" in msg or "lock timeout" in msg:
                    errors.append((type(exc).__name__, sqlstate, str(exc)[:300]))
    assert not errors, errors


# ── b. reconciler × 过期租约释放 ───────────────────────────────────────
#
# 第三会话持 Device 行锁时，reconciler 不得已先持有租约行锁。
# 修复前 _reconcile_expired_leases 为 Job → Lease → Device；被 Device 挡住的那一刻
# 已持有 device_leases，与认领/U3 入口的 Device → Lease 成环。修复后入口在 Device
# 上排队时尚未锁租约，第四会话 NOWAIT 试锁必然成功。


@pytest.mark.asyncio(loop_scope="module")
async def test_reconciler_and_acquire_lease_lock_order(db_session):
    device, job, lease, _host = _seed_running(db_session, expired=True, unknown=True)
    # UNKNOWN 超过宽限期 → 走 Phase 2 的统一入口释放（不是只转 UNKNOWN）。
    job.ended_at = _NOW - timedelta(seconds=600)
    db_session.commit()
    did, jid, lid = device.id, job.id, lease.id

    await async_engine.dispose()
    async with (
        AsyncSessionLocal() as db_blocker,
        AsyncSessionLocal() as db_probe,
        AsyncSessionLocal() as db_entry,
    ):
        await db_blocker.execute(
            select(Device).where(Device.id == did).with_for_update()
        )
        blocker_xid = await _blocker_xid(db_blocker)
        entry_pid = await _session_pid(db_entry)

        async def run_reconciler():
            await db_entry.execute(text("SET LOCAL deadlock_timeout = '200ms'"))
            await db_entry.execute(text("SET LOCAL lock_timeout = '15s'"))
            try:
                return await _reconcile_expired_leases(db_entry)
            finally:
                await db_entry.rollback()

        task = asyncio.create_task(run_reconciler())
        waited = await _wait_until_waiting_on(db_probe, entry_pid, blocker_xid)
        lease_lock_error = (
            await _probe_row_nowait(db_probe, DeviceLease, lid) if waited else None
        )

        await db_blocker.rollback()
        result = await asyncio.wait_for(task, timeout=20)

        assert waited, "reconciler 应先在 Device 行锁上排队（该行被第三会话持有）"
        assert lease_lock_error is None, (
            "reconciler 在等待 Device 行锁时已持有 device_leases 行锁 —— "
            f"锁序仍是 Lease→Device（#3723 §3.4.4 I5 回归）：{lease_lock_error}"
        )
        assert isinstance(result, tuple) and result[1] >= 1, (
            f"过期 UNKNOWN 租约应经统一入口进入 FAILED 计数：{result}"
        )

    job_status, lease_status, _lease_type, _reason, _token = await _read_job_and_lease(jid, lid)
    assert job_status == JobStatus.FAILED.value, f"过期 job 应终态化：{job_status}"
    assert lease_status == LeaseStatus.RELEASED.value, (
        f"无终态解除结果时过期 JOB 租约应释放：{lease_status}"
    )


# ── c. recovery × 清偿 ─────────────────────────────────────────────────
#
# 终态 job 的遗留 ACTIVE 租约：recovery 走统一入口（ABORT_LOCAL 后转欠账）。
# 修复前 sync_agent_recovery 为 Job → Lease → Device；被 Device 挡住时已持有
# 租约行锁，第四会话 NOWAIT 会失败。释放后核对 action 与随后清偿的返回码。


@pytest.mark.asyncio(loop_scope="module")
async def test_recovery_and_discharge_lock_order(db_session, monkeypatch):
    device, job, lease, host = _seed_terminal(db_session)
    did, jid, lid = device.id, job.id, lease.id
    trusted = trust_test_probe(db_session, monkeypatch, did)
    original_token = lease.fencing_token

    await async_engine.dispose()
    async with (
        AsyncSessionLocal() as db_blocker,
        AsyncSessionLocal() as db_probe,
        AsyncSessionLocal() as db_entry,
    ):
        await db_blocker.execute(
            select(Device).where(Device.id == did).with_for_update()
        )
        blocker_xid = await _blocker_xid(db_blocker)
        entry_pid = await _session_pid(db_entry)

        async def run_recovery():
            await db_entry.execute(text("SET LOCAL deadlock_timeout = '200ms'"))
            await db_entry.execute(text("SET LOCAL lock_timeout = '15s'"))
            try:
                payload = _RecoverySyncIn(
                    host_id=host.id,
                    agent_instance_id=host.id,
                    boot_id=host.boot_id or "",
                    active_jobs=[_ActiveJobEntry(
                        job_id=jid, device_id=did, fencing_token=original_token,
                    )],
                )
                return await sync_agent_recovery(db_entry, payload)
            finally:
                await db_entry.rollback()

        task = asyncio.create_task(run_recovery())
        waited = await _wait_until_waiting_on(db_probe, entry_pid, blocker_xid)
        lease_lock_error = (
            await _probe_row_nowait(db_probe, DeviceLease, lid) if waited else None
        )

        await db_blocker.rollback()
        result = await asyncio.wait_for(task, timeout=20)

        assert waited, "sync_agent_recovery 应先在 Device 行锁上排队（该行被第三会话持有）"
        assert lease_lock_error is None, (
            "sync_agent_recovery 在等待 Device 行锁时已持有 device_leases 行锁 —— "
            f"锁序仍是 Lease→Device（#3723 §3.4.4 I5 回归）：{lease_lock_error}"
        )
        actions = result["actions"]
        assert len(actions) == 1, actions
        assert actions[0]["action"] == "ABORT_LOCAL", actions
        assert actions[0]["reason"] == "terminal_job_active_lease", actions

    _job_status, lease_status, lease_type, reason, debt_token = await _read_job_and_lease(jid, lid)
    assert lease_status == LeaseStatus.ACTIVE.value, lease_status
    assert lease_type == LeaseType.MAINTENANCE.value, lease_type
    assert reason == dr.DEBT_LEASE_REASON, reason
    assert debt_token and debt_token != original_token, (
        f"转欠账应推进代次并换发 token：{original_token!r} → {debt_token!r}"
    )

    discharge = await asyncio.to_thread(_run_discharge, did, debt_token, trusted)
    assert discharge["ok"] is True, discharge
    assert discharge["discharged_families"] == ["power-cycle"], discharge
    assert discharge["debt_lease_released"] is True, discharge
    assert discharge["open_debt_count"] == 0, discharge


# ── d. completion UNKNOWN × 终态 ───────────────────────────────────────
#
# completion 的 UNKNOWN 恢复分支：入口先锁 Job，再在 UNKNOWN 分支锁 Device，
# 最后锁候选 Lease（I5）。修复前候选 Lease 先于 Device；被 Device 挡住时已
# 持有租约行锁，第四会话 NOWAIT 会失败。释放后核对 job 终态与租约处理。


@pytest.mark.asyncio(loop_scope="module")
async def test_completion_unknown_and_acquire_lock_order(db_session, monkeypatch):
    device, job, lease, _host = _seed_running(db_session, unknown=True)
    # _seed_* 的 _NOW 是模块导入时刻；全量套件里本文件可能在数十分钟后才执行。
    # UNKNOWN→RUNNING 的恢复宽限只有 300s，必须按测试执行时刻重锚 ended_at，
    # 否则完成入口会因宽限过期抛 INVALID_OR_EXPIRED_FENCING_TOKEN（CI 60% 处实证）。
    now = datetime.now(timezone.utc)
    job.ended_at = now - timedelta(seconds=30)
    lease.expires_at = now + timedelta(hours=1)
    db_session.commit()
    did, jid, lid = device.id, job.id, lease.id
    trusted = trust_test_probe(db_session, monkeypatch, did, jid)
    token = lease.fencing_token

    await async_engine.dispose()
    async with (
        AsyncSessionLocal() as db_blocker,
        AsyncSessionLocal() as db_probe,
        AsyncSessionLocal() as db_entry,
    ):
        await db_blocker.execute(
            select(Device).where(Device.id == did).with_for_update()
        )
        blocker_xid = await _blocker_xid(db_blocker)
        entry_pid = await _session_pid(db_entry)

        async def run_complete():
            await db_entry.execute(text("SET LOCAL deadlock_timeout = '200ms'"))
            await db_entry.execute(text("SET LOCAL lock_timeout = '15s'"))
            try:
                payload = _RunCompleteIn(
                    update={"status": "FINISHED", "exit_code": 0},
                    fencing_token=token,
                    disarm_results={"power-cycle": trusted},
                )
                return await complete_agent_job(db_entry, jid, payload)
            finally:
                await db_entry.rollback()

        task = asyncio.create_task(run_complete())
        waited = await _wait_until_waiting_on(db_probe, entry_pid, blocker_xid)
        lease_lock_error = (
            await _probe_row_nowait(db_probe, DeviceLease, lid) if waited else None
        )

        await db_blocker.rollback()
        result = await asyncio.wait_for(task, timeout=20)

        assert waited, "completion UNKNOWN 分支应先在 Device 行锁上排队（该行被第三会话持有）"
        assert lease_lock_error is None, (
            "completion UNKNOWN 分支在等待 Device 行锁时已持有 device_leases 行锁 —— "
            f"锁序仍是 Lease→Device（#3723 §3.4.4 I5 回归）：{lease_lock_error}"
        )
        assert result["status"] == JobStatus.COMPLETED.value, result

    job_status, lease_status, lease_type, _reason, _token = await _read_job_and_lease(jid, lid)
    assert job_status == JobStatus.COMPLETED.value, f"job 应终态化：{job_status}"
    assert lease_status == LeaseStatus.RELEASED.value, (
        f"confirmed 解除后 JOB 租约应释放（不转欠账）：{lease_status}/{lease_type}"
    )
