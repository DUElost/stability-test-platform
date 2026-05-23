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
        "abort_requested": False,
    }


@pytest.mark.asyncio
async def test_async_plan_aggregator_delegates_to_shared_rule():
    from backend.services.aggregator import PlanAggregator

    terminal_job = SimpleNamespace(plan_run_id=10)
    run = SimpleNamespace(id=10, status="RUNNING")
    jobs = [_job(JobStatus.COMPLETED)]

    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = run
    jobs_result = MagicMock()
    jobs_result.scalars.return_value.all.return_value = jobs

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[run_result, jobs_result])

    with patch("backend.services.aggregator.apply_plan_run_aggregation") as mock_apply:
        await PlanAggregator.on_job_terminal(terminal_job, db)

    mock_apply.assert_called_once_with(run, jobs)


def test_sync_plan_aggregator_delegates_to_shared_rule():
    from backend.services.aggregator_sync import plan_aggregator_sync

    terminal_job = SimpleNamespace(plan_run_id=11)
    run = SimpleNamespace(id=11, status="RUNNING")
    jobs = [_job(JobStatus.COMPLETED)]

    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = run

    query = MagicMock()
    query.filter.return_value.all.return_value = jobs

    db = MagicMock()
    db.execute.return_value = run_result
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


# ── 终态守卫:并发覆盖防御 ────────────────────────────────────────────────


@pytest.mark.parametrize("terminal_status", [
    PlanRunStatus.SUCCESS.value,
    PlanRunStatus.PARTIAL_SUCCESS.value,
    PlanRunStatus.FAILED.value,
    PlanRunStatus.DEGRADED.value,
])
def test_aggregation_skipped_when_run_already_terminal(terminal_status):
    """aggregator/abort 二次重入:run.status 已落终态时不得覆写。

    场景:两个 Job 同帧终态触发聚合 + abort 并发;第一个写者拿锁完成后,第二个
    取得锁时看到的是已落终态的 run,必须原样返回 False。
    """
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    sentinel_summary = {"sentinel": True}
    sentinel_ended_at = "sentinel-ended-at"

    run = SimpleNamespace(
        id=99,
        status=terminal_status,
        failure_threshold=0.5,
        ended_at=sentinel_ended_at,
        result_summary=sentinel_summary,
    )
    jobs = [_job(JobStatus.COMPLETED), _job(JobStatus.COMPLETED)]

    applied = apply_plan_run_aggregation(run, jobs)

    assert applied is False
    assert run.status == terminal_status
    assert run.ended_at is sentinel_ended_at
    assert run.result_summary is sentinel_summary


def test_aggregation_terminal_guard_precedes_unterminated_job_check():
    """终态守卫优先于 jobs 终态校验:即使 jobs 含 RUNNING 也直接 return False。

    Why: 否则 aggregator 在第二轮调用时若赶上某 job 处于 RUNNING 短暂窗口,
         会沿用旧分支落空返回,但无法对外区分"jobs 没全终态"与"run 已终态"
         两种语义。语义上"run 已终态"更强,优先短路。
    """
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=100,
        status=PlanRunStatus.SUCCESS.value,
        failure_threshold=0.5,
        ended_at="x",
        result_summary={"locked": True},
    )
    jobs = [_job(JobStatus.RUNNING), _job(JobStatus.COMPLETED)]

    applied = apply_plan_run_aggregation(run, jobs)

    assert applied is False
    assert run.status == PlanRunStatus.SUCCESS.value
    assert run.result_summary == {"locked": True}


# ── abort_requested 意图不被自然终态吞掉(2a/2b) ────────────────────────


def _abort_requested_ctx(reason: str = "aborted_by_user") -> dict:
    return {
        "abort_requested": {
            "at": "2026-05-23T00:00:00+00:00",
            "reason": reason,
            "triggered_by": "tester",
        }
    }


