"""Reconciler unit/integration tests for ADR-0019 Phase 4a/4b.

Tests the 3 reconciler check functions independently.
Each test seeds data via sync SessionLocal, then calls reconciler
functions with AsyncSessionLocal.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(
    os.getenv("DATABASE_URL", "").startswith("sqlite"),
    reason="Reconciler tests require PostgreSQL (device_leases partial unique index)",
)

from sqlalchemy import select

from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.services.plan_run_aggregation import (
    _TERMINAL_PLAN_RUN_STATUSES,
)
from backend.scheduler.device_lease_reconciler import (
    _abort_at_expired,
    _record_unknown_backlog,
    _reconcile_expired_leases,
    _reconcile_stale_unknown_jobs,
    _reconcile_terminal_job_active_leases,
)

PIPELINE_DEF = {
    "stages": {
        "prepare": [],
        "execute": [{"step_id": "dummy", "action": "builtin:noop", "timeout_seconds": 1}],
        "post_process": [],
    }
}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _seed(host_id: str, device_id: int, job_id: int, status: str) -> None:
    """Create minimal host + device + job via Plan / PlanRun."""
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        host = Host(id=host_id, hostname=f"h-{host_id}", status=HostStatus.ONLINE.value, created_at=now)
        device = Device(id=device_id, serial=f"DW-{device_id}", host_id=host_id,
                        status="ONLINE", tags=[], created_at=now,
                        adb_connected=True, adb_state="device")
        plan = Plan(name=f"wf-{device_id}", description="reconciler test",
                    failure_threshold=0.1,
                    created_by="pytest")
        db.add_all([host, device, plan])
        db.flush()

        step = PlanStep(plan_id=plan.id, step_key="default",
                        script_name="dummy", script_version="v1.0.0",
                        stage="init", sort_order=0)
        db.add(step)
        db.flush()

        run = PlanRun(plan_id=plan.id, status="RUNNING",
                      failure_threshold=0.1, triggered_by="pytest",
                      plan_snapshot={"name": plan.name, "plan_id": plan.id},
                      run_type="MANUAL", started_at=now)
        db.add(run)
        db.flush()

        job = JobInstance(id=job_id, plan_run_id=run.id, plan_id=plan.id,
                          device_id=device_id, host_id=host_id, status=status,
                          pipeline_def=PIPELINE_DEF, created_at=now, updated_at=now,
                          started_at=now if status == JobStatus.RUNNING.value else None)
        db.add(job)
        db.commit()
    finally:
        db.close()


def _add_expired_lease(device_id: int, job_id: int | None, host_id: str,
                       status: str = "ACTIVE", agent_instance_id: str = "") -> int:
    """Insert an expired ACTIVE lease via sync session. Returns lease id."""
    past = datetime.now(timezone.utc) - timedelta(seconds=3600)
    db = SessionLocal()
    try:
        lease = DeviceLease(
            device_id=device_id, job_id=job_id, host_id=host_id,
            lease_type=LeaseType.JOB.value, status=status,
            fencing_token=f"{device_id}:1", lease_generation=1,
            agent_instance_id=agent_instance_id or host_id,
            acquired_at=past - timedelta(seconds=7200),
            renewed_at=past, expires_at=past,
        )
        db.add(lease)
        db.flush()
        lid = lease.id
        # Phase 6d: device_leases is the sole source of truth.  No projection
        # writes to device.lock_run_id / lock_expires_at — those columns are
        # decommissioned.
        db.commit()
        return lid
    finally:
        db.close()


def _cleanup(host_id: str, device_id: int) -> None:
    """Remove seeded data (order respects FK constraints)."""
    from backend.models.job import JobArtifact, StepTrace
    from backend.models.resource_pool import ResourceAllocation
    db = SessionLocal()
    try:
        db.execute(StepTrace.__table__.delete())
        db.execute(JobArtifact.__table__.delete())
        db.execute(DeviceLease.__table__.delete())
        db.execute(ResourceAllocation.__table__.delete())
        db.execute(JobInstance.__table__.delete())
        db.execute(PlanStep.__table__.delete())
        db.execute(PlanRun.__table__.delete())
        db.execute(Plan.__table__.delete())
        db.execute(Device.__table__.delete().where(Device.id == device_id))
        db.execute(Host.__table__.delete().where(Host.id == host_id))
        db.commit()
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# Reconciler Phase 1: expired ACTIVE + RUNNING → UNKNOWN
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_reconciler_expired_lease_running_to_unknown():
    """Reconciler Phase 1: expired ACTIVE lease + RUNNING job → UNKNOWN,
    lease stays ACTIVE, job.ended_at is set."""
    suffix = uuid4().hex[:8]
    host_id = f"rc-host-a-{suffix}"
    device_id = int(suffix[:8], 16) % 10_000_000
    job_id = device_id + 1

    _seed(host_id, device_id, job_id, JobStatus.RUNNING.value)
    _add_expired_lease(device_id, job_id, host_id)

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as db:
            unknown, failed, terminal = await _reconcile_expired_leases(db)
            await db.commit()

            assert unknown == 1, f"Expected 1 UNKNOWN, got {unknown}"
            assert failed == 0
            assert terminal == 0

            # Verify job status
            job = await db.get(JobInstance, job_id)
            assert job.status == JobStatus.UNKNOWN.value
            assert job.ended_at is not None, "ended_at must be set"

            # Verify lease still ACTIVE
            lease = (await db.execute(
                select(DeviceLease).where(
                    DeviceLease.device_id == device_id,
                    DeviceLease.job_id == job_id,
                )
            )).scalars().first()
            assert lease is not None
            assert lease.status == LeaseStatus.ACTIVE.value, (
                f"Lease must stay ACTIVE during grace period; got {lease.status}"
            )
    finally:
        _cleanup(host_id, device_id)


# ══════════════════════════════════════════════════════════════════════════════
# Reconciler Phase 2: UNKNOWN + grace expired → release + FAILED
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_reconciler_unknown_grace_releases_and_fails():
    """Reconciler Phase 2: UNKNOWN job past grace → lease RELEASED + job FAILED."""
    suffix = uuid4().hex[:8]
    host_id = f"rc-host-b-{suffix}"
    device_id = int(suffix[:8], 16) % 10_000_000
    job_id = device_id + 1

    _seed(host_id, device_id, job_id, JobStatus.UNKNOWN.value)
    # Set ended_at far enough back to be past grace (300s)
    db = SessionLocal()
    try:
        job = db.get(JobInstance, job_id)
        job.ended_at = datetime.now(timezone.utc) - timedelta(seconds=600)
        db.commit()
    finally:
        db.close()
    _add_expired_lease(device_id, job_id, host_id, status="ACTIVE")

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as db:
            unknown, failed, terminal = await _reconcile_expired_leases(db)
            await db.commit()

            assert unknown == 0
            assert failed == 1, f"Expected 1 FAILED, got failed={failed} unknown={unknown} terminal={terminal}"
            assert terminal == 0

            job = await db.get(JobInstance, job_id)
            assert job.status == JobStatus.FAILED.value

            # Lease must be RELEASED
            lease = (await db.execute(
                select(DeviceLease).where(
                    DeviceLease.device_id == device_id,
                    DeviceLease.job_id == job_id,
                )
            )).scalars().first()
            assert lease is not None
            assert lease.status == LeaseStatus.RELEASED.value
    finally:
        _cleanup(host_id, device_id)


@pytest.mark.asyncio(loop_scope="module")
async def test_reconciler_unknown_grace_clears_execution_state():
    """#116: UNKNOWN→FAILED 终态转换必须清 execution_state —— 残留的
    WAITING_BARRIER 会污染不按 status 过滤的并发统计。"""
    suffix = uuid4().hex[:8]
    host_id = f"rc-host-c-{suffix}"
    device_id = int(suffix[:8], 16) % 10_000_000
    job_id = device_id + 1

    _seed(host_id, device_id, job_id, JobStatus.UNKNOWN.value)
    db = SessionLocal()
    try:
        job = db.get(JobInstance, job_id)
        job.ended_at = datetime.now(timezone.utc) - timedelta(seconds=600)
        job.execution_state = "WAITING_BARRIER"
        db.commit()
    finally:
        db.close()
    _add_expired_lease(device_id, job_id, host_id, status="ACTIVE")

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as db:
            unknown, failed, terminal = await _reconcile_expired_leases(db)
            await db.commit()

            assert failed == 1
            job = await db.get(JobInstance, job_id)
            assert job.status == JobStatus.FAILED.value
            assert job.execution_state is None
    finally:
        _cleanup(host_id, device_id)


# ══════════════════════════════════════════════════════════════════════════════
# Reconciler D5: terminal job with lingering ACTIVE lease
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_reconciler_terminal_job_active_lease_released():
    """D5: terminal job (FAILED) with ACTIVE lease → lease released, job status unchanged."""
    suffix = uuid4().hex[:8]
    host_id = f"rc-host-c-{suffix}"
    device_id = int(suffix[:8], 16) % 10_000_000
    job_id = device_id + 1

    _seed(host_id, device_id, job_id, JobStatus.FAILED.value)
    _add_expired_lease(device_id, job_id, host_id, status="ACTIVE")

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as db:
            released = await _reconcile_terminal_job_active_leases(db)
            await db.commit()

            assert released == 1, f"Expected 1 terminal lease released; got {released}"

            # Job status must NOT change
            job = await db.get(JobInstance, job_id)
            assert job.status == JobStatus.FAILED.value

            # Lease must be RELEASED
            lease = (await db.execute(
                select(DeviceLease).where(
                    DeviceLease.device_id == device_id,
                    DeviceLease.job_id == job_id,
                )
            )).scalars().first()
            assert lease.status == LeaseStatus.RELEASED.value
    finally:
        _cleanup(host_id, device_id)


# ══════════════════════════════════════════════════════════════════════════════
# Reconciler: stale UNKNOWN jobs (lease already gone)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_reconciler_stale_unknown_job_finalized():
    """_reconcile_stale_unknown_jobs: UNKNOWN past grace without lease → FAILED."""
    suffix = uuid4().hex[:8]
    host_id = f"rc-host-d-{suffix}"
    device_id = int(suffix[:8], 16) % 10_000_000
    job_id = device_id + 1

    _seed(host_id, device_id, job_id, JobStatus.UNKNOWN.value)
    # Set ended_at past grace
    db = SessionLocal()
    try:
        job = db.get(JobInstance, job_id)
        job.ended_at = datetime.now(timezone.utc) - timedelta(seconds=600)
        # Set device without a lock (no ACTIVE lease)
        dev = db.get(Device, device_id)
        dev.status = "ONLINE"
        db.commit()
    finally:
        db.close()

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as db:
            failed = await _reconcile_stale_unknown_jobs(db)
            await db.commit()

            assert failed == 1, f"Expected 1 stale UNKNOWN finalized; got {failed}"

            job = await db.get(JobInstance, job_id)
            assert job.status == JobStatus.FAILED.value
    finally:
        _cleanup(host_id, device_id)


# ══════════════════════════════════════════════════════════════════════════════
# Reconciler: idempotent — expired lease on UNKNOWN still within grace
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_reconciler_unknown_within_grace_noop():
    """UNKNOWN job within grace period → Reconciler skips (no action)."""
    suffix = uuid4().hex[:8]
    host_id = f"rc-host-e-{suffix}"
    device_id = int(suffix[:8], 16) % 10_000_000
    job_id = device_id + 1

    _seed(host_id, device_id, job_id, JobStatus.UNKNOWN.value)
    # set ended_at = recently (within grace)
    db = SessionLocal()
    try:
        job = db.get(JobInstance, job_id)
        job.ended_at = datetime.now(timezone.utc) - timedelta(seconds=60)
        db.commit()
    finally:
        db.close()
    _add_expired_lease(device_id, job_id, host_id, status="ACTIVE")

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as db:
            unknown, failed, terminal = await _reconcile_expired_leases(db)
            await db.commit()

            assert unknown == 0, f"Should skip UNKNOWN within grace; got unknown={unknown}"
            assert failed == 0
            assert terminal == 0

            # Verify nothing changed
            job = await db.get(JobInstance, job_id)
            assert job.status == JobStatus.UNKNOWN.value

            lease = (await db.execute(
                select(DeviceLease).where(
                    DeviceLease.device_id == device_id,
                    DeviceLease.job_id == job_id,
                )
            )).scalars().first()
            assert lease.status == LeaseStatus.ACTIVE.value
    finally:
        _cleanup(host_id, device_id)


# ══════════════════════════════════════════════════════════════════════════════
# Reconciler: orphan lease (job deleted) → release
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_reconciler_orphan_lease_released():
    """Expired ACTIVE lease with no corresponding job → released by Reconciler."""
    suffix = uuid4().hex[:8]
    host_id = f"rc-host-f-{suffix}"
    device_id = int(suffix[:8], 16) % 10_000_000

    # Create host + device only, no job
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        host = Host(id=host_id, hostname=f"h-{host_id}", status=HostStatus.ONLINE.value, created_at=now)
        device = Device(id=device_id, serial=f"DW-{device_id}", host_id=host_id,
                        status="ONLINE", tags=[], created_at=now,
                        adb_connected=True, adb_state="device")
        db.add_all([host, device])
        db.commit()
    finally:
        db.close()

    # Create lease with job_id=None (no associated job)
    _add_expired_lease(device_id, None, host_id, status="ACTIVE")

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as db:
            unknown, failed, terminal = await _reconcile_expired_leases(db)
            await db.commit()

            # Should be counted as terminal_released (orphan cleanup)
            assert terminal == 1, f"Expected 1 orphan lease released; got terminal={terminal}"
            assert unknown == 0
            assert failed == 0

            lease = (await db.execute(
                select(DeviceLease).where(
                    DeviceLease.device_id == device_id,
                    DeviceLease.job_id.is_(None),
                )
            )).scalars().first()
            assert lease.status == LeaseStatus.RELEASED.value
    finally:
        _cleanup(host_id, device_id)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 4c: Reconciler finalizes recycler-originated UNKNOWN
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_reconciler_finalizes_recycler_unknown_after_grace():
    """Phase 4c: Recycler RUNNING→UNKNOWN → Reconciler finalizes after grace.

    Simulates: recycler sets RUNNING→UNKNOWN with ended_at past grace,
    Reconciler finds it → release lease + UNKNOWN→FAILED.
    """
    suffix = uuid4().hex[:8]
    host_id = f"rc-host-g-{suffix}"
    device_id = int(suffix[:8], 16) % 10_000_000
    job_id = device_id + 1

    _seed(host_id, device_id, job_id, JobStatus.UNKNOWN.value)
    # Set ended_at past grace (300s)
    db = SessionLocal()
    try:
        job = db.get(JobInstance, job_id)
        job.ended_at = datetime.now(timezone.utc) - timedelta(seconds=600)
        db.commit()
    finally:
        db.close()
    _add_expired_lease(device_id, job_id, host_id, status="ACTIVE")

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as db:
            unknown, failed, terminal = await _reconcile_expired_leases(db)
            await db.commit()

            # Phase 1 (expired→UNKNOWN): job already UNKNOWN, grace expired
            # → Phase 2: release + FAILED
            assert unknown == 0
            assert failed == 1, (
                f"Recycler-originated UNKNOWN should be finalized; "
                f"got unknown={unknown} failed={failed} terminal={terminal}"
            )
            assert terminal == 0

            job = await db.get(JobInstance, job_id)
            assert job.status == JobStatus.FAILED.value

            lease = (await db.execute(
                select(DeviceLease).where(
                    DeviceLease.device_id == device_id,
                    DeviceLease.job_id == job_id,
                )
            )).scalars().first()
            assert lease.status == LeaseStatus.RELEASED.value
    finally:
        _cleanup(host_id, device_id)


# ── #782：abort reaper 的 cast 失败 fallback 不再用 ISO 文本比较 ──────────────

_ABORT_DEADLINE = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)


class TestAbortAtFallbackComparison:
    """cast 失败后的 fallback 判据（Python 侧解析后比较）。"""

    @pytest.mark.parametrize(
        ("raw", "expired"),
        [
            ("2026-09-12T11:59:59Z", True),
            ("2026-09-12T11:59:59+00:00", True),
            ("2026-09-12T19:59:59+08:00", True),  # 同一时刻的 +08:00 写法
            ("2026-09-12T11:59:59.500000+00:00", True),
            ("2026-09-12T12:00:00Z", False),  # 恰好等于 deadline：不算早于
            ("2026-09-12T12:00:00.500000+00:00", False),
            ("2026-09-12T04:00:01-08:00", False),  # 同一时刻的 -08:00 写法
        ],
    )
    def test_parses_each_iso_form(self, raw, expired):
        assert _abort_at_expired(raw, _ABORT_DEADLINE) is expired

    def test_non_utc_offset_where_text_compare_was_wrong(self):
        """钉住回归：非零偏移下文本比较与真实时间结论相反（旧 fallback 会漏回收）。"""
        raw = "2026-09-12T19:59:59+08:00"  # 真实时刻 11:59:59Z，已过期
        assert (raw < _ABORT_DEADLINE.isoformat()) is False  # 旧口径判为「不早」
        assert _abort_at_expired(raw, _ABORT_DEADLINE) is True

    def test_naive_value_treated_as_utc(self):
        assert _abort_at_expired("2026-09-12T11:59:59", _ABORT_DEADLINE) is True

    @pytest.mark.parametrize("raw", [None, "", "not-a-time", 1736678400, {"at": "x"}])
    def test_unparseable_value_never_triggers_reclaim(self, raw):
        """坏值按「不满足回收条件」处理：宁可漏回收，也不误杀在跑作业。"""
        assert _abort_at_expired(raw, _ABORT_DEADLINE) is False


# ══════════════════════════════════════════════════════════════════════════════
# #2531: Phase 2 / stale 的收口速率——一轮多解、一候选一事务
# ══════════════════════════════════════════════════════════════════════════════


def _seed_unknown_past_grace(n: int) -> list[dict]:
    """N 套 host + device + UNKNOWN(ended_at 已过宽限) + 过期 ACTIVE 租约。

    返回按 ``job_id`` 升序的 seed 记录——候选处理顺序本身就是 #2531 的断言对象
    （跨候选必须与 ``extend_leases_batch`` 同一条 job_id 升序全序）。
    """
    seeds: list[dict] = []
    for _ in range(n):
        suffix = uuid4().hex[:8]
        host_id = f"rc-drain-{suffix}"
        device_id = int(suffix[:8], 16) % 10_000_000
        job_id = device_id + 1
        _seed(host_id, device_id, job_id, JobStatus.UNKNOWN.value)
        db = SessionLocal()
        try:
            job = db.get(JobInstance, job_id)
            job.ended_at = datetime.now(timezone.utc) - timedelta(seconds=600)
            db.commit()
        finally:
            db.close()
        lease_id = _add_expired_lease(device_id, job_id, host_id, status="ACTIVE")
        seeds.append({
            "host_id": host_id, "device_id": device_id,
            "job_id": job_id, "lease_id": lease_id,
        })
    return sorted(seeds, key=lambda s: s["job_id"])


@pytest.mark.asyncio(loop_scope="module")
async def test_reconciler_phase2_drains_batch_in_one_pass():
    """#2531 主断言：N 条过宽限候选在**一个 tick** 内全部解锁 + 判 FAILED。

    修前 Phase 2 分支处理一条就 ``break``，返回恒为 1、其余候选要等
    ``reconciler_interval_seconds``（实测 12s/台 ≈ 4 台/分钟；按 60×17≈1000 台
    容量口径外推 ≈3.3 小时）。这里 3 条候选一 tick 收口，且每条的租约真的
    RELEASED——不是只把 job 判死、设备仍被 ACTIVE 租约占住。
    """
    seeds = _seed_unknown_past_grace(3)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as db:
            unknown, failed, terminal = await _reconcile_expired_leases(db)
            await db.commit()

            assert failed == 3, f"#2531 一轮多解失效：failed={failed}"
            assert unknown == 0 and terminal == 0

            for seed in seeds:
                job = await db.get(JobInstance, seed["job_id"])
                assert job.status == JobStatus.FAILED.value
                lease = (await db.execute(
                    select(DeviceLease).where(DeviceLease.id == seed["lease_id"])
                )).scalars().first()
                assert lease.status == LeaseStatus.RELEASED.value
                # 父 Run 必须一并聚合（一候选一事务没把终态化漏给尾部）
                run = await db.get(PlanRun, job.plan_run_id)
                assert run.status in _TERMINAL_PLAN_RUN_STATUSES, run.status
    finally:
        for seed in seeds:
            _cleanup(seed["host_id"], seed["device_id"])


@pytest.mark.asyncio(loop_scope="module")
async def test_reconciler_phase2_respects_drain_batch_cap(caplog, scheduler_env):
    """#2531 的**速率律**：收口 N 台需要 ``ceil(N/cap)`` 个 tick，不是 ``N`` 个。

    issue 的判据写成墙钟（``≤ 2×宽限 + N/cap×周期 + 容差``）。墙钟钉子跑不进 CI
    （要 dev 隔离栈 + 假 Agent + 300s 宽限），但它的不变量部分是纯计数关系：
    ``2×宽限`` 与 ``×周期`` 都不受本改动影响，唯一变的就是 **tick 数**——
    这里直接钉住它：``cap=2`` 时 5 台按 2/2/1 收口（3 个 tick），修前是 5 个 tick。

    同时钉住截断读数：上限本身是限速器，没有 ``reconciler_drain_truncated``
    它就等于把 O(N) 藏进一个没人看的数字里（#2365 同款「看不见就没法运维」）。
    """
    scheduler_env("RECONCILER_DRAIN_BATCH", "2")
    seeds = _seed_unknown_past_grace(5)
    try:
        await async_engine.dispose()
        drained_per_tick: list[int] = []
        truncated_remaining: list[str] = []
        for _tick in range(len(seeds)):  # 上限之上再留余量，靠 break 收敛
            with caplog.at_level(
                logging.WARNING,
                logger="backend.scheduler.device_lease_reconciler",
            ):
                caplog.clear()
                async with AsyncSessionLocal() as db:
                    _unknown, failed, _terminal = await _reconcile_expired_leases(db)
                    await db.commit()
            drained_per_tick.append(failed)
            truncated_remaining.extend(
                r.getMessage().split("remaining=")[1].split()[0]
                for r in caplog.records
                if "reconciler_drain_truncated" in r.getMessage()
            )
            if failed == 0:
                break

        assert drained_per_tick == [2, 2, 1, 0], (
            f"收口速率不是 ceil(N/cap) 个 tick：{drained_per_tick}"
        )
        assert truncated_remaining == ["3", "1"], (
            f"截断读数应逐轮如实反映余量：{truncated_remaining}"
        )
        assert len([d for d in drained_per_tick if d]) == (
            -(-len(seeds) // 2)
        ), "tick 数必须等于 ceil(N/cap)"

        async with AsyncSessionLocal() as db:
            for seed in seeds:
                job = await db.get(JobInstance, seed["job_id"])
                assert job.status == JobStatus.FAILED.value, (
                    f"上限不得把候选饿死：job={seed['job_id']} {job.status}"
                )
                lease = (await db.execute(
                    select(DeviceLease).where(DeviceLease.id == seed["lease_id"])
                )).scalars().first()
                assert lease.status == LeaseStatus.RELEASED.value
    finally:
        for seed in seeds:
            _cleanup(seed["host_id"], seed["device_id"])


@pytest.mark.asyncio(loop_scope="module")
async def test_reconciler_phase2_commits_each_candidate_independently():
    """一候选一**事务**边界：后序候选被行锁堵住时，前序候选必须已经落库。

    这条钉的是「批量」的实现形状，而不是「循环次数」：如果只在尾部批量终态化
    （#1172 原形状的直白放大），前序候选的 FAILED/RELEASED 会连同被堵住的第三条
    一起留在同一个未提交事务里——观测会话读不到，且回滚时一起丢掉。

    构造：另一会话先占住**最后一条**候选（job_id 最大）的租约行，回收器必然先做完
    前两条才在它上面排队。
    """
    seeds = _seed_unknown_past_grace(3)
    blocker_held = seeds[-1]
    try:
        await async_engine.dispose()
        async with (
            AsyncSessionLocal() as db_blocker,
            AsyncSessionLocal() as db_observer,
            AsyncSessionLocal() as db_rec,
        ):
            await db_blocker.execute(
                select(DeviceLease).where(
                    DeviceLease.id == blocker_held["lease_id"]
                ).with_for_update()
            )

            pid = (await db_rec.execute(text("SELECT pg_backend_pid()"))).scalar()
            task = asyncio.create_task(_reconcile_expired_leases(db_rec))

            blocked = False
            for _ in range(300):
                row = (await db_observer.execute(
                    text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                    {"pid": pid},
                )).first()
                if row is not None and row[0] == "Lock":
                    blocked = True
                    break
                await asyncio.sleep(0.05)
            assert blocked, "回收器应在最后一条候选的租约行锁上排队"

            # 关键：仍在排队时，前两条已经**提交**（读得到终态与 RELEASED）。
            for seed in seeds[:-1]:
                row = (await db_observer.execute(
                    text("SELECT status FROM job_instance WHERE id = :jid"),
                    {"jid": seed["job_id"]},
                )).first()
                assert row is not None and row[0] == JobStatus.FAILED.value, (
                    f"#2531 前序候选未独立提交：job={seed['job_id']} status={row}"
                )
                lease_state = (await db_observer.execute(
                    text("SELECT status FROM device_leases WHERE id = :lid"),
                    {"lid": seed["lease_id"]},
                )).first()
                assert lease_state[0] == LeaseStatus.RELEASED.value

            await db_blocker.rollback()
            failed = (await task)[1]
            assert failed == 3
    finally:
        for seed in seeds:
            _cleanup(seed["host_id"], seed["device_id"])


@pytest.mark.asyncio(loop_scope="module")
async def test_reconciler_stale_unknown_drains_batch():
    """Check 2（stale UNKNOWN，租约已不在）与 Phase 2 同形：一 tick 收多条。"""
    seeds = []
    for _ in range(3):
        suffix = uuid4().hex[:8]
        host_id = f"rc-stale-{suffix}"
        device_id = int(suffix[:8], 16) % 10_000_000
        job_id = device_id + 1
        _seed(host_id, device_id, job_id, JobStatus.UNKNOWN.value)
        db = SessionLocal()
        try:
            job = db.get(JobInstance, job_id)
            job.ended_at = datetime.now(timezone.utc) - timedelta(seconds=600)
            db.commit()
        finally:
            db.close()
        seeds.append({"host_id": host_id, "device_id": device_id, "job_id": job_id})
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as db:
            failed = await _reconcile_stale_unknown_jobs(db)
            await db.commit()
            assert failed == 3, f"#2531 stale 分支一轮多解失效：failed={failed}"
            for seed in seeds:
                job = await db.get(JobInstance, seed["job_id"])
                assert job.status == JobStatus.FAILED.value
    finally:
        for seed in seeds:
            _cleanup(seed["host_id"], seed["device_id"])


@pytest.mark.asyncio(loop_scope="module")
async def test_record_unknown_backlog_buckets_and_writes_gauge(monkeypatch):
    """#2531 观测面：三桶计数正确，且**真的写进 gauge**。

    ``missing_ended_at`` 单独成桶不是凑数：两条回收路径的判据都要 ``ended_at``，
    该桶的行永远不会自己变好，混进 ``within_grace`` 就把「时钟写坏了的死行」
    报成「还在正常宽限期」。
    """
    written: list[tuple[str, int]] = []

    class _FakeGauge:
        def labels(self, **kwargs):
            state = kwargs["state"]

            class _Handle:
                def set(self, value):
                    written.append((state, int(value)))

            return _Handle()

    import backend.scheduler.device_lease_reconciler as rec_mod
    monkeypatch.setattr(rec_mod, "reconciler_unknown_backlog", _FakeGauge())

    seeds = _seed_unknown_past_grace(2)
    # 一桶宽限内、一桶 ended_at 缺失
    suffix = uuid4().hex[:8]
    host_wg = f"rc-wg-{suffix}"
    device_wg = int(suffix[:8], 16) % 10_000_000
    job_wg = device_wg + 1
    _seed(host_wg, device_wg, job_wg, JobStatus.UNKNOWN.value)
    db = SessionLocal()
    try:
        db.get(JobInstance, job_wg).ended_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()
    suffix = uuid4().hex[:8]
    host_ne = f"rc-ne-{suffix}"
    device_ne = int(suffix[:8], 16) % 10_000_000
    job_ne = device_ne + 1
    _seed(host_ne, device_ne, job_ne, JobStatus.UNKNOWN.value)

    extra = [{"host_id": host_wg, "device_id": device_wg},
             {"host_id": host_ne, "device_id": device_ne}]
    try:
        await async_engine.dispose()
        counts = await _record_unknown_backlog()
        assert counts["grace_expired"] >= 2, counts
        assert counts["within_grace"] >= 1, counts
        assert counts["missing_ended_at"] >= 1, counts
        assert {s for s, _ in written} == {
            "grace_expired", "within_grace", "missing_ended_at",
        }, f"gauge 未被完整写入：{written}"
    finally:
        for seed in seeds:
            _cleanup(seed["host_id"], seed["device_id"])
        for seed in extra:
            _cleanup(seed["host_id"], seed["device_id"])
