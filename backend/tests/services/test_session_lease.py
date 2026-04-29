"""Integration tests for session-lease lifecycle in dispatch/claim/complete."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.core.database import AsyncSessionLocal, SessionLocal
from backend.models.enums import HostStatus, JobStatus, WorkflowStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance, TaskTemplate
from backend.models.workflow import WorkflowDefinition, WorkflowRun
from backend.services.device_lock import acquire_lock


PIPELINE_DEF = {
    "stages": {
        "prepare": [{"step_id": "noop", "action": "builtin:check_device", "timeout_seconds": 30}],
        "execute": [],
        "post_process": [],
    }
}


def _seed(lock_run_id=None, lock_expires_at=None) -> dict:
    """Create Host + Device + WorkflowDefinition + TaskTemplate + WorkflowRun + JobInstance."""
    suffix = uuid4().hex[:8]
    host_id = f"lease-host-{suffix}"
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        host = Host(id=host_id, hostname=f"h-{suffix}", status=HostStatus.ONLINE.value, created_at=now)
        device = Device(
            serial=f"S-{suffix}", host_id=host_id, status="ONLINE", tags=[], created_at=now,
            lock_run_id=lock_run_id, lock_expires_at=lock_expires_at,
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
            host_id=host_id, status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now, updated_at=now,
        )
        db.add(job)
        db.commit()

        return {
            "host_id": host_id, "device_id": device.id,
            "wf_id": wf.id, "run_id": run.id, "job_id": job.id,
        }
    finally:
        db.close()


def _get_device(device_id: int) -> Device:
    db = SessionLocal()
    try:
        d = db.get(Device, device_id)
        db.expunge(d)
        return d
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


# ── Claim skips locked device ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_claim_skips_locked_device():
    """Claim endpoint skips job for a device locked by another job."""
    from backend.api.routes.agent_api import get_pending_jobs

    future = datetime.now(timezone.utc) + timedelta(seconds=300)
    seed = _seed(lock_run_id=9999, lock_expires_at=future)

    async with AsyncSessionLocal() as db:
        resp = await get_pending_jobs(host_id=seed["host_id"], limit=10, db=db)

    # No jobs should be claimed since device is locked by job 9999
    assert resp.data == []
    job = _get_job(seed["job_id"])
    assert job.status == JobStatus.PENDING.value


# ── Complete releases device lock ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_complete_job_releases_lock():
    """complete_job endpoint releases device lock on terminal status."""
    from backend.api.routes.agent_api import _RunCompleteIn, complete_job
    from backend.models.device_lease import DeviceLease
    from backend.models.enums import LeaseStatus, LeaseType

    seed = _seed()

    # First acquire the lock
    async with AsyncSessionLocal() as db:
        await acquire_lock(db, seed["device_id"], seed["job_id"], 600)
        # Transition job to RUNNING
        job = await db.get(JobInstance, seed["job_id"])
        job.status = JobStatus.RUNNING.value
        job.started_at = datetime.now(timezone.utc)
        await db.commit()

    d = _get_device(seed["device_id"])
    assert d.lock_run_id == seed["job_id"]

    # Create an ACTIVE DeviceLease for Phase 2b fencing_token validation
    token = f"{seed['device_id']}:1"
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        db.add(DeviceLease(
            device_id=seed["device_id"],
            job_id=seed["job_id"],
            host_id=seed["host_id"],
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=token,
            lease_generation=1,
            agent_instance_id=seed["host_id"],
            acquired_at=now,
            renewed_at=now,
            expires_at=now + timedelta(seconds=600),
        ))
        db.commit()
    finally:
        db.close()

    # Now complete the job
    payload = _RunCompleteIn(update={"status": "COMPLETED"}, fencing_token=token)
    async with AsyncSessionLocal() as db:
        await complete_job(seed["job_id"], payload, db)

    d = _get_device(seed["device_id"])
    assert d.lock_run_id is None
    assert d.status == "ONLINE"
