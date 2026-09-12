"""services/plan_run_manual.py 直测（#1520 切片价值：不经 HTTP 测业务分支）。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.models.audit import AuditLog
from backend.models.enums import JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services.plan_run_manual import manual_retry_job_sync


def _seed(db, *, job_status=JobStatus.RUNNING.value):
    host = Host(id="pm-h1", hostname="pm-h1", status="ONLINE")
    plan = Plan(name="pm-plan", failure_threshold=0.1, created_by="pytest")
    db.add_all([host, plan])
    db.flush()
    device = Device(
        id=77001, serial="PM-D1", host_id="pm-h1", status="ONLINE",
        adb_connected=True, adb_state="device",
    )
    db.add(device)
    db.flush()
    run = PlanRun(
        plan_id=plan.id, status="RUNNING", failure_threshold=0.1,
        plan_snapshot={"name": plan.name, "plan_id": plan.id},
        run_type="MANUAL", triggered_by="pytest",
    )
    db.add(run)
    db.flush()
    job = JobInstance(
        plan_run_id=run.id, plan_id=plan.id, device_id=device.id,
        host_id="pm-h1", status=job_status,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    )
    db.add(job)
    db.commit()
    return run, job


def test_manual_retry_sets_action_and_writes_audit(db_session):
    run, job = _seed(db_session)

    returned = manual_retry_job_sync(
        db_session, run.id, job.id,
        reason="unit", actor_id=None, actor_username="tester",
    )

    assert returned.manual_action == "RETRY_NOW"
    assert returned.next_retry_at is not None
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "patrol_manual_retry",
                AuditLog.resource_id == str(job.id))
        .first()
    )
    assert audit is not None
    assert audit.details["reason"] == "unit"


def test_manual_retry_rejects_non_running_job(db_session):
    run, job = _seed(db_session, job_status=JobStatus.COMPLETED.value)

    with pytest.raises(HTTPException) as exc:
        manual_retry_job_sync(db_session, run.id, job.id)

    assert exc.value.status_code == 409


def test_manual_retry_idempotent_short_circuit(db_session):
    """同向 manual_action 已等待消费时不再二次写审计。"""
    run, job = _seed(db_session)
    manual_retry_job_sync(db_session, run.id, job.id, reason="first")
    manual_retry_job_sync(db_session, run.id, job.id, reason="second")

    count = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "patrol_manual_retry",
                AuditLog.resource_id == str(job.id))
        .count()
    )
    assert count == 1
