# -*- coding: utf-8 -*-
"""#3299 — plan_run_finalization（终态副作用唯一编排者）的归属与行为测试。

覆盖自 ``plan_run_aggregation`` / ``post_completion`` 迁入的副作用：
RUN_* 通知、RISK_HIGH、#1082 报告缓存刷新、链恢复入口。并反向钉住边界：
聚合器 ``apply_*`` **不再**发通知/刷新（那正是五模块环的闭合边形态）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.models.enums import JobStatus, PlanRunStatus


def _job(status: JobStatus) -> SimpleNamespace:
    return SimpleNamespace(status=status.value)


def _run(ns_id: int = 301, plan_id: int = 9) -> SimpleNamespace:
    return SimpleNamespace(
        id=ns_id,
        plan_id=plan_id,
        status=PlanRunStatus.RUNNING.value,
        ended_at=None,
        result_summary=None,
        run_context=None,
    )


def _apply_and_announce(run, jobs, *, no_jobs=False):
    """新归属下的「终态反应」全链：聚合器算完 → 编排者 announce。"""
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation
    from backend.services.plan_run_finalization import announce_parent_terminal

    applied = apply_plan_run_aggregation(run, jobs)
    if applied:
        announce_parent_terminal(run, no_jobs=no_jobs)
    return applied


# ── RUN_* 通知（自 test_plan_run_aggregation_shared 迁入，#3299）──────────────


def test_terminal_notify_run_completed_on_success():
    run = _run(301)
    jobs = [_job(JobStatus.COMPLETED), _job(JobStatus.COMPLETED)]

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ) as notify:
        assert _apply_and_announce(run, jobs) is True

    notify.assert_called_once()
    event_type, context = notify.call_args[0]
    assert event_type == "RUN_COMPLETED"
    assert context["run_id"] == 301
    assert context["plan_id"] == 9
    assert context["task_type"] == "plan"
    assert "2/2 completed" in context["error_message"]


def test_terminal_notify_run_completed_despite_failed_devices():
    """ADR-0048 v1.1：黄色 PARTIAL 归 RUN_COMPLETED 侧——告警信噪比仍属执行链语义。"""
    run = _run(302)
    jobs = [_job(JobStatus.COMPLETED), _job(JobStatus.FAILED)]

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ) as notify:
        assert _apply_and_announce(run, jobs) is True

    event_type, context = notify.call_args[0]
    assert event_type == "RUN_COMPLETED"
    assert run.status == PlanRunStatus.PARTIAL_SUCCESS.value
    assert context["run_id"] == 302
    assert "1 failed" in context["error_message"]  # 失败台数在通知里可见


def test_terminal_notify_run_failed_on_aborted_devices():
    run = _run(303)
    jobs = [_job(JobStatus.COMPLETED), _job(JobStatus.ABORTED)]

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ) as notify:
        assert _apply_and_announce(run, jobs) is True

    event_type, context = notify.call_args[0]
    assert event_type == "RUN_FAILED"
    assert run.status == PlanRunStatus.FAILED.value
    assert context["run_id"] == 303


def test_empty_job_set_notifies_run_failed():
    run = _run(304)

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ) as notify:
        assert _apply_and_announce(run, [], no_jobs=True) is True

    event_type, context = notify.call_args[0]
    assert event_type == "RUN_FAILED"
    assert run.status == PlanRunStatus.FAILED.value
    assert "no jobs" in context["error_message"]


def test_terminal_notification_failure_does_not_block_aggregation():
    """best-effort：通知抛错必须被编排者吞掉，终态事实不受影响。"""
    run = _run(305)
    jobs = [_job(JobStatus.COMPLETED)]

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
        side_effect=RuntimeError("notify boom"),
    ):
        assert _apply_and_announce(run, jobs) is True

    assert run.status == PlanRunStatus.SUCCESS.value


def test_terminal_notify_message_text_is_exact():
    """RUN_* 文案逐字钉住（#3299 前由 ``_finalize_plan_run`` 以 ``new_status.value`` 拼接）。"""
    run = _run(306)
    jobs = [_job(JobStatus.COMPLETED), _job(JobStatus.COMPLETED)]

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ) as notify:
        assert _apply_and_announce(run, jobs) is True

    _, context = notify.call_args[0]
    assert context["error_message"] == "PlanRun SUCCESS: 2/2 completed, 0 failed"


