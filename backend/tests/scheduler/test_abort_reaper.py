"""Abort reaper integration tests — ADR-0021 v3 §P1.

Requires PostgreSQL (::timestamptz cast).  Skips on SQLite.

Pattern (mirrors test_device_lease_reconciler.py):
- Sync ``_seed`` helper builds Host/Device/Plan/PlanRun/Job + abort_requested
  context via ``SessionLocal``.
- Each test calls ``_reconcile_aborted_running_jobs`` through
  ``AsyncSessionLocal`` (the reconciler is an async coroutine).
- Sync ``_cleanup`` tears the rows down respecting FK ordering.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="abort reaper SQL uses PG-native JSONB ::timestamptz cast",
)

from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobArtifact, JobInstance, StepTrace
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.models.resource_pool import ResourceAllocation
from backend.scheduler.device_lease_reconciler import _reconcile_aborted_running_jobs


PIPELINE_DEF = {"lifecycle": {"init": [], "teardown": []}}


def _seed(
    host_id: str,
    device_id: int,
    *,
    job_statuses: list[str],
    abort_age_seconds: int,
) -> tuple[int, list[int]]:
    """Build full chain and return (plan_run_id, [job_ids])."""
    now = datetime.now(timezone.utc)
    abort_at = now - timedelta(seconds=abort_age_seconds)
    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{host_id}",
            status=HostStatus.ONLINE.value, created_at=now,
        )
        devices = [
            Device(
                id=device_id + index,
                serial=f"AR-{device_id}-{index}",
                host_id=host_id,
                status="BUSY",
                tags=[],
                created_at=now,
                adb_connected=True,
                adb_state="device",
            )
            for index in range(len(job_statuses))
        ]
        plan = Plan(
            name=f"ar-plan-{device_id}",
            description="abort reaper test",
            failure_threshold=0.1,
            created_by="pytest",
        )
        db.add_all([host, *devices, plan])
        db.flush()

        step = PlanStep(
            plan_id=plan.id, step_key="default",
            script_name="dummy", script_version="v1.0.0",
            stage="init", sort_order=0,
        )
        db.add(step)
        db.flush()

        run = PlanRun(
            plan_id=plan.id, status="RUNNING",
            failure_threshold=0.1, triggered_by="pytest",
            plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL", started_at=now,
            run_context={"abort_requested": {"at": abort_at.isoformat(), "reason": "test"}},
        )
        db.add(run)
        db.flush()

        job_ids: list[int] = []
        for device, status in zip(devices, job_statuses, strict=True):
            j = JobInstance(
                plan_run_id=run.id, plan_id=plan.id,
                device_id=device.id, host_id=host_id, status=status,
                pipeline_def=PIPELINE_DEF, created_at=now, updated_at=now,
                started_at=now if status == JobStatus.RUNNING.value else None,
            )
            db.add(j)
            db.flush()
            job_ids.append(j.id)

        db.commit()
        return run.id, job_ids
    finally:
        db.close()


def _add_active_lease(device_id: int, job_id: int, host_id: str) -> int:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        lease = DeviceLease(
            device_id=device_id, job_id=job_id, host_id=host_id,
            lease_type=LeaseType.JOB.value, status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{device_id}:1", lease_generation=1,
            agent_instance_id=host_id,
            acquired_at=now, renewed_at=now,
            expires_at=now + timedelta(seconds=600),
        )
        db.add(lease)
        db.flush()
        lid = lease.id
        db.commit()
        return lid
    finally:
        db.close()


def _cleanup(host_id: str, device_id: int) -> None:
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
        db.execute(Device.__table__.delete().where(Device.host_id == host_id))
        db.execute(Host.__table__.delete().where(Host.id == host_id))
        db.commit()
    finally:
        db.close()


def _new_ids() -> tuple[str, int]:
    suffix = uuid4().hex[:8]
    host_id = f"ar-host-{suffix}"
    device_id = int(suffix[:8], 16) % 10_000_000
    return host_id, device_id


# ══════════════════════════════════════════════════════════════════════════════
# Test cases
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_grace_not_expired_job_untouched():
    """grace 未到, job 不动"""
    host_id, device_id = _new_ids()
    _, job_ids = _seed(
        host_id, device_id,
        job_statuses=[JobStatus.RUNNING.value],
        abort_age_seconds=30,  # < 60s default grace
    )
    job_id = job_ids[0]

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as db:
            count, items = await _reconcile_aborted_running_jobs(db)
            await db.commit()

            assert count == 0
            assert items == []

            job = await db.get(JobInstance, job_id)
            assert job.status == JobStatus.RUNNING.value
            assert job.ended_at is None
    finally:
        _cleanup(host_id, device_id)


@pytest.mark.asyncio(loop_scope="module")
async def test_grace_expired_job_transitions_to_unknown():
    """abort ACK grace 已到时 RUNNING 转 UNKNOWN，且仍为非终态。"""
    host_id, device_id = _new_ids()
    _, job_ids = _seed(
        host_id, device_id,
        job_statuses=[JobStatus.RUNNING.value],
        abort_age_seconds=90,  # > 60s default grace
    )
    job_id = job_ids[0]

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as db:
            count, items = await _reconcile_aborted_running_jobs(db)
            await db.commit()

            assert count == 1
            assert len(items) == 1
            assert items[0]["type"] == "job_status"
            assert items[0]["job_id"] == job_id
            assert items[0]["status"] == "UNKNOWN"
            assert items[0]["plan_run_terminal"] is False

            job = await db.get(JobInstance, job_id)
            assert job.status == JobStatus.UNKNOWN.value
            assert job.ended_at is not None
            assert job.status_reason == "abort_ack_timeout"
    finally:
        _cleanup(host_id, device_id)


@pytest.mark.asyncio(loop_scope="module")
async def test_plan_run_stays_running_after_abort_ack_timeout():
    """UNKNOWN 非终态，abort ACK 超时不得终态化 PlanRun。"""
    host_id, device_id = _new_ids()
    plan_run_id, _ = _seed(
        host_id, device_id,
        job_statuses=[
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
            JobStatus.RUNNING.value,
        ],
        abort_age_seconds=90,
    )

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as db:
            count, _ = await _reconcile_aborted_running_jobs(db)
            await db.commit()

            assert count == 1
            run = await db.get(PlanRun, plan_run_id)
            assert run.status == "RUNNING"
            assert run.ended_at is None
            assert run.result_summary is None
    finally:
        _cleanup(host_id, device_id)


@pytest.mark.asyncio(loop_scope="module")
async def test_active_lease_retained_after_abort_ack_timeout():
    """RUNNING→UNKNOWN 后 ACTIVE lease 保留以隔离设备。"""
    host_id, device_id = _new_ids()
    _, job_ids = _seed(
        host_id, device_id,
        job_statuses=[JobStatus.RUNNING.value],
        abort_age_seconds=90,
    )
    job_id = job_ids[0]
    lease_id = _add_active_lease(device_id, job_id, host_id)

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as db:
            count, _ = await _reconcile_aborted_running_jobs(db)
            await db.commit()

            assert count == 1
            lease = await db.get(DeviceLease, lease_id)
            assert lease.status == LeaseStatus.ACTIVE.value
    finally:
        _cleanup(host_id, device_id)


# ══════════════════════════════════════════════════════════════════════════════
# R06-F04 / #989: lock-reread must refresh the identity-map snapshot
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_complete_terminal_committed_between_scan_and_lock_is_not_overwritten():
    """#989 验收：候选扫描（identity map 已加载 RUNNING）后并发 /complete
    提交 COMPLETED——锁复读必须刷新为终态并 skip，不得把终态写回 UNKNOWN。

    反例成立条件：同一 session 内普通 ``SELECT ... FOR UPDATE`` 命中
    identity map 已加载对象时**不刷新属性**，锁后仍看到缓存的 RUNNING →
    transition 会把已提交的 COMPLETED 覆盖回 UNKNOWN。
    """
    from backend.scheduler.device_lease_reconciler import _abort_reaper_recheck_job
    from backend.services.state_machine import JobStateMachine

    host_id, device_id = _new_ids()
    _, job_ids = _seed(
        host_id, device_id,
        job_statuses=[JobStatus.RUNNING.value],
        abort_age_seconds=90,
    )
    job_id = job_ids[0]

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as db:
            # 模拟 reconciler 候选扫描：行已进入本 session identity map
            candidate = await db.get(JobInstance, job_id)
            assert candidate.status == JobStatus.RUNNING.value

            # 并发 /complete 在另一连接提交终态
            sync_db = SessionLocal()
            try:
                j = sync_db.get(JobInstance, job_id)
                JobStateMachine.transition(j, JobStatus.COMPLETED, "complete_concurrent")
                j.ended_at = datetime.now(timezone.utc)
                sync_db.commit()
            finally:
                sync_db.close()

            changed, item = await _abort_reaper_recheck_job(
                db, job_id, datetime.now(timezone.utc),
            )
            await db.rollback()

            assert changed is False
            assert item is None

        # 终态未被覆盖
        verify_db = SessionLocal()
        try:
            j = verify_db.get(JobInstance, job_id)
            assert j.status == JobStatus.COMPLETED.value
            assert j.status_reason == "complete_concurrent"
            assert j.ended_at is not None
        finally:
            verify_db.close()
    finally:
        _cleanup(host_id, device_id)


@pytest.mark.asyncio(loop_scope="module")
async def test_recheck_recovers_still_running_job_with_cached_scan_object():
    """#989 对照：同一 identity map 预加载场景下无并发提交——锁复读刷新后
    仍是 RUNNING，正常转 UNKNOWN（populate_existing 不改变正常路径）。"""
    from backend.scheduler.device_lease_reconciler import _abort_reaper_recheck_job

    host_id, device_id = _new_ids()
    _, job_ids = _seed(
        host_id, device_id,
        job_statuses=[JobStatus.RUNNING.value],
        abort_age_seconds=90,
    )
    job_id = job_ids[0]

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as db:
            candidate = await db.get(JobInstance, job_id)
            assert candidate.status == JobStatus.RUNNING.value

            now = datetime.now(timezone.utc)
            changed, item = await _abort_reaper_recheck_job(db, job_id, now)
            await db.commit()

            assert changed is True
            assert item is not None
            assert item["type"] == "job_status"
            assert item["job_id"] == job_id
            assert item["status"] == "UNKNOWN"
            assert item["plan_run_terminal"] is False

            job = await db.get(JobInstance, job_id)
            assert job.status == JobStatus.UNKNOWN.value
            assert job.ended_at is not None
            assert job.status_reason == "abort_ack_timeout"
    finally:
        _cleanup(host_id, device_id)


# ══════════════════════════════════════════════════════════════════════════════
# #2050：候选面必须限定在 abort_requested.requested_job_ids 之内
#   host 级 abort（#1880）只请求该主机的 job，但写的是 run 级 abort_requested.at
#   —— 不过滤会把同 run 其他主机的正常 RUNNING job 一并打成 UNKNOWN。
# ══════════════════════════════════════════════════════════════════════════════


def _seed_two_hosts(
    hosts: list[tuple[str, int]],
    *,
    abort_age_seconds: int,
    requested_job_ids: list[int] | None = None,
) -> tuple[int, list[int], list[tuple[str, int]]]:
    """一个 PlanRun、每台主机一个 RUNNING job（各自设备）。

    ``requested_job_ids`` 为 ``None`` 时不写该键（模拟历史 run_context 形态）。
    返回 (plan_run_id, device_ids, [(host_id, job_id)])。
    """
    now = datetime.now(timezone.utc)
    abort_at = now - timedelta(seconds=abort_age_seconds)
    db = SessionLocal()
    try:
        plan = Plan(
            name=f"ar2-plan-{uuid4().hex[:8]}",
            description="abort reaper multi-host test",
            failure_threshold=0.1,
            created_by="pytest",
        )
        db.add(plan)
        db.flush()

        step = PlanStep(
            plan_id=plan.id, step_key="default",
            script_name="dummy", script_version="v1.0.0",
            stage="init", sort_order=0,
        )
        db.add(step)
        db.flush()

        abort_payload: dict = {"at": abort_at.isoformat(), "reason": "host_update"}
        if requested_job_ids is not None:
            abort_payload["requested_job_ids"] = list(requested_job_ids)

        run = PlanRun(
            plan_id=plan.id, status="RUNNING",
            failure_threshold=0.1, triggered_by="pytest",
            plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL", started_at=now,
            run_context={"abort_requested": abort_payload},
        )
        db.add(run)
        db.flush()

        device_ids: list[int] = []
        pairs: list[tuple[str, int]] = []
        for index, (host_id, device_id) in enumerate(hosts):
            host = Host(
                id=host_id, hostname=f"h-{host_id}",
                status=HostStatus.ONLINE.value, created_at=now,
            )
            device = Device(
                id=device_id, serial=f"AR2-{device_id}-{index}", host_id=host_id,
                status="BUSY", tags=[], created_at=now,
                adb_connected=True, adb_state="device",
            )
            db.add_all([host, device])
            db.flush()
            job = JobInstance(
                plan_run_id=run.id, plan_id=plan.id,
                device_id=device.id, host_id=host_id,
                status=JobStatus.RUNNING.value,
                pipeline_def=PIPELINE_DEF, created_at=now, updated_at=now,
                started_at=now,
            )
            db.add(job)
            db.flush()
            device_ids.append(device_id)
            pairs.append((host_id, job.id))

        db.commit()
        return run.id, device_ids, pairs
    finally:
        db.close()


def _set_requested_job_ids(plan_run_id: int, job_ids: list[int] | None) -> None:
    """把 requested_job_ids 写进既有 run 的 abort_requested（None = 保持不写）。"""
    if job_ids is None:
        return
    db = SessionLocal()
    try:
        run = db.get(PlanRun, plan_run_id)
        ctx = dict(run.run_context or {})
        ctx["abort_requested"] = {
            **(ctx.get("abort_requested") or {}),
            "requested_job_ids": list(job_ids),
        }
        run.run_context = ctx
        db.commit()
    finally:
        db.close()


def _cleanup_two_hosts(pairs: list[tuple[str, int]], device_ids: list[int]) -> None:
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
        db.execute(Device.__table__.delete().where(Device.id.in_(device_ids)))
        db.execute(Host.__table__.delete().where(Host.id.in_([h for h, _ in pairs])))
        db.commit()
    finally:
        db.close()


def _two_new_ids() -> tuple[str, int, str, int]:
    h1 = f"ar2-host-{uuid4().hex[:6]}"
    h2 = f"ar2-host-{uuid4().hex[:6]}"
    d1 = int(uuid4().hex[:8], 16) % 10_000_000
    return h1, d1, h2, d1 + 1


@pytest.mark.asyncio(loop_scope="module")
async def test_host_scoped_abort_spares_other_hosts_running_jobs():
    """#2050：host 级 abort 的 requested_job_ids 只含该主机 → 其他主机 job 不动。"""
    h1, d1, h2, d2 = _two_new_ids()
    pairs: list[tuple[str, int]] = []
    try:
        run_id, _, pairs = _seed_two_hosts([(h1, d1), (h2, d2)], abort_age_seconds=90)
        (_, job_h1), (_, job_h2) = pairs
        _set_requested_job_ids(run_id, [job_h1])

        await async_engine.dispose()
        async with AsyncSessionLocal() as db:
            count, items = await _reconcile_aborted_running_jobs(db)
            await db.commit()

            assert count == 1, f"只应回收被请求的 job，实际 {count}：{items}"
            assert [i["job_id"] for i in items] == [job_h1]

            assert (await db.get(JobInstance, job_h1)).status == JobStatus.UNKNOWN.value
            other = await db.get(JobInstance, job_h2)
            assert other.status == JobStatus.RUNNING.value, (
                "同 run 其他主机（未被请求中止）的 job 不得被打成 UNKNOWN"
            )
            assert other.ended_at is None
    finally:
        _cleanup_two_hosts(pairs, [d1, d2])


