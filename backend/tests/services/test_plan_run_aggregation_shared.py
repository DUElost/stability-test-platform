from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.enums import JobStatus, PlanRunStatus


def _job(status: JobStatus) -> SimpleNamespace:
    return SimpleNamespace(status=status.value)


def test_apply_plan_run_aggregation_uses_single_status_rule():
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=1,
        status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.5,
        ended_at=None,
        result_summary=None,
    )
    jobs = [
        _job(JobStatus.COMPLETED),
        _job(JobStatus.FAILED),
        _job(JobStatus.COMPLETED),
    ]

    applied = apply_plan_run_aggregation(run, jobs)

    assert applied is True
    assert run.status == PlanRunStatus.PARTIAL_SUCCESS.value
    assert run.ended_at is not None
    assert run.result_summary == {
        "total": 3,
        "completed": 2,
        "failed": 1,
        "failed_only": 1,
        "aborted": 0,
        "unknown": 0,
        "pass_rate": 0.6667,
    }


@pytest.mark.asyncio
async def test_async_plan_aggregator_delegates_to_shared_rule():
    from backend.services.aggregator import PlanAggregator

    terminal_job = SimpleNamespace(plan_run_id=10)
    run = SimpleNamespace(id=10, status="RUNNING")
    jobs = [_job(JobStatus.COMPLETED)]

    result = MagicMock()
    result.scalars.return_value.all.return_value = jobs

    db = MagicMock()
    db.get = AsyncMock(return_value=run)
    db.execute = AsyncMock(return_value=result)

    with patch("backend.services.aggregator.apply_plan_run_aggregation") as mock_apply:
        await PlanAggregator.on_job_terminal(terminal_job, db)

    mock_apply.assert_called_once_with(run, jobs)


def test_sync_plan_aggregator_delegates_to_shared_rule():
    from backend.services.aggregator_sync import plan_aggregator_sync

    terminal_job = SimpleNamespace(plan_run_id=11)
    run = SimpleNamespace(id=11, status="RUNNING")
    jobs = [_job(JobStatus.COMPLETED)]

    query = MagicMock()
    query.filter.return_value.all.return_value = jobs

    db = MagicMock()
    db.get.return_value = run
    db.query.return_value = query

    with patch("backend.services.aggregator_sync.apply_plan_run_aggregation") as mock_apply:
        plan_aggregator_sync(terminal_job, db)

    mock_apply.assert_called_once_with(run, jobs)


# ── v3 §P4: abort → FAILED override ─────────────────────────────────────────


def test_aggregation_aborted_overrides_partial_success():
    """v3 §P4: any ABORTED → FAILED, even if failed_only/total ≤ threshold."""
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=1, status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.5, ended_at=None, result_summary=None,
    )
    jobs = [
        _job(JobStatus.COMPLETED), _job(JobStatus.COMPLETED),
        _job(JobStatus.COMPLETED), _job(JobStatus.ABORTED),
    ]
    applied = apply_plan_run_aggregation(run, jobs)

    assert applied is True
    assert run.status == PlanRunStatus.FAILED.value
    assert run.result_summary["aborted"] == 1
    assert run.result_summary["failed_only"] == 0
    assert run.result_summary["failed"] == 1


def test_aggregation_pure_failed_below_threshold_still_partial():
    """failed_only 内 threshold 仍可落 PARTIAL_SUCCESS."""
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=2, status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.5, ended_at=None, result_summary=None,
    )
    jobs = [
        _job(JobStatus.COMPLETED), _job(JobStatus.COMPLETED),
        _job(JobStatus.FAILED),
    ]
    apply_plan_run_aggregation(run, jobs)

    assert run.status == PlanRunStatus.PARTIAL_SUCCESS.value
    assert run.result_summary["aborted"] == 0
    assert run.result_summary["failed_only"] == 1
    assert run.result_summary["failed"] == 1


def test_aggregation_unknown_overrides_aborted():
    """unknown 优先级最高 → DEGRADED."""
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=3, status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.5, ended_at=None, result_summary=None,
    )
    jobs = [
        _job(JobStatus.COMPLETED), _job(JobStatus.ABORTED),
        _job(JobStatus.UNKNOWN),
    ]
    apply_plan_run_aggregation(run, jobs)

    assert run.status == PlanRunStatus.DEGRADED.value
    assert run.result_summary["unknown"] == 1
    assert run.result_summary["aborted"] == 1


def test_aggregation_only_aborted_no_failed():
    """仅 aborted 无 failed_only → FAILED."""
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=4, status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.5, ended_at=None, result_summary=None,
    )
    jobs = [
        _job(JobStatus.COMPLETED), _job(JobStatus.COMPLETED),
        _job(JobStatus.ABORTED),
    ]
    apply_plan_run_aggregation(run, jobs)

    assert run.status == PlanRunStatus.FAILED.value
    assert run.result_summary["aborted"] == 1
    assert run.result_summary["failed_only"] == 0
    assert run.result_summary["failed"] == 1
