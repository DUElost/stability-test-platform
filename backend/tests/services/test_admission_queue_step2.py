"""ADR-0026 P1 step 2 — state machine dual-track + feature flag + V2 plumbing.

Acceptance criteria covered here (reviewer-specified):
- legal/illegal transition matrix for QUEUED/PRECHECK + dual-track FAILED
- feature gate: env flag alone is NOT enough (pump must register)
- FAILED retry: flag=0 → RUNNING (legacy), flag=1+pump → QUEUED
- QUEUED/PRECHECK abort → FAILED directly, no job recycling
- PRECHECK stale → QUEUED with backoff/attempt bookkeeping; exhausted → FAILED
- flag=0 produces no QUEUED/PRECHECK data (prepare stays RUNNING)

The rest of the criteria are covered by the untouched legacy suites running
green (flag=0 byte-for-byte behavior).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import backend.core.admission_queue as admission_queue
from backend.models.enums import PlanRunStatus
from backend.models.host import Device, Host
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services.state_machine import (
    InvalidTransitionError,
    PlanRunStateMachine,
    PLAN_RUN_VALID_TRANSITIONS,
)


# ── Transition matrix ─────────────────────────────────────────────────────────


def _run(status: str) -> SimpleNamespace:
    return SimpleNamespace(id=1, status=status)


class TestAdmissionTransitionMatrix:
    @pytest.mark.parametrize("src,dst", [
        ("QUEUED", PlanRunStatus.PRECHECK),
        ("QUEUED", PlanRunStatus.FAILED),
        ("PRECHECK", PlanRunStatus.QUEUED),
        ("PRECHECK", PlanRunStatus.RUNNING),
        ("PRECHECK", PlanRunStatus.FAILED),
        ("FAILED", PlanRunStatus.RUNNING),   # legacy retry (dual-track)
        ("FAILED", PlanRunStatus.QUEUED),    # V2 retry (dual-track)
    ])
    def test_legal(self, src, dst):
        run = _run(src)
        PlanRunStateMachine.transition(run, dst)
        assert run.status == dst.value

    @pytest.mark.parametrize("src,dst", [
        ("QUEUED", PlanRunStatus.RUNNING),        # must pass through PRECHECK
        ("QUEUED", PlanRunStatus.SUCCESS),
        ("PRECHECK", PlanRunStatus.SUCCESS),
        ("RUNNING", PlanRunStatus.QUEUED),        # running work never re-queues
        ("RUNNING", PlanRunStatus.PRECHECK),
        ("SUCCESS", PlanRunStatus.QUEUED),
        ("PARTIAL_SUCCESS", PlanRunStatus.QUEUED),
        ("DEGRADED", PlanRunStatus.QUEUED),
    ])
    def test_illegal(self, src, dst):
        run = _run(src)
        with pytest.raises(InvalidTransitionError):
            PlanRunStateMachine.transition(run, dst)
        assert run.status == src

    def test_every_status_has_matrix_entry(self):
        """A status without a matrix key would KeyError instead of raising
        InvalidTransitionError — every enum member must be present."""
        for status in PlanRunStatus:
            assert status in PLAN_RUN_VALID_TRANSITIONS


# ── Feature gate ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_pump_state():
    admission_queue.mark_queue_pump_ready(False)
    yield
    admission_queue.mark_queue_pump_ready(False)


class TestAdmissionQueueGate:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("STP_PLAN_ADMISSION_QUEUE_ENABLED", raising=False)
        assert admission_queue.admission_queue_enabled() is False

    def test_flag_alone_is_not_enough(self, monkeypatch, caplog):
        """Reviewer-required protection: env flag on + pump not registered →
        disabled, with an explicit warning (never silently strand QUEUED)."""
        import logging
        caplog.set_level(logging.WARNING)
        monkeypatch.setenv("STP_PLAN_ADMISSION_QUEUE_ENABLED", "1")
        assert admission_queue.admission_queue_flag_enabled() is True
        assert admission_queue.admission_queue_enabled() is False
        assert any(
            "pump_not_ready" in r.message for r in caplog.records
        )

    def test_flag_plus_pump_enables(self, monkeypatch):
        monkeypatch.setenv("STP_PLAN_ADMISSION_QUEUE_ENABLED", "1")
        admission_queue.mark_queue_pump_ready()
        assert admission_queue.admission_queue_enabled() is True

    def test_pump_alone_is_not_enough(self, monkeypatch):
        monkeypatch.delenv("STP_PLAN_ADMISSION_QUEUE_ENABLED", raising=False)
        admission_queue.mark_queue_pump_ready()
        assert admission_queue.admission_queue_enabled() is False


# ── DB-backed fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def failed_dispatch_run(db_session):
    """A FAILED PlanRun that is eligible for dispatch retry (no jobs)."""
    plan = Plan(name="aq-step2")
    host = Host(id="aq-h1", hostname="aq-h1", status="ONLINE")
    db_session.add_all([plan, host])
    db_session.flush()
    dev = Device(serial="aq-d1", host_id="aq-h1", status="ONLINE")
    db_session.add(dev)
    db_session.flush()
    pr = PlanRun(
        plan_id=plan.id, status="FAILED", failure_threshold=0.05,
        plan_snapshot={}, run_type="MANUAL",
        run_context={"dispatch_device_ids": [dev.id]},
        result_summary={"dispatch_failed": True, "reason": "wifi_allocation_failed"},
        ended_at=datetime.now(timezone.utc),
    )
    db_session.add(pr)
    db_session.commit()
    return pr


# ── Retry dual path ───────────────────────────────────────────────────────────


class TestRetryDualPath:
    def test_flag_off_retry_goes_running(self, db_session, failed_dispatch_run, monkeypatch):
        from backend.services.precheck.runner import retry_plan_run_dispatch
        monkeypatch.delenv("STP_PLAN_ADMISSION_QUEUE_ENABLED", raising=False)

        with patch("backend.tasks.saq_worker.enqueue_sync") as mock_enq:
            result = retry_plan_run_dispatch(
                failed_dispatch_run.id, db_session, triggered_by="pytest",
            )
        assert result["status"] == "RUNNING"
        db_session.refresh(failed_dispatch_run)
        assert failed_dispatch_run.status == "RUNNING"
        mock_enq.assert_called_once()  # legacy path re-enqueues the SAQ gate

    def test_flag_on_retry_goes_queued(self, db_session, failed_dispatch_run, monkeypatch):
        from backend.services.precheck.runner import retry_plan_run_dispatch
        monkeypatch.setenv("STP_PLAN_ADMISSION_QUEUE_ENABLED", "1")
        admission_queue.mark_queue_pump_ready()

        with patch("backend.tasks.saq_worker.enqueue_sync") as mock_enq:
            result = retry_plan_run_dispatch(
                failed_dispatch_run.id, db_session, triggered_by="pytest",
            )
        assert result["status"] == "QUEUED"
        db_session.refresh(failed_dispatch_run)
        assert failed_dispatch_run.status == "QUEUED"
        # queue bookkeeping written; execution clock untouched until admission
        assert failed_dispatch_run.enqueued_at is not None
        assert failed_dispatch_run.ended_at is None
        assert failed_dispatch_run.queue_reason is None
        # V2 does NOT drive the legacy SAQ gate — the pump owns admission
        mock_enq.assert_not_called()

    def test_flag_on_without_pump_stays_legacy(self, db_session, failed_dispatch_run, monkeypatch):
        """Env flag set but pump not registered → legacy RUNNING path."""
        from backend.services.precheck.runner import retry_plan_run_dispatch
        monkeypatch.setenv("STP_PLAN_ADMISSION_QUEUE_ENABLED", "1")
        # pump NOT marked ready (autouse fixture reset it)

        with patch("backend.tasks.saq_worker.enqueue_sync"):
            result = retry_plan_run_dispatch(
                failed_dispatch_run.id, db_session, triggered_by="pytest",
            )
        assert result["status"] == "RUNNING"


# ── Abort on QUEUED / PRECHECK ────────────────────────────────────────────────


class TestAdmissionAbort:
    @pytest.mark.parametrize("status,phase", [("QUEUED", "queued"), ("PRECHECK", "precheck")])
    def test_abort_admission_states_fails_directly(
        self, db_session, failed_dispatch_run, status, phase,
    ):
        from backend.models.job import JobInstance
        from backend.services.plan_run_abort import abort_plan_run

        pr = failed_dispatch_run
        pr.status = status  # direct write: simulate flag=1 runtime state
        db_session.commit()

        result = abort_plan_run(
            pr.id, db=db_session, reason="aborted_by_user", triggered_by="pytest",
        )
        assert result["status"] == "FAILED"
        assert result["phase"] == phase
        assert result["aborted_jobs"] == []
        assert result["released_leases"] == 0
        db_session.refresh(pr)
        assert pr.status == "FAILED"
        assert pr.result_summary["aborted"] is True
        assert db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id
        ).count() == 0


# ── Reaper: stale PRECHECK recovery ──────────────────────────────────────────


class TestAdmissionReaper:
    def _make_precheck(self, db_session, failed_dispatch_run, *, stale_seconds, attempts=0):
        pr = failed_dispatch_run
        pr.status = "PRECHECK"
        pr.precheck_started_at = datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)
        pr.enqueued_at = datetime.now(timezone.utc) - timedelta(seconds=stale_seconds + 60)
        ctx = dict(pr.run_context or {})
        if attempts:
            ctx["admission_requeue_attempts"] = attempts
        pr.run_context = ctx
        pr.ended_at = None
        db_session.commit()
        return pr

    def test_stale_precheck_requeues_with_backoff(self, db_session, failed_dispatch_run):
        from backend.scheduler.precheck_reaper import reconcile_stale_precheck_v2

        pr = self._make_precheck(db_session, failed_dispatch_run, stale_seconds=10_000)
        original_enqueued_at = pr.enqueued_at

        summary = reconcile_stale_precheck_v2(db=db_session)
        assert summary["requeued"] == 1 and summary["failed"] == 0

        db_session.refresh(pr)
        assert pr.status == "QUEUED"
        assert pr.queue_reason == "PRECHECK_STALE"
        assert pr.next_admission_at is not None
        assert pr.precheck_started_at is None
        assert pr.admission_attempt_id is None
        # aging basis preserved — enqueued_at NOT reset on requeue
        assert pr.enqueued_at == original_enqueued_at
        assert pr.run_context["admission_requeue_attempts"] == 1

    def test_exhausted_attempts_fail(self, db_session, failed_dispatch_run):
        from backend.scheduler.precheck_reaper import (
            MAX_ADMISSION_REQUEUE_ATTEMPTS,
            reconcile_stale_precheck_v2,
        )

        pr = self._make_precheck(
            db_session, failed_dispatch_run,
            stale_seconds=10_000, attempts=MAX_ADMISSION_REQUEUE_ATTEMPTS,
        )
        summary = reconcile_stale_precheck_v2(db=db_session)
        assert summary["failed"] == 1 and summary["requeued"] == 0

        db_session.refresh(pr)
        assert pr.status == "FAILED"
        assert pr.result_summary["reason"] == "admission_requeue_exhausted"

    def test_fresh_precheck_untouched(self, db_session, failed_dispatch_run):
        from backend.scheduler.precheck_reaper import reconcile_stale_precheck_v2

        pr = self._make_precheck(db_session, failed_dispatch_run, stale_seconds=1)
        summary = reconcile_stale_precheck_v2(db=db_session)
        assert summary == {"checked": 0, "requeued": 0, "failed": 0}
        db_session.refresh(pr)
        assert pr.status == "PRECHECK"

    def test_noop_when_no_precheck_rows(self, db_session):
        from backend.scheduler.precheck_reaper import reconcile_stale_precheck_v2
        summary = reconcile_stale_precheck_v2(db=db_session)
        assert summary == {"checked": 0, "requeued": 0, "failed": 0}


# ── flag=0 produces no admission-queue data ──────────────────────────────────


class TestFlagOffProducesNoQueueData:
    def test_prepare_still_creates_running(self, db_session, monkeypatch):
        """flag=0: prepare_plan_run keeps building RUNNING directly, and no
        QUEUED/PRECHECK rows appear anywhere."""
        from backend.models.plan import PlanStep
        from backend.models.script import Script
        from backend.services.plan_dispatcher_sync import prepare_plan_run

        monkeypatch.delenv("STP_PLAN_ADMISSION_QUEUE_ENABLED", raising=False)

        plan = Plan(name="aq-flagoff")
        host = Host(id="aq-h2", hostname="aq-h2", status="ONLINE")
        script = Script(
            name="noop", script_type="python", version="1.0.0",
            nfs_path="/s/noop.py", content_sha256="x", default_params={},
        )
        db_session.add_all([plan, host, script])
        db_session.flush()
        dev = Device(serial="aq-d2", host_id="aq-h2", status="ONLINE")
        db_session.add(dev)
        db_session.add(PlanStep(
            plan_id=plan.id, step_key="s", script_name="noop",
            script_version="1.0.0", stage="init", sort_order=0,
            timeout_seconds=30, retry=0,
        ))
        db_session.commit()

        pr = prepare_plan_run(
            plan_id=plan.id, device_ids=[dev.id],
            triggered_by="pytest", db=db_session, run_type="MANUAL",
        )
        assert pr.status == "RUNNING"
        assert db_session.query(PlanRun).filter(
            PlanRun.status.in_(["QUEUED", "PRECHECK"])
        ).count() == 0