@pytest.mark.asyncio(loop_scope="module")
async def test_run_level_abort_reaps_every_requested_job():
    """run 级 abort：requested_job_ids 覆盖全部 RUNNING job → 全部回收（行为不变）。"""
    h1, d1, h2, d2 = _two_new_ids()
    pairs: list[tuple[str, int]] = []
    try:
        run_id, _, pairs = _seed_two_hosts([(h1, d1), (h2, d2)], abort_age_seconds=90)
        (_, job_h1), (_, job_h2) = pairs
        _set_requested_job_ids(run_id, [job_h1, job_h2])

        await async_engine.dispose()
        async with AsyncSessionLocal() as db:
            count, items = await _reconcile_aborted_running_jobs(db)
            await db.commit()

            assert count == 2
            assert sorted(i["job_id"] for i in items) == sorted([job_h1, job_h2])
    finally:
        _cleanup_two_hosts(pairs, [d1, d2])


@pytest.mark.asyncio(loop_scope="module")
async def test_legacy_abort_without_requested_ids_still_reaps():
    """兼容：run_context 无 requested_job_ids（历史形态）时退化为「只看 at」。"""
    h1, d1, h2, d2 = _two_new_ids()
    pairs: list[tuple[str, int]] = []
    try:
        _, _, pairs = _seed_two_hosts([(h1, d1), (h2, d2)], abort_age_seconds=90)
        await async_engine.dispose()
        async with AsyncSessionLocal() as db:
            count, items = await _reconcile_aborted_running_jobs(db)
            await db.commit()

            assert count == 2, "键缺失时不得改变既有回收行为（否则老数据无人回收）"
            assert len(items) == 2
    finally:
        _cleanup_two_hosts(pairs, [d1, d2])
