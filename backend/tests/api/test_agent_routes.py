"""Tests for Agent Jobs API routes."""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.api.routes.agent_api import (
    _ExtendLockIn,
    _JobHeartbeatIn,
    _RunCompleteIn,
    _StepStatusIn,
    complete_job,
    extend_job_lock,
    get_pending_jobs,
    job_heartbeat,
    update_job_step_status,
)
from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType, WorkflowStatus
from backend.models.device_lease import DeviceLease
from backend.models.host import Device, Host
from backend.models.job import JobInstance, StepTrace, TaskTemplate
from backend.models.workflow import WorkflowDefinition, WorkflowRun


PIPELINE_DEF = {
    "stages": {
        "prepare": [
            {
                "step_id": "check_device",
                "action": "builtin:check_device",
                "timeout_seconds": 30,
            }
        ],
        "execute": [],
        "post_process": [],
    }
}


def _seed_job(status: str = JobStatus.PENDING.value) -> dict:
    suffix = uuid4().hex[:8]
    host_id = f"agent-host-{suffix}"
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        host = Host(
            id=host_id,
            hostname=f"host-{suffix}",
            status=HostStatus.ONLINE.value,
            created_at=now,
        )
        device = Device(
            serial=f"SERIAL-{suffix}",
            host_id=host_id,
            status="ONLINE",
            tags=[],
            created_at=now,
        )
        wf = WorkflowDefinition(
            name=f"wf-{suffix}",
            description="pytest workflow",
            failure_threshold=0.1,
            created_by="pytest",
            created_at=now,
            updated_at=now,
        )
        db.add_all([host, device, wf])
        db.flush()

        tpl = TaskTemplate(
            workflow_definition_id=wf.id,
            name=f"tpl-{suffix}",
            pipeline_def=PIPELINE_DEF,
            sort_order=0,
            created_at=now,
        )
        db.add(tpl)
        db.flush()

        run = WorkflowRun(
            workflow_definition_id=wf.id,
            status=WorkflowStatus.RUNNING.value,
            failure_threshold=0.1,
            triggered_by="pytest",
            started_at=now,
        )
        db.add(run)
        db.flush()

        job = JobInstance(
            workflow_run_id=run.id,
            task_template_id=tpl.id,
            device_id=device.id,
            host_id=host_id,
            status=status,
            pipeline_def=PIPELINE_DEF,
            created_at=now,
            updated_at=now,
            started_at=now if status == JobStatus.RUNNING.value else None,
        )
        db.add(job)
        db.commit()

        return {
            "host_id": host_id,
            "device_id": device.id,
            "device_serial": device.serial,
            "workflow_definition_id": wf.id,
            "task_template_id": tpl.id,
            "workflow_run_id": run.id,
            "job_id": job.id,
        }
    finally:
        db.close()


def _seed_host_only() -> dict:
    suffix = uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    host_id = f"agent-empty-{suffix}"
    db = SessionLocal()
    try:
        host = Host(
            id=host_id,
            hostname=f"empty-{suffix}",
            status=HostStatus.ONLINE.value,
            created_at=now,
        )
        db.add(host)
        db.commit()
        return {"host_id": host_id}
    finally:
        db.close()


def _setup_lease(seed: dict) -> str:
    """Create an ACTIVE DeviceLease for Phase 2b fencing_token validation.
    Also projects to device table (Phase 2c: projection is required).

    Returns the fencing_token that callers should pass to handlers.
    """
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=600)
    token = f"{seed['device_id']}:1"

    db = SessionLocal()
    try:
        lease = DeviceLease(
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
            expires_at=expires,
        )
        db.add(lease)
        # Phase 2c: project to device table (release_lease/extend_lease require it)
        dev = db.query(Device).filter(Device.id == seed["device_id"]).first()
        if dev is not None:
            dev.status = "BUSY"
        db.commit()
        return token
    finally:
        db.close()


