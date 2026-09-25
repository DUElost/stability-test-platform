from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.enums import JobStatus, PlanRunStatus


def _job(status: JobStatus) -> SimpleNamespace:
    return SimpleNamespace(status=status.value)


def test_apply_plan_run_aggregation_uses_single_status_rule():
    """ADR-0048 v1.1：完成但有设备失败 → PARTIAL_SUCCESS（黄，唯一单规则入口）。"""
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=1,
        status=PlanRunStatus.RUNNING.value,
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
        "abort_requested": False,
    }


@pytest.mark.asyncio
async def test_async_plan_aggregator_delegates_to_terminalization():
    from backend.services.aggregator import PlanAggregator

    terminal_job = SimpleNamespace(plan_run_id=10)
    db = MagicMock()

    with patch(
        "backend.services.job_terminalization.on_job_terminal",
        new_callable=AsyncMock,
    ) as mock_term:
        mock_term.return_value = (False, None)
        await PlanAggregator.on_job_terminal(terminal_job, db)

    mock_term.assert_awaited_once_with(terminal_job, db)


def test_sync_plan_aggregator_delegates_to_terminalization():
    from backend.services.aggregator_sync import plan_aggregator_sync

    terminal_job = SimpleNamespace(plan_run_id=11)
    db = MagicMock()

    with patch(
        "backend.services.job_terminalization.on_job_terminal_sync",
    ) as mock_term:
        plan_aggregator_sync(terminal_job, db)

    mock_term.assert_called_once_with(terminal_job, db)


# ── abort → FAILED override（#783 裁决，v1.1 语义下唯一红来源）────────────────


def test_aggregation_aborted_forces_failed():
    """v3 §P4 保留：任一 ABORTED → FAILED——人工中止=未覆盖计划。"""
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=1, status=PlanRunStatus.RUNNING.value,
        ended_at=None, result_summary=None,
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


def test_failed_devices_yield_partial_success_never_failed():
    """ADR-0048 v1.1：设备失败（无论占比）→ PARTIAL_SUCCESS——永不判红、永不产出阈值语义。

    断言「永不为 FAILED」即阈值轴防回潮的行为面：任何占比都不许红。
    """
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    for failed_count, completed_count in [(1, 2), (2, 1), (1, 0), (5, 5)]:
        run = SimpleNamespace(
            id=2, status=PlanRunStatus.RUNNING.value,
            ended_at=None, result_summary=None,
        )
        jobs = (
            [_job(JobStatus.COMPLETED)] * completed_count
            + [_job(JobStatus.FAILED)] * failed_count
        )
        apply_plan_run_aggregation(run, jobs)

        assert run.status == PlanRunStatus.PARTIAL_SUCCESS.value, (
            f"failed={failed_count}/{len(jobs)} 应判黄不判红"
        )
        assert run.result_summary["failed_only"] == failed_count
        assert run.result_summary["failed"] == failed_count


def test_zero_failed_devices_yield_success():
    """v1.1 绿侧语义：全部 job COMPLETED → SUCCESS（黄只由 failed_only>0 触发）。"""
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=5, status=PlanRunStatus.RUNNING.value,
        ended_at=None, result_summary=None,
    )
    jobs = [_job(JobStatus.COMPLETED)] * 4
    apply_plan_run_aggregation(run, jobs)

    assert run.status == PlanRunStatus.SUCCESS.value
    assert run.result_summary["failed_only"] == 0


def test_aggregation_unknown_overrides_aborted():
    """UNKNOWN is no longer terminal — aggregation waits for reconciler to
    convert UNKNOWN→FAILED.  PlanRun stays RUNNING until all jobs resolve."""
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=3, status=PlanRunStatus.RUNNING.value,
        ended_at=None, result_summary=None,
    )
    jobs = [
        _job(JobStatus.COMPLETED), _job(JobStatus.ABORTED),
        _job(JobStatus.UNKNOWN),
    ]
    applied = apply_plan_run_aggregation(run, jobs)

    assert applied is False
    assert run.status == PlanRunStatus.RUNNING.value