def test_terminal_message_renders_enum_status_as_value():
    """``run.status`` 若为 ``PlanRunStatus`` 成员，文案仍渲染为 ``SUCCESS`` 而非 ``PlanRunStatus.SUCCESS``。"""
    from backend.services.plan_run_finalization import announce_parent_terminal

    run = _run(307)
    run.status = PlanRunStatus.SUCCESS
    run.result_summary = {"total": 3, "completed": 3, "failed": 0}
    empty = _run(308)
    empty.status = PlanRunStatus.FAILED

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ) as notify, patch(
        "backend.services.plan_run_finalization.schedule_report_cache_refresh",
    ):
        announce_parent_terminal(run)
        announce_parent_terminal(empty, no_jobs=True)

    messages = [call.args[1]["error_message"] for call in notify.call_args_list]
    assert messages == [
        "PlanRun SUCCESS: 3/3 completed, 0 failed",
        "PlanRun FAILED: no jobs were created for this plan",
    ]


def test_notify_plan_run_terminal_public_helper_maps_status_string():
    from backend.services.plan_run_finalization import notify_plan_run_terminal

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


# ── 边界反证：聚合器不再携带副作用（#3299 收口的机读形态）────────────────────


def test_apply_plan_run_aggregation_is_side_effect_free():
    """``apply_*`` 只算事实：不发通知、不调度刷新。

    钉住 #3299 的归属边界——内联通知/刷新曾住在 ``_finalize_plan_run``，
    其「刷新」半边正是 C5 基线里 ``plan_run_aggregation -> post_completion``
    环边；复发即红。
    """
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation

    run = _run(306)
    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ) as notify, patch(
        "backend.services.plan_run_finalization.schedule_report_cache_refresh",
    ) as schedule:
        assert apply_plan_run_aggregation(run, [_job(JobStatus.COMPLETED)]) is True

    notify.assert_not_called()
    schedule.assert_not_called()
    assert run.result_summary is not None  # 事实仍照常落列


# ── finalize_parent_run_*（原 job_terminalization._post_aggregation_side_effects_*）


def test_finalize_parent_run_skips_when_not_applied():
    """applied=False → 什么都不发生（不 commit、不发通知）。"""
    from backend.services.plan_run_finalization import finalize_parent_run_sync

    run = _run(307)
    db = MagicMock()
    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ) as notify:
        finalize_parent_run_sync(run, db, applied=False)
    notify.assert_not_called()
    db.commit.assert_not_called()


def test_finalize_parent_run_sync_notifies_before_commit_then_chains():
    """#986 顺序：RUN_* 通知/刷新（announce）→ commit → 链触发 → dedup。"""
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation
    from backend.services.plan_run_finalization import finalize_parent_run_sync

    run = _run(308)
    jobs = [_job(JobStatus.COMPLETED)]
    order: list[str] = []

    def _commit():
        order.append("commit")

    db = MagicMock()
    db.commit.side_effect = _commit

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
        side_effect=lambda *a, **k: order.append("notify"),
    ), patch(
        "backend.services.plan_chain_trigger.trigger_next_plan_sync",
        side_effect=lambda *a, **k: order.append("chain"),
    ), patch(
        "backend.services.dedup_scan.should_trigger_dedup", return_value=True,
    ), patch(
        "backend.services.dedup_scan.enqueue_dedup_terminal_sync",
        side_effect=lambda *a, **k: order.append("dedup"),
    ), patch(
        "backend.services.plan_run_finalization.schedule_report_cache_refresh",
    ):
        applied = apply_plan_run_aggregation(run, jobs)
        assert applied is True
        finalize_parent_run_sync(run, db, applied)

    assert order == ["notify", "commit", "chain", "dedup"]