def _cleanup_seed(seed: dict) -> None:
    db = SessionLocal()
    try:
        job_id = seed.get("job_id")
        if job_id:
            db.query(DeviceLease).filter(DeviceLease.job_id == job_id).delete()
            db.query(StepTrace).filter(StepTrace.job_id == job_id).delete()
            db.query(JobInstance).filter(JobInstance.id == job_id).delete()

        run_id = seed.get("workflow_run_id")
        if run_id:
            db.query(WorkflowRun).filter(WorkflowRun.id == run_id).delete()

        tpl_id = seed.get("task_template_id")
        if tpl_id:
            db.query(TaskTemplate).filter(TaskTemplate.id == tpl_id).delete()

        wf_id = seed.get("workflow_definition_id")
        if wf_id:
            db.query(WorkflowDefinition).filter(WorkflowDefinition.id == wf_id).delete()

        device_id = seed.get("device_id")
        if device_id:
            db.query(Device).filter(Device.id == device_id).delete()

        host_id = seed.get("host_id")
        if host_id:
            db.query(Host).filter(Host.id == host_id).delete()

        db.commit()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_get_pending_jobs_success():
    seed = _seed_job(status=JobStatus.PENDING.value)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await get_pending_jobs(host_id=seed["host_id"], limit=5, db=async_db, _=None)
        assert result.error is None
        assert isinstance(result.data, list)
        assert len(result.data) == 1
        item = result.data[0]
        assert item.id == seed["job_id"]
        assert item.host_id == seed["host_id"]
        assert item.device_id == seed["device_id"]
        assert item.device_serial == seed["device_serial"]
        # claim 端点原子过渡 PENDING → RUNNING（ADR-0018 锁保护机制）
        assert item.status == JobStatus.RUNNING.value
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_get_pending_jobs_empty():
    seed = _seed_host_only()
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await get_pending_jobs(host_id=seed["host_id"], limit=10, db=async_db, _=None)
        assert result.error is None
        assert result.data == []
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_job_heartbeat_transitions_to_running():
    seed = _seed_job(status=JobStatus.PENDING.value)
    token = _setup_lease(seed)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await job_heartbeat(
                job_id=seed["job_id"],
                payload=_JobHeartbeatIn(status="RUNNING", fencing_token=token),
                db=async_db,
                _=None,
            )
        assert result.error is None
        assert result.data["status"] == JobStatus.RUNNING.value

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.status == JobStatus.RUNNING.value
            assert job.started_at is not None
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_job_heartbeat_refreshes_liveness_for_running_job(engine):
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    old_liveness = datetime.now(timezone.utc) - timedelta(hours=1)
    try:
        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            job.updated_at = old_liveness
            db.commit()
        finally:
            db.close()

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await job_heartbeat(
                job_id=seed["job_id"],
                payload=_JobHeartbeatIn(status="RUNNING", fencing_token=token),
                db=async_db,
                _=None,
            )
        assert result.error is None
        assert result.data["status"] == JobStatus.RUNNING.value

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.updated_at > old_liveness
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_complete_job_maps_finished_to_completed():
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await complete_job(
                job_id=seed["job_id"],
                payload=_RunCompleteIn(update={"status": "FINISHED", "exit_code": 0}, fencing_token=token),
                db=async_db,
                _=None,
            )
        assert result.error is None
        assert result.data["status"] == JobStatus.COMPLETED.value

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            run = db.get(WorkflowRun, seed["workflow_run_id"])
            assert job is not None
            assert run is not None
            assert job.status == JobStatus.COMPLETED.value
            assert job.ended_at is not None
            assert run.status == WorkflowStatus.SUCCESS.value
            assert run.ended_at is not None
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_complete_job_persists_run_complete_snapshot():
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await complete_job(
                job_id=seed["job_id"],
                payload=_RunCompleteIn(
                    update={
                        "status": "FINISHED",
                        "exit_code": 0,
                        "log_summary": "risk=LOW;restarts=1",
                    },
                    artifact={
                        "storage_uri": "file:///tmp/report.tar.gz",
                        "size_bytes": 1024,
                        "checksum": "abc123",
                    },
                    fencing_token=token,
                ),
                db=async_db,
                _=None,
            )
        assert result.error is None
        assert result.data["status"] == JobStatus.COMPLETED.value

        db = SessionLocal()
        try:
            snapshot = (
                db.query(StepTrace)
                .filter(
                    StepTrace.job_id == seed["job_id"],
                    StepTrace.step_id == "__job__",
                    StepTrace.event_type == "RUN_COMPLETE",
                )
                .first()
            )
            assert snapshot is not None
            payload = json.loads(snapshot.output)
            assert payload["update"]["log_summary"] == "risk=LOW;restarts=1"
            assert payload["artifact"]["storage_uri"] == "file:///tmp/report.tar.gz"
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_extend_lock_success(engine):
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    old_liveness = datetime.now(timezone.utc) - timedelta(hours=1)
    try:
        db = SessionLocal()
        try:
            device = db.get(Device, seed["device_id"])
            assert device is not None
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            job.updated_at = old_liveness
            db.commit()
        finally:
            db.close()

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await extend_job_lock(
                job_id=seed["job_id"],
                payload=_ExtendLockIn(fencing_token=token),
                db=async_db, _=None,
            )
        assert result.error is None
        assert result.data["job_id"] == seed["job_id"]
        assert result.data["expires_at"]

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.updated_at > old_liveness
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_extend_lock_conflict():
    """Phase 2c: extend_job_lock returns 409 when no ACTIVE lease exists."""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    try:
        # Release the lease so extend_lease returns False → 409
        db = SessionLocal()
        try:
            db.query(DeviceLease).filter(
                DeviceLease.device_id == seed["device_id"],
                DeviceLease.job_id == seed["job_id"],
            ).update({"status": LeaseStatus.RELEASED.value})
            db.commit()
        finally:
            db.close()

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as exc_info:
                await extend_job_lock(
                    job_id=seed["job_id"],
                    payload=_ExtendLockIn(fencing_token=token),
                    db=async_db, _=None,
                )
        assert exc_info.value.status_code == 409
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_update_job_step_status_upserts_trace():
    seed = _seed_job(status=JobStatus.RUNNING.value)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await update_job_step_status(
                job_id=seed["job_id"],
                step_id="check_device",
                payload=_StepStatusIn(
                    status="RUNNING",
                    started_at="2026-03-01T10:00:00Z",
                    error_message=None,
                ),
                db=async_db,
                _=None,
            )
        assert result.error is None
        assert result.data["job_id"] == seed["job_id"]
        assert result.data["step_id"] == "check_device"
        assert result.data["status"] == "RUNNING"

        db = SessionLocal()
        try:
            trace = (
                db.query(StepTrace)
                .filter(
                    StepTrace.job_id == seed["job_id"],
                    StepTrace.step_id == "check_device",
                    StepTrace.event_type == "status_update",
                )
                .first()
            )
            assert trace is not None
            assert trace.stage == "execute"
            assert trace.status == "RUNNING"
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)
