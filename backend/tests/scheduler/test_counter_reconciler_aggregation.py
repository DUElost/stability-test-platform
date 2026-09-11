"""#789 — counter reconciler must re-aggregate after fixing drift."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.models.enums import JobStatus, PlanRunStatus


def test_reconcile_aggregates_after_counter_drift_fix():
    from backend.scheduler.counter_reconciler import _reconcile_plan_run_counters_body

    run = SimpleNamespace(
        id=42,
        status=PlanRunStatus.RUNNING.value,
        total_job_count=1,
        terminal_job_count=0,
        completed_job_count=0,
        failed_job_count=0,
        aborted_job_count=0,
        failure_threshold=0.05,
        run_context=None,
        result_summary=None,
        ended_at=None,
        plan_id=1,
    )
    jobs = [SimpleNamespace(status=JobStatus.COMPLETED.value)]

    mock_db = MagicMock()
    mock_db.execute.return_value.scalars.return_value.all.return_value = [run]
    mock_db.query.return_value.filter.return_value.all.return_value = jobs

    with patch(
        "backend.scheduler.counter_reconciler.SessionLocal",
        return_value=MagicMock(__enter__=lambda s: mock_db, __exit__=lambda *a: None),
    ), patch(
        "backend.scheduler.counter_reconciler.apply_plan_run_aggregation_from_counters",
        return_value=True,
    ) as mock_agg, patch(
        "backend.services.plan_run_aggregation.PlanRunStateMachine.transition",
        side_effect=lambda obj, status, reason=None: setattr(
            obj, "status", status.value if hasattr(status, "value") else status
        ),
    ), patch(
        "backend.services.plan_run_aggregation.record_plan_run_terminal",
    ), patch(
        "backend.services.notification_service.dispatch_notification_async",
    ):
        summary = _reconcile_plan_run_counters_body(batch_size=1, lookback_hours=1)

    assert summary["drifted"] == 1
    assert summary["aggregated"] == 1
    mock_agg.assert_called_once_with(run)
    mock_db.commit.assert_called_once()