def test_finalize_parent_run_sync_emits_plan_run_status_after_commit():
    """ADR-0052 D1 后父终态由聚合者判定：``plan_run_status`` 推送由编排者在提交**之后**补发。"""
    from backend.services.plan_run_aggregation import apply_plan_run_aggregation
    from backend.services.plan_run_finalization import finalize_parent_run_sync

    run = _run(309)
    order: list[object] = []
    db = MagicMock()
    db.commit.side_effect = lambda: order.append("commit")

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ), patch(
        "backend.services.plan_chain_trigger.trigger_next_plan_sync",
    ), patch(
        "backend.services.dedup_scan.should_trigger_dedup", return_value=False,
    ), patch(
        "backend.services.plan_run_finalization.schedule_report_cache_refresh",
    ), patch(
        "backend.services.plan_run_finalization.emit_plan_run_status",
        side_effect=lambda run_id, status: order.append(("emit", run_id, status)),
    ):
        assert apply_plan_run_aggregation(run, [_job(JobStatus.COMPLETED)]) is True
        finalize_parent_run_sync(run, db, True)

    assert order[:2] == ["commit", ("emit", 309, "SUCCESS")]


async def test_finalize_parent_run_async_emits_plan_run_status_after_commit():
    from unittest.mock import AsyncMock

    from backend.services.plan_run_finalization import finalize_parent_run_async

    run = _run(310)
    run.status = PlanRunStatus.FAILED  # 枚举成员也须渲染为 value
    order: list[object] = []
    db = MagicMock()
    db.commit = AsyncMock(side_effect=lambda: order.append("commit"))

    with patch(
        "backend.services.plan_run_finalization.announce_parent_terminal",
    ), patch(
        "backend.services.plan_chain_trigger.trigger_next_plan", new=AsyncMock(),
    ), patch(
        "backend.services.dedup_scan.should_trigger_dedup", return_value=False,
    ), patch(
        "backend.services.plan_run_finalization.emit_plan_run_status",
        side_effect=lambda run_id, status: order.append(("emit", run_id, status)),
    ):
        await finalize_parent_run_async(run, db, True)

    assert order[:2] == ["commit", ("emit", 310, "FAILED")]


def test_finalize_parent_run_skips_emit_when_not_applied():
    from backend.services.plan_run_finalization import finalize_parent_run_sync

    with patch(
        "backend.services.plan_run_finalization.emit_plan_run_status",
    ) as emit:
        finalize_parent_run_sync(_run(311), MagicMock(), False)
    emit.assert_not_called()


def test_emit_plan_run_status_payload_matches_broadcast():
    """载荷与 ``broadcast_plan_run_status`` 同形：事件名 / namespace / room / type / payload。"""
    from backend.services.plan_run_events import emit_plan_run_status

    with patch("backend.realtime.socketio_server.schedule_emit") as emit:
        emit_plan_run_status(312, "PARTIAL_SUCCESS")

    event, data = emit.call_args.args
    assert event == "plan_run_status"
    assert emit.call_args.kwargs == {"namespace": "/dashboard", "room": "plan_run:312"}
    assert data["type"] == "PLAN_RUN_STATUS"
    assert data["payload"] == {"status": "PARTIAL_SUCCESS"}
    assert "timestamp" in data


# ── RISK_HIGH（自 plan_run_aggregation 迁入）────────────────────────────────


def test_maybe_notify_risk_high_skips_non_s_levels():
    from backend.services.plan_run_finalization import maybe_notify_risk_high

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
        assert maybe_notify_risk_high(
            db, plan_run_id=None, risk_summary={"risk_level": "S"},
        ) is False
        assert maybe_notify_risk_high(db, plan_run_id=10, risk_summary=None) is False
    notify.assert_not_called()
    db.execute.assert_not_called()


