"""Reconciler unit/integration tests for ADR-0019 Phase 4a/4b.

Tests the 3 reconciler check functions independently.
Each test seeds data via sync SessionLocal, then calls reconciler
functions with AsyncSessionLocal.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("DATABASE_URL", "").startswith("sqlite"),
    reason="Reconciler tests require PostgreSQL (device_leases partial unique index)",
)

from sqlalchemy import select

from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType, WorkflowStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance, TaskTemplate
from backend.models.workflow import WorkflowDefinition, WorkflowRun
from backend.scheduler.device_lease_reconciler import (
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
    """Create minimal host + device + job → WorkflowDefinition → WorkflowRun."""
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        host = Host(id=host_id, hostname=f"h-{host_id}", status=HostStatus.ONLINE.value, created_at=now)
        device = Device(id=device_id, serial=f"DW-{device_id}", host_id=host_id,
                        status="ONLINE", tags=[], created_at=now,
                        adb_connected=True, adb_state="device")
        wf = WorkflowDefinition(name=f"wf-{device_id}", description="reconciler test",
                                failure_threshold=0.1, created_by="pytest",
                                created_at=now, updated_at=now)
        db.add_all([host, device, wf])
        db.flush()

        tpl = TaskTemplate(workflow_definition_id=wf.id, name=f"tpl-{device_id}",
                           pipeline_def=PIPELINE_DEF, sort_order=0, created_at=now)
        db.add(tpl)
        db.flush()

        run = WorkflowRun(workflow_definition_id=wf.id, status=WorkflowStatus.RUNNING.value,
                          failure_threshold=0.1, triggered_by="pytest", started_at=now)
        db.add(run)
        db.flush()

        job = JobInstance(id=job_id, workflow_run_id=run.id, task_template_id=tpl.id,
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
        # Project onto device
        dev = db.get(Device, device_id)
        if dev:
            dev.status = "BUSY"
            dev.lock_run_id = job_id
            dev.lock_expires_at = past
        db.commit()
        return lid
    finally:
        db.close()


def _cleanup(host_id: str, device_id: int) -> None:
    """Remove seeded data (order respects FK constraints)."""
    from backend.models.job import JobArtifact, StepTrace
    from backend.models.resource_pool import ResourceAllocation
    from backend.models.script_batch import ScriptBatch, ScriptRun
    db = SessionLocal()
    try:
        db.execute(StepTrace.__table__.delete())
        db.execute(JobArtifact.__table__.delete())
        db.execute(DeviceLease.__table__.delete())
        db.execute(ResourceAllocation.__table__.delete())
        db.execute(ScriptRun.__table__.delete())
        db.execute(ScriptBatch.__table__.delete())
        db.execute(JobInstance.__table__.delete())
        db.execute(TaskTemplate.__table__.delete())
        db.execute(WorkflowRun.__table__.delete())
        db.execute(WorkflowDefinition.__table__.delete())
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
        dev.lock_run_id = None
        dev.lock_expires_at = None
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