def test_aggregation_abort_requested_overrides_natural_success():
    """abort_requested + 所有 job 自然 COMPLETED:必须 override 成 FAILED。

    Why: 用户主动 abort 但所有 job 在 lease 释放前已自然完成 → natural mix
         算出 SUCCESS,abort 意图静默丢失。override 让 abort 始终留痕。
    """
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=201,
        status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.5,
        ended_at=None,
        result_summary=None,
        run_context=_abort_requested_ctx(),
    )
    jobs = [_job(JobStatus.COMPLETED), _job(JobStatus.COMPLETED)]

    applied = apply_plan_run_aggregation(run, jobs)

    assert applied is True
    assert run.status == PlanRunStatus.FAILED.value
    assert run.result_summary["abort_requested"] is True
    assert run.result_summary["aborted"] == 0  # 没有 ABORTED job
    assert run.result_summary["failed_only"] == 0


def test_aggregation_abort_requested_overrides_partial_success():
    """abort_requested + 自然 PARTIAL_SUCCESS:必须 override 成 FAILED。"""
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=202,
        status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.5,
        ended_at=None,
        result_summary=None,
        run_context=_abort_requested_ctx(),
    )
    # 1/3 failed, threshold 0.5 → 自然算 PARTIAL_SUCCESS
    jobs = [
        _job(JobStatus.COMPLETED),
        _job(JobStatus.COMPLETED),
        _job(JobStatus.FAILED),
    ]

    applied = apply_plan_run_aggregation(run, jobs)

    assert applied is True
    assert run.status == PlanRunStatus.FAILED.value
    assert run.result_summary["abort_requested"] is True
    assert run.result_summary["failed_only"] == 1


def test_aggregation_abort_requested_yields_to_degraded():
    """abort_requested + UNKNOWN 存在:DEGRADED 优先,abort override 不应触发。

    Why: UNKNOWN 表示 job 状态未知需要人工介入,abort override 强制 FAILED
         会掩盖这条调查信号。优先级 UNKNOWN > abort_requested。
    """
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=203,
        status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.5,
        ended_at=None,
        result_summary=None,
        run_context=_abort_requested_ctx(),
    )
    jobs = [_job(JobStatus.COMPLETED), _job(JobStatus.UNKNOWN)]

    applied = apply_plan_run_aggregation(run, jobs)

    assert applied is True
    assert run.status == PlanRunStatus.DEGRADED.value
    # marker 仍写入,前端可同时知道"abort 被请求过且 UNKNOWN 待调查"
    assert run.result_summary["abort_requested"] is True
    assert run.result_summary["unknown"] == 1


def test_aggregation_abort_requested_marker_with_aborted_jobs():
    """abort_requested + 真正 ABORTED job:状态已 FAILED,marker 仍正确写入。

    Why: 这是正常 abort 流(PENDING→ABORTED 触发 v3 规则 FAILED),验证
         marker 不会因为 status 已是 FAILED 就丢字段。
    """
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=204,
        status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.5,
        ended_at=None,
        result_summary=None,
        run_context=_abort_requested_ctx(),
    )
    jobs = [_job(JobStatus.COMPLETED), _job(JobStatus.ABORTED)]

    applied = apply_plan_run_aggregation(run, jobs)

    assert applied is True
    assert run.status == PlanRunStatus.FAILED.value
    assert run.result_summary["abort_requested"] is True
    assert run.result_summary["aborted"] == 1


def test_aggregation_no_run_context_attribute_safe():
    """既有 SimpleNamespace 测试不传 run_context,需保证 getattr 兜底安全。"""
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=205,
        status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.5,
        ended_at=None,
        result_summary=None,
        # 故意不设 run_context
    )
    jobs = [_job(JobStatus.COMPLETED), _job(JobStatus.COMPLETED)]

    applied = apply_plan_run_aggregation(run, jobs)

    assert applied is True
    assert run.status == PlanRunStatus.SUCCESS.value
    assert run.result_summary["abort_requested"] is False


def test_aggregation_run_context_none_treated_as_no_abort():
    """run_context 显式为 None / 空 dict 时,marker 为 False,不 override。"""
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    for ctx in (None, {}):
        run = SimpleNamespace(
            id=206,
            status=PlanRunStatus.RUNNING.value,
            failure_threshold=0.5,
            ended_at=None,
            result_summary=None,
            run_context=ctx,
        )
        jobs = [_job(JobStatus.COMPLETED), _job(JobStatus.COMPLETED)]

        apply_plan_run_aggregation(run, jobs)

        assert run.status == PlanRunStatus.SUCCESS.value, f"ctx={ctx!r}"
        assert run.result_summary["abort_requested"] is False, f"ctx={ctx!r}"
