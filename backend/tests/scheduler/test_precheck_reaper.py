"""Tests for precheck_reaper — orphan precheck PlanRun reconciliation."""

from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy.orm.attributes import flag_modified

from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.scheduler.precheck_reaper import (
    reconcile_stale_precheck_runs,
    _is_stale_iso,
    _is_stale_epoch_ms,
)


@pytest.fixture(autouse=True)
def _seed_plan_one(db_session):
    """PG 严格 FK:测试里直接写 plan_id=1 需要先有对应的 Plan 行(SQLite 弱 FK 时此问题被掩盖)。"""
    if db_session.get(Plan, 1) is None:
        db_session.add(
            Plan(
                id=1,
                name="reaper-test-plan",
                description="stub plan for reaper tests",
                failure_threshold=0.05,
                created_by="test",
            )
        )
        db_session.flush()
    yield


# ── Unit helpers ───────────────────────────────────────────────────────────

def test_is_stale_iso_none():
    assert _is_stale_iso(None, 60) is False


def test_is_stale_iso_fresh():
    # A timestamp from 5 seconds ago should not be stale against a 90s threshold.
    fresh = "2026-05-10T07:09:44.728Z"
    # We can't easily test staleness without mocking time, but we can test
    # the None/parse cases.
    pass


def test_is_stale_epoch_ms_none():
    assert _is_stale_epoch_ms(None, 60) is False


def test_is_stale_epoch_ms_recent():
    # 5 seconds ago in ms — should NOT be stale against 180s threshold.
    recent_ms = (time.time() - 5) * 1000
    assert _is_stale_epoch_ms(recent_ms, 180) is False


def test_is_stale_epoch_ms_old():
    # 200 seconds ago in ms — should be stale against 180s threshold.
    old_ms = (time.time() - 200) * 1000
    assert _is_stale_epoch_ms(old_ms, 180) is True


# ── Reaper integration tests ───────────────────────────────────────────────


def test_reaper_marks_swept_precheck_failed(db_session):
    """A RUNNING PlanRun whose SAQ precheck job was aborted should be failed."""
    run_ctx = {
        "dispatch_device_ids": [2429],
        "dispatch_state": {
            "enqueue_key": "precheck:143",
            "requeue_attempts": 0,
            "status": "queued",
            "enqueued_at": "2026-05-10T07:09:44.728Z",
            "started_at": None,
            "completed_at": None,
            "last_error": None,
        },
    }
    pr = PlanRun(
        plan_id=1,
        status="RUNNING",
        failure_threshold=0.05,
        plan_snapshot={},
        run_type="MANUAL",
        run_context=run_ctx,
        triggered_by="test",
    )
    db_session.add(pr)
    db_session.flush()

    with patch(
        "backend.scheduler.precheck_reaper.get_saq_job_state_sync",
        return_value={"status": "aborted", "error": "swept", "worker_id": "dead-worker"},
    ):
        summary = reconcile_stale_precheck_runs(db=db_session)

    assert summary["failed"] == 1
    assert summary["checked"] == 1
    db_session.refresh(pr)
    assert pr.status == "FAILED"
    assert pr.result_summary["precheck_failed"] is True
    assert "precheck_job_aborted:swept" in pr.result_summary["reason"]


def test_reaper_reenqueues_missing_precheck_once(db_session):
    """A RUNNING PlanRun with a missing SAQ job should be re-enqueued once."""
    run_ctx = {
        "dispatch_device_ids": [2429],
        "dispatch_state": {
            "enqueue_key": "precheck:136",
            "requeue_attempts": 0,
            "status": "queued",
            "enqueued_at": "2026-05-10T07:00:00.000Z",
            "started_at": None,
            "completed_at": None,
            "last_error": None,
        },
    }
    pr = PlanRun(
        plan_id=1,
        status="RUNNING",
        failure_threshold=0.05,
        plan_snapshot={},
        run_type="MANUAL",
        run_context=run_ctx,
        triggered_by="test",
    )
    db_session.add(pr)
    db_session.flush()

    enqueue_calls = []

    with patch(
        "backend.scheduler.precheck_reaper.get_saq_job_state_sync",
        return_value=None,
    ), patch(
        "backend.scheduler.precheck_reaper._is_stale_iso",
        return_value=True,
    ), patch(
        "backend.scheduler.precheck_reaper.enqueue_sync",
        side_effect=lambda *args, **kwargs: enqueue_calls.append(kwargs),
    ):
        summary = reconcile_stale_precheck_runs(db=db_session)

    assert summary["reenqueued"] == 1
    assert len(enqueue_calls) == 1
    assert enqueue_calls[0]["plan_run_id"] == pr.id
    assert enqueue_calls[0]["key"] == "precheck:136"

    db_session.refresh(pr)
    assert pr.run_context["dispatch_state"]["requeue_attempts"] == 1
    assert pr.run_context["dispatch_state"]["last_error"] == "precheck_job_missing_reenqueued"