def test_aggregation_only_aborted_no_failed():
    """仅 aborted 无 failed_only → FAILED."""
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=4, status=PlanRunStatus.RUNNING.value,
        ended_at=None, result_summary=None,
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
    PlanRunStatus.PARTIAL_SUCCESS.value,  # v1.1 恢复产出，终态写入守卫同样覆盖（二次聚合不得改写）
    PlanRunStatus.FAILED.value,
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

    Why: 用户主动 abort 但所有 job 在 lease 释放前已自然完成 → 聚合算出
         SUCCESS，abort 意图静默丢失。override 让 abort 始终留痕。
    """
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=201,
        status=PlanRunStatus.RUNNING.value,
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


def test_aggregation_abort_requested_overrides_failed_devices():
    """abort_requested + 设备失败批次：FAILED 且 failed 计数不被吞。"""
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=202,
        status=PlanRunStatus.RUNNING.value,
        ended_at=None,
        result_summary=None,
        run_context=_abort_requested_ctx(),
    )
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


def test_aggregation_abort_requested_waits_for_unknown():
    """UNKNOWN no longer terminal → aggregation waits for reconciler to resolve
    UNKNOWN→FAILED before evaluating abort_requested override.  PlanRun stays
    RUNNING until all jobs reach true terminal state."""
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=203,
        status=PlanRunStatus.RUNNING.value,
        ended_at=None,
        result_summary=None,
        run_context=_abort_requested_ctx(),
    )
    jobs = [_job(JobStatus.COMPLETED), _job(JobStatus.UNKNOWN)]

    applied = apply_plan_run_aggregation(run, jobs)

    assert applied is False
    assert run.status == PlanRunStatus.RUNNING.value


def test_aggregation_abort_requested_marker_with_aborted_jobs():
    """abort_requested + 真正 ABORTED job:状态已 FAILED,marker 仍正确写入。

    Why: 这是正常 abort 流(PENDING→ABORTED 触发 v3 规则 FAILED),验证
         marker 不会因为 status 已是 FAILED 就丢字段。
    """
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=204,
        status=PlanRunStatus.RUNNING.value,
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
            ended_at=None,
            result_summary=None,
            run_context=ctx,
        )
        jobs = [_job(JobStatus.COMPLETED), _job(JobStatus.COMPLETED)]

        apply_plan_run_aggregation(run, jobs)

        assert run.status == PlanRunStatus.SUCCESS.value, f"ctx={ctx!r}"
        assert run.result_summary["abort_requested"] is False, f"ctx={ctx!r}"


# ── ADR-0048 v1.1 结构断言：判定轴=abort(红)/failed_only(黄)/无(绿)，阈值不回潮 ────


def test_resolve_plan_run_status_signature_has_no_threshold_axes():
    """结构钉：判定入口参数恰好三计数（failure_threshold/total 等比例轴不可入参，防回潮）。"""
    import inspect

    from backend.services.plan_run_aggregation import _resolve_plan_run_status

    params = inspect.signature(_resolve_plan_run_status).parameters
    assert set(params) == {"failed_only", "aborted", "abort_requested"}
    # AST 检查 return 表达式：判定函数只能返回三终态（docstring 允许提及废止词）
    import ast
    tree = ast.parse(inspect.getsource(_resolve_plan_run_status).lstrip())
    returns = {
        ast.unparse(n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Return) and n.value is not None
    }
    assert returns == {
        "PlanRunStatus.FAILED",
        "PlanRunStatus.PARTIAL_SUCCESS",
        "PlanRunStatus.SUCCESS",
    }, returns
    # v1.1 防回潮：函数体内不得出现除法/比率形态（阈值轴的算式特征）
    assert not [
        n for n in ast.walk(tree) if isinstance(n, ast.BinOp)
        and isinstance(n.op, (ast.Div, ast.FloorDiv))
    ], "判定函数出现比率运算——failure_threshold 轴疑似回潮"


def test_milestone_probe_is_gone():
    """#1591-④ 里程碑豁免随阈值轴删除（防死代码回潮）。"""
    import backend.services.plan_run_aggregation as mod

    assert not hasattr(mod, "_MILESTONE_SCRIPT_NAMES")
    assert not hasattr(mod, "_failed_jobs_all_past_milestone")
