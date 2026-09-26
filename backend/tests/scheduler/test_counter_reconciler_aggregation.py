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
        "backend.scheduler.counter_reconciler._has_pending_aggregation",
        return_value=False,
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


def test_reconcile_emits_counter_drift_metric():
    """#77：漂移修复时按漂移列打点（Counter 自增，非漂移列不记）。"""
    from backend.core.metrics import plan_run_counter_drift_total
    from backend.scheduler.counter_reconciler import _reconcile_plan_run_counters_body

    run = SimpleNamespace(
        id=4242,
        status=PlanRunStatus.RUNNING.value,
        total_job_count=1,
        terminal_job_count=0,
        completed_job_count=0,
        failed_job_count=0,
        aborted_job_count=0,
        run_context=None,
        result_summary=None,
        ended_at=None,
        plan_id=1,
    )
    # terminal 列与 completed 列漂移（0 → 1）；failed/aborted/total 列不漂移
    jobs = [SimpleNamespace(status=JobStatus.COMPLETED.value)]

    mock_db = MagicMock()
    mock_db.execute.return_value.scalars.return_value.all.return_value = [run]
    mock_db.query.return_value.filter.return_value.all.return_value = jobs

    def _value(mode: str) -> float:
        # #1927：label 只剩 mode（plan_run_id 已收敛为日志维度）
        return plan_run_counter_drift_total.labels(
            mode=mode,
        )._value.get()

    before = {m: _value(m) for m in ("terminal", "completed", "failed", "aborted")}

    with patch(
        "backend.scheduler.counter_reconciler.SessionLocal",
        return_value=MagicMock(__enter__=lambda s: mock_db, __exit__=lambda *a: None),
    ), patch(
        "backend.scheduler.counter_reconciler._has_pending_aggregation",
        return_value=False,
    ), patch(
        "backend.scheduler.counter_reconciler.apply_plan_run_aggregation_from_counters",
        return_value=True,
    ), patch(
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
    assert _value("terminal") == before["terminal"] + 1
    assert _value("completed") == before["completed"] + 1
    assert _value("failed") == before["failed"], "非漂移列不得打点"
    assert _value("aborted") == before["aborted"], "非漂移列不得打点"


def test_reconcile_skips_run_with_pending_aggregation():
    """#3399 规格补充：仍有待聚合标记的 run 跳过——正常滞后不得记成漂移。

    聚合器还没跑时计数必然滞后；reconciler 抢跑只会误报（换条路径复发）。
    跳过的 run 既不 recount、也不 commit，等聚合器（或 pending 重放通道）处理。
    """
    from backend.core.metrics import plan_run_counter_drift_total
    from backend.scheduler.counter_reconciler import _reconcile_plan_run_counters_body

    run = SimpleNamespace(
        id=43001,
        status=PlanRunStatus.RUNNING.value,
        total_job_count=1,
        terminal_job_count=0,
        completed_job_count=0,
        failed_job_count=0,
        aborted_job_count=0,
        run_context=None,
        result_summary=None,
        ended_at=None,
        plan_id=1,
    )
    mock_db = MagicMock()
    mock_db.execute.return_value.scalars.return_value.all.return_value = [run]

    before = plan_run_counter_drift_total.labels(mode="terminal")._value.get()

    with patch(
        "backend.scheduler.counter_reconciler.SessionLocal",
        return_value=MagicMock(__enter__=lambda s: mock_db, __exit__=lambda *a: None),
    ), patch(
        "backend.scheduler.counter_reconciler._has_pending_aggregation",
        return_value=True,
    ):
        summary = _reconcile_plan_run_counters_body(batch_size=1, lookback_hours=1)

    assert summary["scanned"] == 1
    assert summary["skipped_pending"] == 1
    assert summary["drifted"] == 0
    assert summary["fixed"] == 0
    assert plan_run_counter_drift_total.labels(mode="terminal")._value.get() == before
    assert not mock_db.query.called, "跳过的 run 不应加载 jobs"
    assert run.terminal_job_count == 0, "计数保持不动，交给聚合器"
    mock_db.commit.assert_not_called()
