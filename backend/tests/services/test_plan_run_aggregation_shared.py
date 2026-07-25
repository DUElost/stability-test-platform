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
    """UNKNOWN is no longer terminal — aggregation waits for reconciler to
    convert UNKNOWN→FAILED.  PlanRun stays RUNNING until all jobs resolve."""
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=3, status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.5, ended_at=None, result_summary=None,
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
    """UNKNOWN no longer terminal → aggregation waits for reconciler to resolve
    UNKNOWN→FAILED before evaluating abort_requested override.  PlanRun stays
    RUNNING until all jobs reach true terminal state."""
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


# ── PlanRun-level terminal notifications ─────────────────────────────────────


def test_finalize_notifies_run_completed_on_success():
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=301,
        plan_id=9,
        status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.5,
        ended_at=None,
        result_summary=None,
    )
    jobs = [_job(JobStatus.COMPLETED), _job(JobStatus.COMPLETED)]

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ) as notify:
        assert apply_plan_run_aggregation(run, jobs) is True

    notify.assert_called_once()
    event_type, context = notify.call_args[0]
    assert event_type == "RUN_COMPLETED"
    assert context["run_id"] == 301
    assert context["plan_id"] == 9
    assert context["task_type"] == "plan"
    assert "2/2 completed" in context["error_message"]


def test_finalize_notifies_run_failed_on_threshold_breach():
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=302,
        plan_id=9,
        status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.0,
        ended_at=None,
        result_summary=None,
    )
    jobs = [_job(JobStatus.COMPLETED), _job(JobStatus.FAILED)]

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ) as notify:
        assert apply_plan_run_aggregation(run, jobs) is True

    event_type, context = notify.call_args[0]
    assert event_type == "RUN_FAILED"
    assert run.status == PlanRunStatus.FAILED.value
    assert context["run_id"] == 302


def test_finalize_notifies_run_completed_on_partial_success():
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=303,
        plan_id=9,
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

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ) as notify:
        assert apply_plan_run_aggregation(run, jobs) is True

    event_type, context = notify.call_args[0]
    assert event_type == "RUN_COMPLETED"
    assert run.status == PlanRunStatus.PARTIAL_SUCCESS.value
    assert context["run_id"] == 303


def test_empty_job_set_notifies_run_failed():
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=304,
        plan_id=9,
        status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.5,
        ended_at=None,
        result_summary=None,
    )

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ) as notify:
        assert apply_plan_run_aggregation(run, []) is True

    event_type, context = notify.call_args[0]
    assert event_type == "RUN_FAILED"
    assert run.status == PlanRunStatus.FAILED.value
    assert "no jobs" in context["error_message"]


def test_terminal_notification_failure_does_not_block_aggregation():
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = SimpleNamespace(
        id=305,
        plan_id=9,
        status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.5,
        ended_at=None,
        result_summary=None,
    )
    jobs = [_job(JobStatus.COMPLETED)]

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
        side_effect=RuntimeError("notify boom"),
    ):
        assert apply_plan_run_aggregation(run, jobs) is True

    assert run.status == PlanRunStatus.SUCCESS.value


def test_notify_plan_run_terminal_public_helper_maps_status_string():
    from backend.services.plan_run_aggregation import notify_plan_run_terminal

    run = SimpleNamespace(id=401, plan_id=7)
    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ) as notify:
        notify_plan_run_terminal(
            run,
            new_status="FAILED",
            error_message="admission_failed: device_host_drift",
        )

    event_type, context = notify.call_args[0]
    assert event_type == "RUN_FAILED"
    assert context["run_id"] == 401
    assert context["plan_id"] == 7
    assert "admission_failed" in context["error_message"]


def test_maybe_notify_risk_high_skips_non_s_levels():
    from backend.services.plan_run_aggregation import maybe_notify_risk_high

    db = MagicMock()
    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ) as notify:
        assert maybe_notify_risk_high(
            db, plan_run_id=10, risk_summary={"risk_level": "A"},
        ) is False
        assert maybe_notify_risk_high(
            db, plan_run_id=10, risk_summary={"risk_level": "B"},
        ) is False
        assert maybe_notify_risk_high(db, plan_run_id=None, risk_summary={"risk_level": "S"}) is False
        assert maybe_notify_risk_high(db, plan_run_id=10, risk_summary=None) is False
    notify.assert_not_called()
    db.execute.assert_not_called()


def test_maybe_notify_risk_high_emits_once_for_level_s():
    from backend.services.plan_run_aggregation import maybe_notify_risk_high

    pr = SimpleNamespace(id=77, plan_id=3, run_context={})
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = pr

    risk = {
        "risk_level": "S",
        "counts": {"by_type": {"SWT": 1, "ANR": 2}, "by_severity": {"S": 1}},
    }
    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ) as notify:
        assert maybe_notify_risk_high(db, plan_run_id=77, risk_summary=risk) is True
        # second call should no-op after marker written
        assert maybe_notify_risk_high(db, plan_run_id=77, risk_summary=risk) is False

    notify.assert_called_once()
    event_type, context = notify.call_args[0]
    assert event_type == "RISK_HIGH"
    assert context["run_id"] == 77
    assert context["plan_id"] == 3
    assert context["risk_level"] == "S"
    assert "risk_level=S" in context["risk_summary"]
    assert pr.run_context["risk_high_notified"]["risk_level"] == "S"
    db.commit.assert_called()
