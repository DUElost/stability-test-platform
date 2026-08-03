"""Tests for the #116 historical-data cleanup script."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from backend.models.enums import JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun


def _seed(db) -> tuple[JobInstance, JobInstance]:
    suffix = uuid4().hex[:6]
    host = Host(id=f"clean-h-{suffix}", hostname="clean", status="ONLINE")
    db.add(host)
    db.flush()
    dev_a = Device(serial=f"clean-da-{suffix}", host_id=host.id, status="ONLINE")
    dev_b = Device(serial=f"clean-db-{suffix}", host_id=host.id, status="ONLINE")
    db.add_all([dev_a, dev_b])
    db.flush()
    plan = Plan(name=f"clean-{suffix}", failure_threshold=0.05, created_by="pytest")
    db.add(plan)
    db.flush()
    pr = PlanRun(
        plan_id=plan.id, status="RUNNING", failure_threshold=0.05,
        plan_snapshot={}, run_type="MANUAL",
    )
    db.add(pr)
    db.flush()
    now = datetime.now(timezone.utc)
    terminal = JobInstance(
        plan_run_id=pr.id, plan_id=plan.id, device_id=dev_a.id, host_id=host.id,
        status=JobStatus.FAILED.value,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        execution_state="EXECUTING_STEP", started_at=now,
    )
    running = JobInstance(
        plan_run_id=pr.id, plan_id=plan.id, device_id=dev_b.id, host_id=host.id,
        status=JobStatus.RUNNING.value,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        execution_state="WAITING_BARRIER", started_at=now,
    )
    db.add_all([terminal, running])
    db.commit()
    return terminal, running


def test_clean_nulls_terminal_only(db_session):
    from backend.scripts.clean_terminal_execution_state import (
        clean_terminal_execution_state,
    )

    terminal, running = _seed(db_session)
    summary = clean_terminal_execution_state(db_session)

    assert summary["scanned"] == 1
    assert summary["changed"] == 1
    assert summary["job_ids"] == [terminal.id]
    db_session.refresh(terminal)
    db_session.refresh(running)
    assert terminal.execution_state is None
    assert running.execution_state == "WAITING_BARRIER"  # 非终态不动


def test_clean_dry_run_reports_without_changing(db_session):
    from backend.scripts.clean_terminal_execution_state import (
        clean_terminal_execution_state,
    )

    terminal, _running = _seed(db_session)
    summary = clean_terminal_execution_state(db_session, dry_run=True)

    assert summary["dry_run"] is True
    assert summary["scanned"] == 1
    assert summary["changed"] == 1
    db_session.refresh(terminal)
    assert terminal.execution_state == "EXECUTING_STEP"


def test_clean_is_idempotent(db_session):
    from backend.scripts.clean_terminal_execution_state import (
        clean_terminal_execution_state,
    )

    _seed(db_session)
    assert clean_terminal_execution_state(db_session)["changed"] == 1
    second = clean_terminal_execution_state(db_session)
    assert second["scanned"] == 0
    assert second["changed"] == 0
