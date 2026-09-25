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

    def _transition(obj, status, reason=None):
        obj.status = status.value if hasattr(status, "value") else status

    with patch("backend.services.plan_run_aggregation.PlanRunStateMachine") as sm, \
         patch("backend.services.plan_run_aggregation.record_plan_run_terminal"), \
         patch("backend.services.notification_service.dispatch_notification_async") as notify:
        sm.transition.side_effect = _transition
        assert apply_plan_run_aggregation_from_counters(run) is True
        sm.transition.assert_called_once()
        assert run.result_summary["completed"] == 3
        assert run.result_summary["failed_only"] == 0
        # #3299：聚合器不再内联通知——RUN_* 归编排者（announce）发。
        notify.assert_not_called()
        from backend.services.plan_run_finalization import announce_parent_terminal
        announce_parent_terminal(run)
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

    def _transition(obj, status, reason=None):
        obj.status = status.value if hasattr(status, "value") else status

    with patch("backend.services.plan_run_aggregation.PlanRunStateMachine") as sm, \
         patch("backend.services.plan_run_aggregation.record_plan_run_terminal"), \
         patch("backend.services.notification_service.dispatch_notification_async") as notify:
        sm.transition.side_effect = _transition
        assert apply_plan_run_aggregation_from_counters(run) is True
        assert sm.transition.call_args[0][1] == PlanRunStatus.FAILED
        notify.assert_not_called()
        from backend.services.plan_run_finalization import announce_parent_terminal
        announce_parent_terminal(run)
        assert notify.call_args[0][0] == "RUN_FAILED"


def test_on_job_terminal_sync_writes_pending_and_defers_parent(db_session, sample_device, monkeypatch):
    """ADR-0052 D1/D2 新形状：终态事务只写 pending 标记 + 自管理提交。

    非 TESTING 语境下唤醒走 enqueue（此处以 mock 记录入队形状）；父 Run 计数
    **不得**在终态事务内变化（热行移出）。
    """
    from datetime import datetime, timezone

    from sqlalchemy import select

    from backend.models.job import JobInstance
    from backend.models.plan import Plan
    from backend.models.plan_run import PlanRun, PlanRunPendingAggregation
    from backend.services.job_terminalization import on_job_terminal_sync

    monkeypatch.delenv("TESTING", raising=False)
    enqueued: list[dict] = []
    monkeypatch.setattr(
        "backend.core.task_queue.enqueue_sync",
        lambda name, **kwargs: enqueued.append({"name": name, **kwargs}) or True,
    )

    now = datetime.now(timezone.utc)
    plan = Plan(name="pending-marker-plan")
    db_session.add(plan)
    db_session.flush()
    run = PlanRun(
        plan_id=plan.id,
        status=PlanRunStatus.RUNNING.value,
        plan_snapshot={"name": plan.name, "steps": []},
        run_type="MANUAL",
    )
    db_session.add(run)
    db_session.flush()
    job = JobInstance(
        plan_run_id=run.id, plan_id=plan.id,
        device_id=sample_device.id, host_id=sample_device.host_id,
        status=JobStatus.COMPLETED.value,
        pipeline_def={"lifecycle": {}},
        started_at=now, ended_at=now, created_at=now, updated_at=now,
    )
    db_session.add(job)
    db_session.flush()

    pending_written, _ = on_job_terminal_sync(job, db_session)
    assert pending_written is True

    db_session.expire_all()
    marks = (
        db_session.execute(
            select(PlanRunPendingAggregation.job_id).where(
                PlanRunPendingAggregation.plan_run_id == run.id
            )
        )
    ).scalars().all()
    assert list(marks) == [job.id]
    assert int(run.terminal_job_count or 0) == 0
    assert int(run.completed_job_count or 0) == 0
    assert len(enqueued) == 1
    assert enqueued[0]["name"] == "aggregate_plan_run_task"
    assert enqueued[0]["key"] == f"agg:{run.id}"
    assert enqueued[0]["plan_run_id"] == run.id

    # outbox 重放同终态：ON CONFLICT DO NOTHING ⇒ 标记不重复（幂等锚点）。
    on_job_terminal_sync(job, db_session)
    db_session.expire_all()
    marks2 = (
        db_session.execute(
            select(PlanRunPendingAggregation.job_id).where(
                PlanRunPendingAggregation.plan_run_id == run.id
            )
        )
    ).scalars().all()
    assert list(marks2) == [job.id]


def test_finalize_parent_run_sync_orders_commit_before_dedup():
    """#781/#986：聚合 applied 后**先提交父终态**再跑 chain/dedup。

    ADR-0052 后该顺序契约的载体是编排者（聚合执行器 applied 分支调用它）；
    终态事务内已无父聚合，用假会话直接钉 finalize 的顺序。
    """
    from unittest.mock import MagicMock

    from backend.services.plan_run_finalization import finalize_parent_run_sync

    run = _run(
        id=10, plan_id=1,
        total_job_count=1, terminal_job_count=1, completed_job_count=1,
        status=PlanRunStatus.SUCCESS.value,
        terminal_effects_state="pending",
    )
    db = MagicMock()
    order: list[str] = []
    db.commit.side_effect = lambda: order.append("commit")

    with patch(
        "backend.services.plan_chain_trigger.trigger_next_plan_sync",
    ), patch(
        "backend.services.dedup_scan.should_trigger_dedup", return_value=True,
    ), patch(
        "backend.services.dedup_scan.enqueue_dedup_terminal_sync",
        side_effect=lambda _rid: order.append("enqueue"),
    ), patch(
        "backend.services.notification_service.dispatch_notification_async",
    ):
        finalize_parent_run_sync(run, db, applied=True)

    assert order[0] == "commit"
    assert "enqueue" in order
    assert order.index("commit") < order.index("enqueue")
    # ADR-0052 D4：副作用块走完 → 置 done（下一次重复唤醒被此拦住）
    assert order == ["commit", "enqueue", "commit"]
    assert run.terminal_effects_state == "done"


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


def test_post_flash_failure_yields_partial_success_through_terminalization(
    db_session, sample_device,
):
    """ADR-0048 v1.1 端到端判据：刷机后步骤失败的批次经真实终态化路径落 PARTIAL_SUCCESS（黄）。

    v1.1 恢复三态：有设备失败=黄，但**永不判红**——本条保留走真实 db_session 的
    终态化全链回归，盯「设备失败被误判成 FAILED（阈值/豁免逻辑回潮）」与
    「漏传 db 静默不生效」两类回归。
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

    pending_written, _ = on_job_terminal_sync(job, db_session)
    assert pending_written is True
    # TESTING=1：唤醒内联排空 ⇒ 父终态在返回时已收敛（旧「终态即聚合」语义）
    db_session.refresh(run)

    # ADR-0048 v1.1：完成有设备失败=黄（不产生 FAILED——设备失败永不判红的内核不变）
    assert run.status == PlanRunStatus.PARTIAL_SUCCESS.value
