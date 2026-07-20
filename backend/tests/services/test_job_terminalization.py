"""ADR-0026 §6 — O(1) terminalization + counter aggregation tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.models.enums import JobStatus, PlanRunStatus
from backend.services.plan_run_aggregation import (
    apply_plan_run_aggregation_from_counters,
)


def _run(**kwargs):
    defaults = dict(
        status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.05,
        run_context=None,
        total_job_count=0,
        terminal_job_count=0,
        completed_job_count=0,
        failed_job_count=0,
        aborted_job_count=0,
        result_summary=None,
        ended_at=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_aggregation_from_counters_success():
    run = _run(
        total_job_count=3,
        terminal_job_count=3,
        completed_job_count=3,
    )
    with patch("backend.services.plan_run_aggregation.PlanRunStateMachine") as sm, \
         patch("backend.services.plan_run_aggregation.record_plan_run_terminal"):
        assert apply_plan_run_aggregation_from_counters(run) is True
        sm.transition.assert_called_once()
        assert run.result_summary["completed"] == 3
        assert run.result_summary["failed_only"] == 0


def test_aggregation_from_counters_waits_until_all_terminal():
    run = _run(total_job_count=3, terminal_job_count=2, completed_job_count=2)
    assert apply_plan_run_aggregation_from_counters(run) is False


def test_aggregation_from_counters_abort_override():
    run = _run(
        total_job_count=2,
        terminal_job_count=2,
        completed_job_count=2,
        run_context={"abort_requested": {"reason": "user"}},
    )
    with patch("backend.services.plan_run_aggregation.PlanRunStateMachine") as sm, \
         patch("backend.services.plan_run_aggregation.record_plan_run_terminal"):
        assert apply_plan_run_aggregation_from_counters(run) is True
        assert sm.transition.call_args[0][1] == PlanRunStatus.FAILED


def test_on_job_terminal_sync_bumps_and_aggregates():
    from backend.services.job_terminalization import on_job_terminal_sync

    run = _run(total_job_count=2, id=10)
    job1 = SimpleNamespace(
        id=1, plan_run_id=10, host_id=None, status=JobStatus.COMPLETED.value,
    )
    job2 = SimpleNamespace(
        id=2, plan_run_id=10, host_id=None, status=JobStatus.COMPLETED.value,
    )
    db = MagicMock()

    def _transition(obj, status, reason=None):
        obj.status = status.value if hasattr(status, "value") else status

    with patch(
        "backend.services.plan_chain_trigger.trigger_next_plan_sync",
    ) as trigger, patch(
        "backend.services.dedup_scan.should_trigger_dedup", return_value=False,
    ), patch(
        "backend.services.plan_run_aggregation.PlanRunStateMachine.transition",
        side_effect=_transition,
    ), patch(
        "backend.services.plan_run_aggregation.record_plan_run_terminal",
    ):
        applied1, _ = on_job_terminal_sync(job1, db, run=run)
        assert applied1 is False
        assert run.terminal_job_count == 1
        assert run.completed_job_count == 1

        applied2, status = on_job_terminal_sync(job2, db, run=run)
        assert applied2 is True
        assert run.terminal_job_count == 2
        assert status == PlanRunStatus.SUCCESS.value
        trigger.assert_called_once()


def test_recount_detects_drift():
    from backend.services.job_terminalization import recount_plan_run_counters

    run = _run(total_job_count=1, terminal_job_count=0, completed_job_count=0)
    jobs = [
        SimpleNamespace(status=JobStatus.COMPLETED.value),
        SimpleNamespace(status=JobStatus.FAILED.value),
    ]
    result = recount_plan_run_counters(run, jobs)
    assert result["drifted"] is True
    assert run.total_job_count == 2
    assert run.terminal_job_count == 2
    assert run.completed_job_count == 1
    assert run.failed_job_count == 1