def test_maybe_notify_risk_high_emits_once_for_level_s():
    from backend.services.plan_run_finalization import maybe_notify_risk_high

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
    assert "ANR=2, SWT=1" in context["risk_summary"]
    assert pr.run_context["risk_high_notified"]["risk_level"] == "S"
    db.commit.assert_called()


# ── #1082 报告缓存刷新（自 post_completion 迁入）────────────────────────────


def test_schedule_report_cache_refresh_gating(monkeypatch):
    """调度门控：TESTING=1（pytest）跳过；非测试环境经线程池提交。"""
    from backend.services import plan_run_finalization

    submitted: list = []

    def fake_submit(fn, *args, **kwargs):
        submitted.append((fn, args))

    monkeypatch.setattr(
        "backend.core.thread_pool.submit", fake_submit,
    )
    # 非 TESTING：提交刷新任务
    monkeypatch.delenv("TESTING", raising=False)
    plan_run_finalization.schedule_report_cache_refresh(77)
    assert len(submitted) == 1
    fn, args = submitted[0]
    assert fn is plan_run_finalization.refresh_report_cache_for_plan_run
    assert args == (77,)

    # TESTING=1：跳过（避免后台线程与 TRUNCATE 隔离竞争）
    monkeypatch.setenv("TESTING", "1")
    plan_run_finalization.schedule_report_cache_refresh(78)
    assert len(submitted) == 1


def test_refresh_report_cache_for_plan_run_recomputes(db_session, sample_device, monkeypatch):
    """PlanRun 终态刷新：重算全部已后处理 job 的缓存并更新生成时刻。"""
    from backend.models.enums import DeviceStatus
    from backend.models.host import Device
    from backend.models.job import JobInstance
    from backend.models.plan import Plan
    from backend.models.plan_run import PlanRun
    from backend.services.plan_run_finalization import (
        refresh_report_cache_for_plan_run,
    )

    plan = Plan(name="rc-plan")
    db_session.add(plan)
    db_session.flush()
    pr = PlanRun(
        plan_id=plan.id, status="RUNNING", run_type="MANUAL",
        plan_snapshot={}, started_at=datetime.now(timezone.utc),
    )
    db_session.add(pr)
    db_session.flush()

    devices = []
    for i in range(3):
        dev = Device(
            serial=f"rc-dev-{i}", host_id=sample_device.host_id,
            status=DeviceStatus.ONLINE.value,
            last_seen=datetime.now(timezone.utc), adb_connected=True,
            adb_state="device", battery_level=80, temperature=35,
        )
        db_session.add(dev)
        devices.append(dev)
    db_session.flush()

    jobs = []
    for dev, processed in ((devices[0], True), (devices[1], True), (devices[2], False)):
        job = JobInstance(
            plan_id=plan.id, plan_run_id=pr.id,
            device_id=dev.id, host_id=sample_device.host_id,
            status="COMPLETED", pipeline_def={"lifecycle": {}},
        )
        if processed:
            job.report_json = {"risk_summary": None, "stale": True}
            job.post_processed_at = datetime.now(timezone.utc)
        db_session.add(job)
        if processed:
            jobs.append(job)
    db_session.commit()
    old_ts = {j.id: j.post_processed_at for j in jobs}

    calls = []

    def fake_compose(db, job_id):
        calls.append(job_id)
        return SimpleNamespace(model_dump=lambda mode="json": {
            "risk_summary": {"risk_level": "S"},
        })

    monkeypatch.setattr(
        "backend.services.report_service.compose_run_report", fake_compose,
    )

    refreshed = refresh_report_cache_for_plan_run(pr.id)

    assert refreshed == 2
    assert sorted(calls) == sorted(j.id for j in jobs)
    for j in jobs:
        db_session.expire(j)
        assert j.report_json["risk_summary"]["risk_level"] == "S"
        assert "stale" not in j.report_json
        assert j.post_processed_at > old_ts[j.id]


# ── 链恢复入口 ──────────────────────────────────────────────────────────────