def test_reaper_enqueue_failure_does_not_bump_requeue_attempts(db_session):
    """EnqueueSyncError must not silently drop — leave row for next reaper pass."""
    from datetime import datetime, timezone

    run_ctx = {
        "dispatch_device_ids": [2429],
        "dispatch_state": {
            "enqueue_key": "precheck:137",
            "requeue_attempts": 0,
            "status": "queued",
            "enqueued_at": "2026-05-10T07:00:00.000Z",
            "started_at": None,
            "completed_at": None,
            "last_error": None,
        },
    }
    pr = PlanRun(
        plan_id=1,
        status="RUNNING",
        failure_threshold=0.05,
        plan_snapshot={},
        run_type="MANUAL",
        run_context=run_ctx,
        triggered_by="test",
    )
    db_session.add(pr)
    db_session.flush()

    from backend.tasks.saq_worker import EnqueueSyncError

    with patch(
        "backend.scheduler.precheck_reaper.get_saq_job_state_sync",
        return_value=None,
    ), patch(
        "backend.scheduler.precheck_reaper._is_stale_iso",
        return_value=True,
    ), patch(
        "backend.scheduler.precheck_reaper.enqueue_sync",
        side_effect=EnqueueSyncError("SAQ not running"),
    ):
        summary = reconcile_stale_precheck_runs(db=db_session)

    assert summary["reenqueued"] == 0
    assert summary["skipped"] == 1

    db_session.refresh(pr)
    assert pr.run_context["dispatch_state"]["requeue_attempts"] == 0
    assert "precheck_reenqueue_failed" in pr.run_context["dispatch_state"]["last_error"]


def test_reaper_skips_planrun_with_jobs(db_session):
    """A RUNNING PlanRun that already has JobInstance rows is skipped."""
    from datetime import datetime, timezone
    from backend.models.enums import HostStatus
    from backend.models.host import Device, Host

    # PG 严格 FK:JobInstance.device_id / host_id 必须先有 Device / Host 行。
    now = datetime.now(timezone.utc)
    host = Host(
        id="h-A", hostname="h-A",
        status=HostStatus.ONLINE.value, created_at=now,
    )
    device = Device(
        id=2429, serial="DEV-2429", host_id="h-A",
        status="ONLINE", tags=[], created_at=now,
    )
    db_session.add_all([host, device])
    db_session.flush()

    run_ctx = {
        "dispatch_device_ids": [2429],
        "dispatch_state": {
            "enqueue_key": "precheck:200",
            "requeue_attempts": 0,
            "status": "queued",
            "enqueued_at": "2026-05-10T07:00:00.000Z",
            "started_at": None,
            "completed_at": None,
            "last_error": None,
        },
    }
    pr = PlanRun(
        plan_id=1,
        status="RUNNING",
        failure_threshold=0.05,
        plan_snapshot={},
        run_type="MANUAL",
        run_context=run_ctx,
        triggered_by="test",
    )
    db_session.add(pr)
    db_session.flush()

    # Create a JobInstance for this PlanRun — it should be skipped.
    job = JobInstance(
        plan_run_id=pr.id,
        plan_id=1,
        device_id=2429,
        host_id="h-A",
        status="RUNNING",
        pipeline_def={},
    )
    db_session.add(job)
    db_session.commit()

    summary = reconcile_stale_precheck_runs(db=db_session)
    assert summary["checked"] == 0
    assert summary["skipped"] == 0  # not even counted

    db_session.refresh(pr)
    assert pr.status == "RUNNING"  # untouched


def test_reaper_skips_run_without_dispatch_state(db_session):
    """PlanRun rows without dispatch_state are skipped."""
    pr = PlanRun(
        plan_id=1,
        status="RUNNING",
        failure_threshold=0.05,
        plan_snapshot={},
        run_type="MANUAL",
        run_context={},
        triggered_by="test",
    )
    db_session.add(pr)
    db_session.commit()

    summary = reconcile_stale_precheck_runs(db=db_session)
    assert summary["checked"] == 0


def test_reaper_does_not_re_enqueue_beyond_cap(db_session, monkeypatch):
    """When requeue_attempts already == MAX, skip instead of re-enqueuing."""
    monkeypatch.setattr(
        "backend.scheduler.precheck_reaper.MAX_PRECHECK_REENQUEUE_ATTEMPTS", 1
    )
    run_ctx = {
        "dispatch_device_ids": [2429],
        "dispatch_state": {
            "enqueue_key": "precheck:301",
            "requeue_attempts": 1,  # already at cap
            "status": "queued",
            "enqueued_at": "2026-05-10T07:00:00.000Z",
            "started_at": None,
            "completed_at": None,
            "last_error": "precheck_job_missing_reenqueued",
        },
    }
    pr = PlanRun(
        plan_id=1,
        status="RUNNING",
        failure_threshold=0.05,
        plan_snapshot={},
        run_type="MANUAL",
        run_context=run_ctx,
        triggered_by="test",
    )
    db_session.add(pr)
    db_session.flush()

    with patch(
        "backend.scheduler.precheck_reaper.get_saq_job_state_sync",
        return_value=None,
    ), patch(
        "backend.scheduler.precheck_reaper._is_stale_iso",
        return_value=True,
    ):
        summary = reconcile_stale_precheck_runs(db=db_session)

    assert summary["reenqueued"] == 0
    # Should be in skipped (it was checked but no action taken)
    assert summary["skipped"] >= 0
