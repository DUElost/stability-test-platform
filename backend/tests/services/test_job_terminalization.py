"""ADR-0026 §6 — O(1) terminalization + counter aggregation tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


from backend.models.enums import JobStatus, PlanRunStatus
from backend.services.plan_run_aggregation import (
    apply_plan_run_aggregation_from_counters,
)


def _run(**kwargs):
    defaults = dict(
        status=PlanRunStatus.RUNNING.value,
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
        id=1,
        plan_id=1,
        total_job_count=3,
        terminal_job_count=3,
        completed_job_count=3,
    )
    with patch("backend.services.plan_run_aggregation.PlanRunStateMachine") as sm, \
         patch("backend.services.plan_run_aggregation.record_plan_run_terminal"), \
         patch("backend.services.notification_service.dispatch_notification_async") as notify:
        assert apply_plan_run_aggregation_from_counters(run) is True
        sm.transition.assert_called_once()
        assert run.result_summary["completed"] == 3
        assert run.result_summary["failed_only"] == 0
        assert notify.call_args[0][0] == "RUN_COMPLETED"


def test_aggregation_from_counters_waits_until_all_terminal():
    run = _run(total_job_count=3, terminal_job_count=2, completed_job_count=2)
    assert apply_plan_run_aggregation_from_counters(run) is False


def test_aggregation_from_counters_abort_override():
    run = _run(
        id=2,
        plan_id=1,
        total_job_count=2,
        terminal_job_count=2,
        completed_job_count=2,
        run_context={"abort_requested": {"reason": "user"}},
    )
    with patch("backend.services.plan_run_aggregation.PlanRunStateMachine") as sm, \
         patch("backend.services.plan_run_aggregation.record_plan_run_terminal"), \
         patch("backend.services.notification_service.dispatch_notification_async") as notify:
        assert apply_plan_run_aggregation_from_counters(run) is True
        assert sm.transition.call_args[0][1] == PlanRunStatus.FAILED
        assert notify.call_args[0][0] == "RUN_FAILED"


def test_on_job_terminal_sync_bumps_and_aggregates():
    from backend.services.job_terminalization import on_job_terminal_sync

    run = _run(total_job_count=2, id=10, plan_id=1)
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
    ), patch(
        "backend.services.notification_service.dispatch_notification_async",
    ):
        applied1, _ = on_job_terminal_sync(job1, db, run=run)
        assert applied1 is False
        assert run.terminal_job_count == 1
        assert run.completed_job_count == 1
        db.commit.assert_not_called()

        applied2, status = on_job_terminal_sync(job2, db, run=run)
        assert applied2 is True
        assert run.terminal_job_count == 2
        assert status == PlanRunStatus.SUCCESS.value
        # #986: parent terminal facts commit before chain trigger
        db.commit.assert_called_once()
        trigger.assert_called_once()


def test_on_job_terminal_sync_dedup_enqueue_after_commit():
    """#781/#986: enqueue_dedup 必须在 db.commit() 之后（Redis 不可回滚）。"""
    from backend.services.job_terminalization import on_job_terminal_sync

    run = _run(
        total_job_count=1, id=10, plan_id=1,
        status=PlanRunStatus.RUNNING.value,
    )
    job = SimpleNamespace(
        id=1, plan_run_id=10, host_id=None, status=JobStatus.COMPLETED.value,
    )
    db = MagicMock()
    order: list[str] = []

    def _commit():
        order.append("commit")

    def _enqueue(run_id):
        order.append("enqueue")

    def _transition(obj, status, reason=None):
        obj.status = status.value if hasattr(status, "value") else status

    db.commit.side_effect = _commit

    with patch(
        "backend.services.plan_chain_trigger.trigger_next_plan_sync",
    ), patch(
        "backend.services.dedup_scan.should_trigger_dedup", return_value=True,
    ), patch(
        "backend.services.dedup_scan.enqueue_dedup_terminal_sync",
        side_effect=_enqueue,
    ), patch(
        "backend.services.plan_run_aggregation.PlanRunStateMachine.transition",
        side_effect=_transition,
    ), patch(
        "backend.services.plan_run_aggregation.record_plan_run_terminal",
    ), patch(
        "backend.services.notification_service.dispatch_notification_async",
    ):
        applied, _ = on_job_terminal_sync(job, db, run=run)
        assert applied is True
        assert order == ["commit", "enqueue"]


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


def test_post_flash_failure_yields_success_through_terminalization(
    db_session, sample_device,
):
    """ADR-0048 端到端判据：刷机后步骤失败的批次经真实终态化路径落 SUCCESS。

    #1591-④ 里程碑豁免已随通过率轴移除——新语义下设备失败本就不改 run 状态，
    这条保留走真实 db_session 的终态化全链回归（原用例盯「漏传 db 静默不生效」，
    现在盯「任何回潮的阈值/豁免逻辑不得重新进入终态化路径」）。
    """
    from datetime import datetime, timezone

    from backend.models.job import JobInstance, StepTrace
    from backend.models.plan import Plan
    from backend.models.plan_run import PlanRun
    from backend.services.job_terminalization import on_job_terminal_sync

    now = datetime.now(timezone.utc)
    plan = Plan(name="flash-batch-plan")
    db_session.add(plan)
    db_session.flush()
    run = PlanRun(
        plan_id=plan.id,
        status=PlanRunStatus.RUNNING.value,
        plan_snapshot={"name": plan.name, "steps": [
            {"step_key": "flash", "script_name": "flash_firmware"},
            {"step_key": "oobe", "script_name": "oobe_skip"},
        ]},
        run_type="MANUAL",
    )
    db_session.add(run)
    db_session.flush()
    job = JobInstance(
        plan_run_id=run.id,
        plan_id=plan.id,
        device_id=sample_device.id,
        host_id=sample_device.host_id,
        status=JobStatus.FAILED.value,
        status_reason="lifecycle init failed: step failed in init: oobe",
        pipeline_def={"lifecycle": {}},
        started_at=now,
        ended_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(job)
    db_session.flush()
    db_session.add_all([
        StepTrace(
            job_id=job.id, step_id="flash", stage="init", status="COMPLETED",
            event_type="STEP_COMPLETE", output=None, error_message=None,
            original_ts=now, created_at=now,
        ),
        StepTrace(
            job_id=job.id, step_id="oobe", stage="init", status="FAILED",
            event_type="STEP_FAILED", output=None, error_message="OOBE skip failed",
            original_ts=now, created_at=now,
        ),
    ])
    db_session.commit()

    applied, status = on_job_terminal_sync(job, db_session, run=run)

    assert applied is True
    # ADR-0048：完成即绿——设备失败（含刷机后步骤失败）不产生 PARTIAL/FAILED
    assert status == PlanRunStatus.SUCCESS.value