def test_recover_chain_trigger_delegates_to_plan_chain_trigger():
    """post_completion 的链式修复经编排者恢复入口，委托给 plan_chain_trigger。"""
    from backend.services.plan_run_finalization import recover_chain_trigger

    db = MagicMock()
    with patch(
        "backend.services.plan_chain_trigger.reconcile_chain_trigger_sync",
    ) as reconcile:
        recover_chain_trigger(42, db)
    reconcile.assert_called_once_with(42, db)


# ── #3077 / #3066 A半：终态可见性信号 ────────────────────────────────────────


def test_zero_output_signal_warning_when_first_window():
    from backend.services import plan_run_finalization as fin

    run = SimpleNamespace(
        id=601, plan_id=9, status=PlanRunStatus.SUCCESS.value,
        total_job_count=25, completed_job_count=0, run_context=None,
    )
    with patch.object(fin, "_prev_window_zero_output", return_value=False), \
         patch("backend.core.metrics.plan_run_zero_output_total") as metric:
        fin._emit_zero_output_signal(run)
    metric.labels.assert_called_once_with(level="warning")
    metric.labels.return_value.inc.assert_called_once()


def test_zero_output_signal_critical_on_consecutive_window():
    from backend.services import plan_run_finalization as fin

    run = SimpleNamespace(
        id=602, plan_id=9, status=PlanRunStatus.SUCCESS.value,
        total_job_count=25, completed_job_count=0, run_context=None,
    )
    with patch.object(fin, "_prev_window_zero_output", return_value=True), \
         patch("backend.core.metrics.plan_run_zero_output_total") as metric:
        fin._emit_zero_output_signal(run)
    metric.labels.assert_called_once_with(level="critical")


def test_zero_output_signal_gated_by_threshold_and_nonzero():
    from backend.services import plan_run_finalization as fin

    small = SimpleNamespace(id=603, plan_id=9, status=PlanRunStatus.SUCCESS.value,
                            total_job_count=10, completed_job_count=0, run_context=None)
    partial = SimpleNamespace(id=604, plan_id=9, status=PlanRunStatus.SUCCESS.value,
                              total_job_count=25, completed_job_count=5, run_context=None)
    with patch("backend.core.metrics.plan_run_zero_output_total") as metric, \
         patch.object(fin, "_prev_window_zero_output") as prev:
        fin._emit_zero_output_signal(small)
        fin._emit_zero_output_signal(partial)
    metric.labels.assert_not_called()
    prev.assert_not_called()


def test_parent_chain_gap_signal_fires_for_failed_parent():
    from backend.services import plan_run_finalization as fin

    run = SimpleNamespace(
        id=605, plan_id=9, status=PlanRunStatus.FAILED.value,
        run_context=None, root_plan_run_id=None, chain_index=0,
    )
    with patch.object(fin, "_chain_gap_missing_for_run", return_value=3), \
         patch("backend.services.plan_chain_trigger._mark_chain_gap_signaled", return_value=True) as mark, \
         patch("backend.services.plan_chain_trigger._fire_chain_gap_signal") as fire:
        fin._emit_parent_chain_gap_signal(run)
    mark.assert_called_once()
    assert mark.call_args.kwargs["reason"] == "parent_failed"
    fire.assert_called_once()


def test_parent_chain_gap_signal_skips_triggerable_and_deduped():
    from backend.services import plan_run_finalization as fin

    success_run = SimpleNamespace(id=606, plan_id=9, status=PlanRunStatus.SUCCESS.value,
                                  run_context=None)
    deduped = SimpleNamespace(
        id=607, plan_id=9, status=PlanRunStatus.FAILED.value,
        run_context={"chain_visibility_gap_signaled": {"reason": "parent_failed"}},
    )
    with patch.object(fin, "_chain_gap_missing_for_run") as lookup, \
         patch("backend.services.plan_chain_trigger._fire_chain_gap_signal") as fire:
        fin._emit_parent_chain_gap_signal(success_run)
        fin._emit_parent_chain_gap_signal(deduped)
    lookup.assert_not_called()
    fire.assert_not_called()
