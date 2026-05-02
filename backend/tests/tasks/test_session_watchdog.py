"""Tests for session_watchdog checks — ADR-0019 Phase 4c updated."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType, WorkflowStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance, TaskTemplate
from backend.models.workflow import WorkflowDefinition, WorkflowRun
from backend.tasks.session_watchdog import session_watchdog_once


PIPELINE_DEF = {
    "stages": {"prepare": [], "execute": [], "post_process": []}
}


def _seed_job_with_host(
    host_heartbeat: datetime,
    job_status: str = JobStatus.RUNNING.value,
    lock_run_id=None,
    lock_expires_at=None,
    job_ended_at=None,
) -> dict:
    suffix = uuid4().hex[:8]
    host_id = f"wd-host-{suffix}"
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{suffix}",
            status=HostStatus.ONLINE.value,
            last_heartbeat=host_heartbeat,
            created_at=now,
        )
        device = Device(
            serial=f"S-{suffix}", host_id=host_id, status="BUSY", tags=[],
            created_at=now,
            lock_run_id=None if lock_run_id == "auto" else lock_run_id,
            lock_expires_at=lock_expires_at,
        )
        db.add_all([host, device])
        db.flush()

        wf = WorkflowDefinition(
            name=f"wf-{suffix}", description="t", failure_threshold=0.1,
            created_by="test", created_at=now, updated_at=now,
        )
        db.add(wf)
        db.flush()

        tpl = TaskTemplate(
            workflow_definition_id=wf.id, name=f"tpl-{suffix}",
            pipeline_def=PIPELINE_DEF, sort_order=0, created_at=now,
        )
        db.add(tpl)
        db.flush()

        run = WorkflowRun(
            workflow_definition_id=wf.id, status=WorkflowStatus.RUNNING.value,
            failure_threshold=0.1, triggered_by="test", started_at=now,
        )
        db.add(run)
        db.flush()

        job = JobInstance(
            workflow_run_id=run.id, task_template_id=tpl.id, device_id=device.id,
            host_id=host_id, status=job_status, pipeline_def=PIPELINE_DEF,
            created_at=now, updated_at=now, started_at=now,
            ended_at=job_ended_at,
        )
        db.add(job)

        # Update lock_run_id to point to the job
        if lock_run_id == "auto":
            db.flush()
            device.lock_run_id = job.id
            device.lock_expires_at = lock_expires_at

        db.commit()
        return {
            "host_id": host_id, "device_id": device.id,
            "job_id": job.id, "run_id": run.id,
        }
    finally:
        db.close()


def _get_job(job_id: int) -> JobInstance:
    db = SessionLocal()
    try:
        j = db.get(JobInstance, job_id)
        db.expunge(j)
        return j
    finally:
        db.close()


def _get_host(host_id: str) -> Host:
    db = SessionLocal()
    try:
        h = db.get(Host, host_id)
        db.expunge(h)
        return h
    finally:
        db.close()


@pytest.mark.asyncio
async def test_host_timeout_marks_offline_and_jobs_unknown():
    """Host with stale heartbeat → OFFLINE, RUNNING jobs → UNKNOWN."""
    old_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=300)
    seed = _seed_job_with_host(host_heartbeat=old_heartbeat)

    await async_engine.dispose()
    await session_watchdog_once()

    host = _get_host(seed["host_id"])
    assert host.status == HostStatus.OFFLINE.value

    job = _get_job(seed["job_id"])
    assert job.status == JobStatus.UNKNOWN.value


@pytest.mark.asyncio
async def test_watchdog_host_timeout_keeps_lease_active():
    """Phase 4c: host timeout → UNKNOWN but lease stays ACTIVE.

    Watchdog no longer calls release_lease — Reconciler is the sole handler
    of lease expiration.
    """
    old_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=300)
    future_expiry = datetime.now(timezone.utc) + timedelta(seconds=600)
    seed = _seed_job_with_host(
        host_heartbeat=old_heartbeat,
        lock_run_id="auto",
        lock_expires_at=future_expiry,
    )

    # Create an ACTIVE lease for the job
    db = SessionLocal()
    try:
        lease = DeviceLease(
            device_id=seed["device_id"],
            job_id=seed["job_id"],
            host_id=seed["host_id"],
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{seed['device_id']}:1",
            lease_generation=1,
            agent_instance_id=seed["host_id"],
            acquired_at=datetime.now(timezone.utc),
            renewed_at=datetime.now(timezone.utc),
            expires_at=future_expiry,
        )
        db.add(lease)
        db.commit()
    finally:
        db.close()

    await async_engine.dispose()
    await session_watchdog_once()

    # Job → UNKNOWN
    job = _get_job(seed["job_id"])
    assert job.status == JobStatus.UNKNOWN.value

    # Lease must still be ACTIVE (not released by watchdog)
    db = SessionLocal()
    try:
        dl = (
            db.query(DeviceLease)
            .filter(
                DeviceLease.device_id == seed["device_id"],
                DeviceLease.job_id == seed["job_id"],
            )
            .first()
        )
        assert dl is not None
        assert dl.status == LeaseStatus.ACTIVE.value, (
            f"Lease must stay ACTIVE after host timeout; got {dl.status}"
        )

        # Phase 6d-1: 投影列断言（device.lock_run_id == seed["job_id"]）已下沉到
        # device_leases 真源；保留 lease ACTIVE 断言即可覆盖语义
    finally:
        db.close()


@pytest.mark.asyncio
async def test_watchdog_unknown_grace_keeps_lease_active():
    """Phase 4c: UNKNOWN grace → FAILED but lease stays ACTIVE.

    Watchdog _check_unknown_grace_period only changes job status.
    Reconciler is responsible for releasing the lease.
    """
    now = datetime.now(timezone.utc)
    old_ended = now - timedelta(seconds=600)  # past default 300s grace
    seed = _seed_job_with_host(
        host_heartbeat=now,
        job_status=JobStatus.UNKNOWN.value,
        job_ended_at=old_ended,
    )

    # Create an ACTIVE lease for the job
    db = SessionLocal()
    try:
        lease = DeviceLease(
            device_id=seed["device_id"],
            job_id=seed["job_id"],
            host_id=seed["host_id"],
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{seed['device_id']}:1",
            lease_generation=1,
            agent_instance_id=seed["host_id"],
            acquired_at=now,
            renewed_at=now,
            expires_at=now + timedelta(seconds=600),
        )
        db.add(lease)
        db.commit()
    finally:
        db.close()

    await async_engine.dispose()
    await session_watchdog_once()

    # Job → FAILED
    job = _get_job(seed["job_id"])
    assert job.status == JobStatus.FAILED.value

    # Lease must still be ACTIVE (not released by watchdog)
    db = SessionLocal()
    try:
        dl = (
            db.query(DeviceLease)
            .filter(
                DeviceLease.device_id == seed["device_id"],
                DeviceLease.job_id == seed["job_id"],
            )
            .first()
        )
        assert dl is not None
        assert dl.status == LeaseStatus.ACTIVE.value, (
            f"Lease must stay ACTIVE after UNKNOWN grace; got {dl.status}"
        )
    finally:
        db.close()


@pytest.mark.asyncio
async def test_unknown_grace_period_expires_to_failed():
    """Job in UNKNOWN past grace period → FAILED."""
    now = datetime.now(timezone.utc)
    old_ended = now - timedelta(seconds=600)  # well past default 300s grace
    seed = _seed_job_with_host(
        host_heartbeat=now,
        job_status=JobStatus.UNKNOWN.value,
        job_ended_at=old_ended,
    )

    await async_engine.dispose()
    await session_watchdog_once()

    job = _get_job(seed["job_id"])
    assert job.status == JobStatus.FAILED.value
