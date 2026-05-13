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
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

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
        device = Device(
            id=device_id, serial=f"AR-{device_id}", host_id=host_id,
            status="BUSY", tags=[], created_at=now,
            adb_connected=True, adb_state="device",
        )
        plan = Plan(
            name=f"ar-plan-{device_id}",
            description="abort reaper test",
            failure_threshold=0.1,
            created_by="pytest",
        )
        db.add_all([host, device, plan])
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
        for status in job_statuses:
            j = JobInstance(
                plan_run_id=run.id, plan_id=plan.id,
                device_id=device_id, host_id=host_id, status=status,
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
        db.execute(Device.__table__.delete().where(Device.id == device_id))
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
async def test_grace_expired_job_transitions_to_aborted():
    """grace 已到, job 转 ABORTED"""
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
            assert items[0]["status"] == "ABORTED"

            job = await db.get(JobInstance, job_id)
            assert job.status == JobStatus.ABORTED.value
            assert job.ended_at is not None
            assert job.status_reason == "aborted_reaper_timeout"
    finally:
        _cleanup(host_id, device_id)


@pytest.mark.asyncio(loop_scope="module")
async def test_plan_run_failed_on_abort_v3_override():
    """PlanRun 转 FAILED (v3 §P4: abort 覆盖 threshold)"""
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
            assert run.status == "FAILED"
            assert run.result_summary is not None
            assert run.result_summary["total"] == 3
            assert run.result_summary["aborted"] == 1
            assert run.result_summary["failed_only"] == 1
    finally:
        _cleanup(host_id, device_id)


@pytest.mark.asyncio(loop_scope="module")
async def test_lingering_active_lease_released_on_abort():
    """残留 ACTIVE lease 被防御性释放"""
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
            assert lease.status == LeaseStatus.RELEASED.value
    finally:
        _cleanup(host_id, device_id)
