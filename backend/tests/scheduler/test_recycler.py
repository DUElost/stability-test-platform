from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.core.database import SessionLocal
from backend.models.enums import HostStatus, JobStatus, WorkflowStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance, TaskTemplate
from backend.models.workflow import WorkflowDefinition, WorkflowRun
from backend.scheduler import recycler


PIPELINE_DEF = {"stages": {"prepare": [], "execute": [], "post_process": []}}


def _seed_running_job(started_at: datetime, updated_at: datetime) -> dict:
    suffix = uuid4().hex[:8]
    host_id = f"recycler-host-{suffix}"
    db = SessionLocal()
    try:
        host = Host(
            id=host_id,
            hostname=f"recycler-{suffix}",
            status=HostStatus.ONLINE.value,
            last_heartbeat=updated_at,
            created_at=started_at,
        )
        device = Device(
            serial=f"R-{suffix}",
            host_id=host_id,
            status="BUSY",
            tags=[],
            created_at=started_at,
        )
        db.add_all([host, device])
        db.flush()

        wf = WorkflowDefinition(
            name=f"wf-{suffix}",
            description="pytest workflow",
            failure_threshold=0.1,
            created_by="pytest",
            created_at=started_at,
            updated_at=updated_at,
        )
        db.add(wf)
        db.flush()

        tpl = TaskTemplate(
            workflow_definition_id=wf.id,
            name=f"tpl-{suffix}",
            pipeline_def=PIPELINE_DEF,
            sort_order=0,
            created_at=started_at,
        )
        db.add(tpl)
        db.flush()

        run = WorkflowRun(
            workflow_definition_id=wf.id,
            status=WorkflowStatus.RUNNING.value,
            failure_threshold=0.1,
            triggered_by="pytest",
            started_at=started_at,
        )
        db.add(run)
        db.flush()

        job = JobInstance(
            workflow_run_id=run.id,
            task_template_id=tpl.id,
            device_id=device.id,
            host_id=host_id,
            status=JobStatus.RUNNING.value,
            pipeline_def=PIPELINE_DEF,
            created_at=started_at,
            updated_at=updated_at,
            started_at=started_at,
        )
        db.add(job)
        db.flush()
        device.lock_run_id = job.id
        device.lock_expires_at = updated_at + timedelta(minutes=5)
        db.commit()

        return {
            "host_id": host_id,
            "device_id": device.id,
            "workflow_definition_id": wf.id,
            "task_template_id": tpl.id,
            "workflow_run_id": run.id,
            "job_id": job.id,
        }
    finally:
        db.close()


def _cleanup_seed(seed: dict) -> None:
    from backend.models.device_lease import DeviceLease
    db = SessionLocal()
    try:
        db.query(DeviceLease).filter(DeviceLease.job_id == seed["job_id"]).delete()
        db.query(JobInstance).filter(JobInstance.id == seed["job_id"]).delete()
        db.query(WorkflowRun).filter(WorkflowRun.id == seed["workflow_run_id"]).delete()
        db.query(TaskTemplate).filter(TaskTemplate.id == seed["task_template_id"]).delete()
        db.query(WorkflowDefinition).filter(WorkflowDefinition.id == seed["workflow_definition_id"]).delete()
        db.query(Device).filter(Device.id == seed["device_id"]).delete()
        db.query(Host).filter(Host.id == seed["host_id"]).delete()
        db.commit()
    finally:
        db.close()


def test_recycler_keeps_running_job_with_recent_liveness(engine, monkeypatch):
    now = datetime.now(timezone.utc)
    old_started_at = now - timedelta(seconds=recycler.RUNNING_HEARTBEAT_TIMEOUT_SECONDS + 60)
    seed = _seed_running_job(started_at=old_started_at, updated_at=now)
    monkeypatch.setattr(recycler, "_fill_deferred_post_completions", lambda db, current: 0)
    monkeypatch.setattr(recycler, "_prune_steptrace_artifacts", lambda db, current: None)
    monkeypatch.setattr(recycler, "schedule_emit", lambda *args, **kwargs: None)
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.status == JobStatus.RUNNING.value
            assert job.status_reason != "running_timeout: no completion within window"
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2c recycler: LeaseProjectionError self-recovery
# ══════════════════════════════════════════════════════════════════════════════


def test_recycler_mark_timeout_releases_lease_despite_projection_failure(engine, monkeypatch):
    """_mark_timeout 在投影失败时走 fallback 释放 lease，不阻断后续 job (Phase 2c)."""
    from backend.models.device_lease import DeviceLease
    from backend.models.enums import LeaseStatus, LeaseType

    now = datetime.now(timezone.utc)
    old_time = now - timedelta(seconds=recycler.RUNNING_HEARTBEAT_TIMEOUT_SECONDS + 60)
    seed = _seed_running_job(started_at=old_time, updated_at=old_time)

    db = SessionLocal()
    try:
        # Create ACTIVE lease for the job
        lease = DeviceLease(
            device_id=seed["device_id"],
            job_id=seed["job_id"],
            host_id=seed["host_id"],
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{seed['device_id']}:1",
            lease_generation=1,
            agent_instance_id=seed["host_id"],
            acquired_at=old_time,
            renewed_at=old_time,
            expires_at=old_time + timedelta(seconds=600),
        )
        db.add(lease)
        # Tamper device.lock_run_id to trigger projection failure
        dev = db.query(Device).filter(Device.id == seed["device_id"]).first()
        dev.lock_run_id = 99999  # wrong holder
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(recycler, "_fill_deferred_post_completions", lambda db, current: 0)
    monkeypatch.setattr(recycler, "_prune_steptrace_artifacts", lambda db, current: None)
    monkeypatch.setattr(recycler, "schedule_emit", lambda *args, **kwargs: None)
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            # Job must be FAILED
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.status == JobStatus.FAILED.value, (
                f"Recycler must transition job to FAILED; got {job.status}"
            )

            # Lease must be RELEASED (fallback path)
            db.expire_all()
            dl = (
                db.query(DeviceLease)
                .filter(
                    DeviceLease.device_id == seed["device_id"],
                    DeviceLease.job_id == seed["job_id"],
                )
                .first()
            )
            assert dl is not None
            assert dl.status == LeaseStatus.RELEASED.value, (
                f"Recycler fallback must release lease; got {dl.status}"
            )
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)
