"""ADR-0022 — POST /plan-runs/{run_id}/jobs/{job_id}/manual-retry|manual-exit tests.

Validates:
  manual-retry:
    - sets next_retry_at = now() and manual_action = RETRY_NOW
    - DOES NOT reset current_failure_streak
    - writes audit_log
  manual-exit:
    - sets manual_action = EXIT_REQUESTED
    - records reason in status_reason if blank
    - writes audit_log
  both:
    - 409 when job is in terminal status
    - 404 when job is not in the requested PlanRun
    - require auth
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.models.audit import AuditLog
from backend.models.enums import HostStatus, JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def manual_chain(db_session):
    host = Host(
        id="h-manual", hostname="hostM",
        status=HostStatus.ONLINE.value,
        ip="10.0.0.61",
        ssh_user="root",
        ssh_port=22,
        extra={},
        last_heartbeat=datetime.now(timezone.utc),
    )
    dev = Device(serial="dev-manual-1", host_id="h-manual", status="BUSY")
    plan = Plan(name="manual-plan", failure_threshold=0.05)
    db_session.add_all([host, dev, plan])
    db_session.commit()

    pr = PlanRun(
        plan_id=plan.id,
        status="RUNNING",
        failure_threshold=0.05,
        plan_snapshot={"plan": {"id": plan.id}, "steps": []},
        run_type="MANUAL",
        triggered_by="testuser",
        chain_index=0,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(pr)
    db_session.commit()
    return {"host": host, "device": dev, "plan": plan, "plan_run": pr}


def _make_job(
    db_session,
    plan_run_id: int,
    plan_id: int,
    device_id: int,
    *,
    status: str = JobStatus.RUNNING.value,
    streak: int = 0,
    next_retry_at: datetime | None = None,
) -> JobInstance:
    job = JobInstance(
        plan_run_id=plan_run_id,
        plan_id=plan_id,
        device_id=device_id,
        host_id="h-manual",
        status=status,
        pipeline_def={"lifecycle": {"init": [], "patrol": {"steps": []}, "teardown": []}},
        started_at=datetime.now(timezone.utc) if status == JobStatus.RUNNING.value else None,
        current_failure_streak=streak,
        next_retry_at=next_retry_at,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


# ---------------------------------------------------------------------------
# manual-retry
# ---------------------------------------------------------------------------


class TestManualRetry:
    def test_manual_retry_sets_action_and_does_not_reset_streak(
        self, client, auth_headers, db_session, manual_chain,
    ):
        plan = manual_chain["plan"]
        pr = manual_chain["plan_run"]
        future = datetime.now(timezone.utc) + timedelta(minutes=20)
        job = _make_job(
            db_session, pr.id, plan.id, manual_chain["device"].id,
            streak=5, next_retry_at=future,
        )

        resp = client.post(
            f"/api/v1/plan-runs/{pr.id}/jobs/{job.id}/manual-retry",
            json={"reason": "运维干预"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["job_id"] == job.id
        assert data["action"] == "manual_retry"
        assert data["manual_action"] == "RETRY_NOW"
        assert data["current_failure_streak"] == 5  # PRESERVED — D7 contract

        db_session.expire_all()
        refreshed = db_session.get(JobInstance, job.id)
        assert refreshed.manual_action == "RETRY_NOW"
        assert refreshed.current_failure_streak == 5  # not reset
        # next_retry_at moved to ~now() (much earlier than the original 20-min future).
        # SQLite may strip tz on refresh; normalize before comparing.
        assert refreshed.next_retry_at is not None
        actual_next = refreshed.next_retry_at
        if actual_next.tzinfo is None:
            actual_next = actual_next.replace(tzinfo=timezone.utc)
        assert actual_next < future

        # audit_log written
        audit = db_session.execute(
            select(AuditLog)
            .where(AuditLog.action == "patrol_manual_retry")
            .where(AuditLog.resource_id == str(job.id))
        ).scalars().first()
        assert audit is not None
        assert audit.details["plan_run_id"] == pr.id
        assert audit.details["reason"] == "运维干预"
        assert audit.details["current_failure_streak"] == 5

    def test_manual_retry_terminal_job_returns_409(
        self, client, auth_headers, db_session, manual_chain,
    ):
        plan = manual_chain["plan"]
        pr = manual_chain["plan_run"]
        job = _make_job(
            db_session, pr.id, plan.id, manual_chain["device"].id,
            status=JobStatus.COMPLETED.value,
        )
        resp = client.post(
            f"/api/v1/plan-runs/{pr.id}/jobs/{job.id}/manual-retry",
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "terminal" in resp.json()["detail"].lower()

    def test_manual_retry_wrong_plan_run_returns_404(
        self, client, auth_headers, db_session, manual_chain,
    ):
        plan = manual_chain["plan"]
        pr = manual_chain["plan_run"]
        job = _make_job(db_session, pr.id, plan.id, manual_chain["device"].id)
        resp = client.post(
            f"/api/v1/plan-runs/{pr.id + 999}/jobs/{job.id}/manual-retry",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_manual_retry_unknown_job_returns_404(
        self, client, auth_headers, manual_chain,
    ):
        pr = manual_chain["plan_run"]
        resp = client.post(
            f"/api/v1/plan-runs/{pr.id}/jobs/999999/manual-retry",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_manual_retry_requires_auth(self, client, db_session, manual_chain):
        plan = manual_chain["plan"]
        pr = manual_chain["plan_run"]
        job = _make_job(db_session, pr.id, plan.id, manual_chain["device"].id)
        resp = client.post(f"/api/v1/plan-runs/{pr.id}/jobs/{job.id}/manual-retry")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# manual-exit
# ---------------------------------------------------------------------------


class TestManualExit:
    def test_manual_exit_sets_exit_requested_and_writes_audit(
        self, client, auth_headers, db_session, manual_chain,
    ):
        plan = manual_chain["plan"]
        pr = manual_chain["plan_run"]
        job = _make_job(
            db_session, pr.id, plan.id, manual_chain["device"].id, streak=8,
        )

        resp = client.post(
            f"/api/v1/plan-runs/{pr.id}/jobs/{job.id}/manual-exit",
            json={"reason": "设备故障"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["action"] == "manual_exit"
        assert data["manual_action"] == "EXIT_REQUESTED"
        # status remains RUNNING here — Agent will report ABORTED via /complete
        assert data["status"] == JobStatus.RUNNING.value
        assert data["current_failure_streak"] == 8

        db_session.expire_all()
        refreshed = db_session.get(JobInstance, job.id)
        assert refreshed.manual_action == "EXIT_REQUESTED"
        assert refreshed.status == JobStatus.RUNNING.value
        assert "patrol_manual_exit_pending" in (refreshed.status_reason or "")
        assert "设备故障" in (refreshed.status_reason or "")

        audit = db_session.execute(
            select(AuditLog)
            .where(AuditLog.action == "patrol_manual_exit")
            .where(AuditLog.resource_id == str(job.id))
        ).scalars().first()
        assert audit is not None
        assert audit.details["reason"] == "设备故障"
        assert audit.details["current_failure_streak"] == 8

    def test_manual_exit_does_not_overwrite_existing_status_reason(
        self, client, auth_headers, db_session, manual_chain,
    ):
        plan = manual_chain["plan"]
        pr = manual_chain["plan_run"]
        job = _make_job(db_session, pr.id, plan.id, manual_chain["device"].id)
        job.status_reason = "lease_renewer_failure"
        db_session.commit()

        resp = client.post(
            f"/api/v1/plan-runs/{pr.id}/jobs/{job.id}/manual-exit",
            json={"reason": "新的原因"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        db_session.expire_all()
        refreshed = db_session.get(JobInstance, job.id)
        # Existing status_reason preserved
        assert refreshed.status_reason == "lease_renewer_failure"
        # But manual_action still set
        assert refreshed.manual_action == "EXIT_REQUESTED"

    def test_manual_exit_terminal_returns_409(
        self, client, auth_headers, db_session, manual_chain,
    ):
        plan = manual_chain["plan"]
        pr = manual_chain["plan_run"]
        job = _make_job(
            db_session, pr.id, plan.id, manual_chain["device"].id,
            status=JobStatus.ABORTED.value,
        )
        resp = client.post(
            f"/api/v1/plan-runs/{pr.id}/jobs/{job.id}/manual-exit",
            headers=auth_headers,
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# ADR-0021 C5c — SocketIO invalidation hint emission
# ---------------------------------------------------------------------------


class TestManualActionEmitsJobStatusInvalidation:
    """The sync manual-retry / manual-exit endpoints must publish a lightweight
    JOB_STATUS event to ``plan_run:{run_id}`` so the dashboard can drop its
    cached devices/timeline payloads and refetch.  We patch the imported
    ``schedule_emit`` symbol because ``_emit_job_status_invalidation`` does a
    deferred import to avoid SocketIO bootstrapping in test environments.
    """

    def test_manual_retry_emits_job_status_to_plan_run_room(
        self, client, auth_headers, db_session, manual_chain, monkeypatch,
    ):
        captured: list[tuple[str, dict, str | None]] = []

        def fake_schedule_emit(event, data, namespace="/dashboard", room=None):
            captured.append((event, data, room))

        # Patch in the realtime module so the deferred import inside the
        # endpoint sees our fake.
        from backend.realtime import socketio_server
        monkeypatch.setattr(socketio_server, "schedule_emit", fake_schedule_emit)

        plan = manual_chain["plan"]
        pr = manual_chain["plan_run"]
        job = _make_job(
            db_session, pr.id, plan.id, manual_chain["device"].id, streak=3,
        )
        resp = client.post(
            f"/api/v1/plan-runs/{pr.id}/jobs/{job.id}/manual-retry",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text

        assert captured, "expected schedule_emit to be invoked"
        event, data, room = captured[-1]
        assert event == "job_status"
        assert room == f"plan_run:{pr.id}"
        assert data["payload"]["job_id"] == job.id
        assert data["payload"]["reason"] == "manual_retry"

    def test_manual_exit_emits_job_status_to_plan_run_room(
        self, client, auth_headers, db_session, manual_chain, monkeypatch,
    ):
        captured: list[tuple[str, dict, str | None]] = []

        def fake_schedule_emit(event, data, namespace="/dashboard", room=None):
            captured.append((event, data, room))

        from backend.realtime import socketio_server
        monkeypatch.setattr(socketio_server, "schedule_emit", fake_schedule_emit)

        plan = manual_chain["plan"]
        pr = manual_chain["plan_run"]
        job = _make_job(db_session, pr.id, plan.id, manual_chain["device"].id)
        resp = client.post(
            f"/api/v1/plan-runs/{pr.id}/jobs/{job.id}/manual-exit",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text

        assert captured, "expected schedule_emit to be invoked"
        event, data, room = captured[-1]
        assert event == "job_status"
        assert room == f"plan_run:{pr.id}"
        assert data["payload"]["job_id"] == job.id
        assert data["payload"]["reason"] == "manual_exit_pending"
