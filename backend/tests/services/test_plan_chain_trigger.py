"""Plan chain trigger — dispatch failure rollback (sync + async + unexpected)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import update

from backend.models.enums import JobStatus
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.services.plan_chain_trigger import (
    reconcile_chain_trigger_sync,
    trigger_next_plan,
    trigger_next_plan_sync,
)
from backend.services.plan_dispatcher_core import PlanDispatchError
from backend.scheduler.plan_chain_reconciler import reconcile_plan_chains


def _seed_successful_parent_run(db_session, sample_device, sample_host):
    child_plan = Plan(name="chain-child")
    parent_plan = Plan(name="chain-parent", next_plan_id=None)
    db_session.add_all([parent_plan, child_plan])
    db_session.flush()
    parent_plan.next_plan_id = child_plan.id

    pr = PlanRun(
        plan_id=parent_plan.id,
        status="SUCCESS",
        plan_snapshot={
            "plan": {
                "id": parent_plan.id,
                "next_plan_id": child_plan.id,
            },
            "steps": [],
        },
        run_type="MANUAL",
        triggered_by="test",
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(pr)
    db_session.flush()

    job = JobInstance(
        plan_run_id=pr.id,
        plan_id=parent_plan.id,
        device_id=sample_device.id,
        host_id=sample_host.id,
        status=JobStatus.COMPLETED.value,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(pr)
    return pr


class TestPlanChainTriggerRollback:
    def test_child_row_and_parent_flag_commit_before_admission(
        self, db_session, sample_device, sample_host, sample_script,
    ):
        parent = _seed_successful_parent_run(
            db_session, sample_device, sample_host,
        )
        child_plan = db_session.get(
            Plan, parent.plan_snapshot["plan"]["next_plan_id"],
        )
        db_session.add(
            PlanStep(
                plan_id=child_plan.id,
                stage="init",
                sort_order=0,
                step_key="child-init",
                script_name=sample_script[0].name,
                script_version=sample_script[0].version,
                timeout_seconds=300,
                enabled=True,
            )
        )
        db_session.commit()

        child = trigger_next_plan_sync(parent, db_session)

        assert child is not None
        db_session.expire_all()
        stored_parent = db_session.get(PlanRun, parent.id)
        stored_child = db_session.get(PlanRun, child.id)
        assert stored_parent.next_plan_triggered is True
        assert stored_child.parent_plan_run_id == parent.id
        assert stored_child.status == "QUEUED"
        assert stored_child.run_context["dispatch_state"]["status"] == "queued"
        assert stored_child.run_context["dispatch_state"]["enqueue_key"] is None
        assert (
            db_session.query(JobInstance)
            .filter(JobInstance.plan_run_id == stored_child.id)
            .count()
            == 0
        )

    def test_dispatch_failure_rolls_back_next_plan_triggered(
        self, db_session, sample_device, sample_host,
    ):
        pr = _seed_successful_parent_run(db_session, sample_device, sample_host)

        with patch(
            "backend.services.plan_chain_trigger.prepare_plan_run",
            side_effect=PlanDispatchError("devices unavailable"),
        ):
            result = trigger_next_plan_sync(pr, db_session)

        assert result is None
        db_session.expire_all()
        refreshed = db_session.get(PlanRun, pr.id)
        assert refreshed.next_plan_triggered is False
        assert refreshed.result_summary is not None
        assert "chain_dispatch_failed" in refreshed.result_summary
        assert "devices unavailable" in refreshed.result_summary["chain_dispatch_failed"]["error"]
        # child 未创建 → 允许下次 aggregator 重试
        assert refreshed.result_summary["chain_dispatch_failed"]["child_already_created"] is False

    def _settle_off(self, monkeypatch):
        from types import SimpleNamespace
        monkeypatch.setattr(
            "backend.services.plan_chain_trigger.get_scheduler_settings",
            lambda: SimpleNamespace(chain_trigger_settle_seconds=0),
        )

    def test_uncommitted_parent_terminalization_survives_chain_prepare_failure(
        self, db_session, sample_device, sample_host, monkeypatch,
    ):
        # #2755：本例断言 prepare 失败路径——把 settle 窗关掉直达 prepare。
        self._settle_off(monkeypatch)
        """#986: complete→aggregate 尚未提交时，子 prepare 失败不得回滚父终态。

        真实调用链：Job 在同会话标为 COMPLETED 后直接 ``on_job_terminal_sync``
        （不先 commit），聚合触发 ``trigger_next_plan_sync``；prepare 抛错后的
        ``session.rollback()`` 不得撤销 Job/PlanRun 终态与计数。
        """
        from backend.services.job_terminalization import on_job_terminal_sync

        child_plan = Plan(name="chain-child-986")
        parent_plan = Plan(name="chain-parent-986")
        db_session.add_all([parent_plan, child_plan])
        db_session.flush()
        parent_plan.next_plan_id = child_plan.id

        pr = PlanRun(
            plan_id=parent_plan.id,
            status="RUNNING",
            plan_snapshot={
                "plan": {
                    "id": parent_plan.id,
                    "next_plan_id": child_plan.id,
                },
                "steps": [],
            },
            run_type="MANUAL",
            triggered_by="test",
            started_at=datetime.now(timezone.utc),
            total_job_count=1,
            terminal_job_count=0,
            completed_job_count=0,
            failed_job_count=0,
            aborted_job_count=0,
        )
        db_session.add(pr)
        db_session.flush()
        job = JobInstance(
            plan_run_id=pr.id,
            plan_id=parent_plan.id,
            device_id=sample_device.id,
            host_id=sample_host.id,
            status=JobStatus.RUNNING.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        )
        db_session.add(job)
        db_session.commit()

        job.status = JobStatus.COMPLETED.value
        job.ended_at = datetime.now(timezone.utc)
        # Intentionally no commit — mirrors complete_job before outer commit.

        with patch(
            "backend.services.plan_chain_trigger.prepare_plan_run",
            side_effect=PlanDispatchError("child plan has no steps"),
        ), patch(
            "backend.services.notification_service.dispatch_notification_async",
        ), patch(
            "backend.services.dedup_scan.should_trigger_dedup",
            return_value=False,
        ):
            applied, status = on_job_terminal_sync(job, db_session)

        assert applied is True
        assert status == "SUCCESS"
        db_session.expire_all()
        stored_job = db_session.get(JobInstance, job.id)
        stored_run = db_session.get(PlanRun, pr.id)
        assert stored_job.status == JobStatus.COMPLETED.value
        assert stored_run.status == "SUCCESS"
        assert stored_run.terminal_job_count == 1
        assert stored_run.completed_job_count == 1
        assert stored_run.next_plan_triggered is False
        assert "chain_dispatch_failed" in (stored_run.result_summary or {})
        assert "child plan has no steps" in (
            stored_run.result_summary["chain_dispatch_failed"]["error"]
        )

    def test_sync_unexpected_exception_also_rolls_back(
        self, db_session, sample_device, sample_host,
    ):
        """#4: 非 PlanDispatchError 系统异常(网络/SAQ enqueue)也必须 rollback,
        否则 next_plan_triggered=True 会让后续 aggregator 重试因 CAS 失败链断。
        """
        pr = _seed_successful_parent_run(db_session, sample_device, sample_host)

        with patch(
            "backend.services.plan_chain_trigger.prepare_plan_run",
            side_effect=RuntimeError("SAQ enqueue failed: redis timeout"),
        ):
            result = trigger_next_plan_sync(pr, db_session)

        assert result is None
        db_session.expire_all()
        refreshed = db_session.get(PlanRun, pr.id)
        assert refreshed.next_plan_triggered is False, "未预期异常也必须 rollback flag"
        assert "chain_dispatch_failed" in refreshed.result_summary
        assert "SAQ enqueue failed" in refreshed.result_summary["chain_dispatch_failed"]["error"]

    def test_rollback_preserves_flag_when_child_plan_run_already_exists(
        self, db_session, sample_device, sample_host,
    ):
        """ADR-0021 dispatch gate: prepare_plan_run 已写 child PlanRun 后 gate 失败,
        rollback 不能 reset parent.next_plan_triggered,否则下次 aggregator 重试会撞
        ``uniq_plan_run_chain_child`` partial unique index 死循环。
        """
        pr = _seed_successful_parent_run(db_session, sample_device, sample_host)
        # parent 的 next_plan 已经在 _seed 内创建,取出来手动 INSERT 一个 FAILED child
        parent = db_session.get(Plan, pr.plan_id)
        next_plan_id = parent.next_plan_id
        existing_child = PlanRun(
            plan_id=next_plan_id,
            status="FAILED",
            plan_snapshot={"plan_id": next_plan_id},
            run_type="CHAIN",
            triggered_by="test",
            parent_plan_run_id=pr.id,
            chain_index=1,
            started_at=datetime.now(timezone.utc),
            ended_at=datetime.now(timezone.utc),
            result_summary={"dispatch_failed": True, "reason": "wifi_allocation_failed"},
        )
        db_session.add(existing_child)
        db_session.commit()

        result = trigger_next_plan_sync(pr, db_session)

        assert result.id == existing_child.id
        db_session.expire_all()
        refreshed = db_session.get(PlanRun, pr.id)
        # 关键不变量:child 已存在 → flag 保持 True,防止下次 aggregator INSERT 撞 unique
        assert refreshed.next_plan_triggered is True, (
            "child PlanRun 已落库,parent flag 必须保持 True 防止重试撞 unique 索引"
        )


class TestPlanChainInterruptedFlagReconciliation:
    def test_orphaned_true_flag_is_reset_before_redispatch(
        self, db_session, sample_device, sample_host,
    ):
        """CAS 已提交但 child 未创建的中断态，必须先清 flag 才能重新触发。"""
        parent = _seed_successful_parent_run(db_session, sample_device, sample_host)
        parent.next_plan_triggered = True
        db_session.commit()
        sentinel_child = SimpleNamespace(id=9876)

        def _redispatch(refreshed_parent, db, **_kw):
            # #2755 后补偿 helper 会带 respect_settle=True 调用——本例只断言
            # 「flag 先清再重派」的顺序，窗语义由 TestChainTriggerSettleWindow 覆盖。
            assert refreshed_parent.next_plan_triggered is False
            assert db.get(PlanRun, parent.id).next_plan_triggered is False
            return sentinel_child

        with patch(
            "backend.services.plan_chain_trigger.trigger_next_plan_sync",
            side_effect=_redispatch,
        ) as dispatch:
            result = reconcile_chain_trigger_sync(parent.id, db_session)

        assert result is sentinel_child
        dispatch.assert_called_once()

    def test_existing_child_repairs_false_parent_flag_without_redispatch(
        self, db_session, sample_device, sample_host,
    ):
        """child 已落库但 parent flag 未提交时，以 durable child 行为准修复为 True。"""
        parent = _seed_successful_parent_run(db_session, sample_device, sample_host)
        parent_plan = db_session.get(Plan, parent.plan_id)
        child = PlanRun(
            plan_id=parent_plan.next_plan_id,
            status="RUNNING",
            plan_snapshot={"plan_id": parent_plan.next_plan_id},
            run_type="CHAIN",
            triggered_by="test",
            parent_plan_run_id=parent.id,
            root_plan_run_id=parent.id,
            chain_index=1,
            started_at=datetime.now(timezone.utc),
        )
        parent.next_plan_triggered = False
        db_session.add(child)
        db_session.commit()

        with patch(
            "backend.services.plan_chain_trigger.trigger_next_plan_sync",
        ) as dispatch:
            result = reconcile_chain_trigger_sync(parent.id, db_session)

        assert result.id == child.id
        db_session.expire_all()
        assert db_session.get(PlanRun, parent.id).next_plan_triggered is True
        dispatch.assert_not_called()

    def test_scheduler_repairs_durable_child_parent_flag(
        self, db_session, sample_device, sample_host,
    ):
        parent = _seed_successful_parent_run(
            db_session, sample_device, sample_host,
        )
        next_plan_id = parent.plan_snapshot["plan"]["next_plan_id"]
        child = PlanRun(
            plan_id=next_plan_id,
            status="RUNNING",
            plan_snapshot={"plan": {"id": next_plan_id}},
            run_type="CHAIN",
            parent_plan_run_id=parent.id,
            root_plan_run_id=parent.id,
            chain_index=1,
            started_at=datetime.now(timezone.utc),
        )
        parent.next_plan_triggered = False
        db_session.add(child)
        db_session.commit()

        assert reconcile_plan_chains() == 1
        db_session.expire_all()
        assert db_session.get(PlanRun, parent.id).next_plan_triggered is True


# ── Async 路径 — atomic child creation + post-commit enqueue ───────────────


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _device_result(*device_ids, status="ONLINE", last_seen=None, job_status="COMPLETED"):
    """#2648/#1686/#1822：JOIN 返回 (id, job_status, status, last_seen) 元组。"""
    result = MagicMock()
    result.all.return_value = [(d, job_status, status, last_seen) for d in device_ids]
    result.scalars.return_value.unique.return_value = list(device_ids)
    return result


def _build_mock_async_session(parent: PlanRun):
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(
        side_effect=[
            _scalar_result(parent),
            _scalar_result(None),
            _device_result(1, 2),
        ]
    )
    mock_db.run_sync = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()
    mock_db.get = AsyncMock()
    return mock_db


@pytest.mark.asyncio
async def test_async_dispatch_failure_rolls_back():
    """Child creation failure rolls back the parent flag transaction."""
    pr = PlanRun(
        id=42, plan_id=10, status="SUCCESS",
        chain_index=0, root_plan_run_id=None, triggered_by="test",
        next_plan_triggered=False, result_summary=None,
        plan_snapshot={"plan": {"id": 10, "next_plan_id": 20}, "steps": []},
    )
    mock_db = _build_mock_async_session(pr)
    mock_db.run_sync.side_effect = PlanDispatchError("no devices")

    with patch(
        "backend.services.plan_chain_trigger._rollback_chain_trigger_async",
        new=AsyncMock(),
    ) as rb:
        result = await trigger_next_plan(pr, mock_db)

    assert result is None
    mock_db.rollback.assert_awaited_once()
    rb.assert_awaited_once()
    assert rb.call_args.args[1] == 42


@pytest.mark.asyncio
async def test_async_unexpected_exception_also_rolls_back():
    """Unexpected child creation errors also leave a retryable parent."""
    pr = PlanRun(
        id=99, plan_id=10, status="SUCCESS",
        chain_index=0, root_plan_run_id=None, triggered_by="test",
        next_plan_triggered=False, result_summary=None,
        plan_snapshot={"plan": {"id": 10, "next_plan_id": 20}, "steps": []},
    )
    mock_db = _build_mock_async_session(pr)
    mock_db.run_sync.side_effect = ConnectionError("postgres link lost")

    with patch(
        "backend.services.plan_chain_trigger._rollback_chain_trigger_async",
        new=AsyncMock(),
    ) as rb:
        # 不应抛 — swallow + return None 是契约
        result = await trigger_next_plan(pr, mock_db)

    assert result is None
    rb.assert_awaited_once()
    rb_err = rb.call_args.args[2]
    assert isinstance(rb_err, ConnectionError)


@pytest.mark.asyncio
async def test_async_cas_loser_returns_none_without_dispatch():
    """A parent already claimed by another trigger cannot create a child."""
    pr = PlanRun(
        id=7, plan_id=10, status="SUCCESS",
        chain_index=0, root_plan_run_id=None, triggered_by="test",
        next_plan_triggered=True,
        plan_snapshot={"plan": {"id": 10, "next_plan_id": 20}, "steps": []},
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(
        side_effect=[_scalar_result(pr), _scalar_result(None)]
    )
    mock_db.run_sync = AsyncMock()
    mock_db.commit = AsyncMock()

    result = await trigger_next_plan(pr, mock_db)

    assert result is None
    mock_db.run_sync.assert_not_awaited()


class TestPlanChainLegacySnapshotFallback:
    def test_trigger_sync_falls_back_to_live_plan_next_plan_id(
        self, db_session, sample_device, sample_host, sample_script,
    ):
        child_plan = Plan(name="chain-child-fallback")
        parent_plan = Plan(name="chain-parent-fallback")
        db_session.add_all([parent_plan, child_plan])
        db_session.flush()
        parent_plan.next_plan_id = child_plan.id

        pr = PlanRun(
            plan_id=parent_plan.id,
            status="SUCCESS",
            plan_snapshot={
                "plan": {"id": parent_plan.id},
                "steps": [],
            },
            run_type="MANUAL",
            triggered_by="test",
            started_at=datetime.now(timezone.utc),
        )
        db_session.add(pr)
        db_session.flush()
        db_session.add(
            JobInstance(
                plan_run_id=pr.id,
                plan_id=parent_plan.id,
                device_id=sample_device.id,
                host_id=sample_host.id,
                status=JobStatus.COMPLETED.value,
                pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            )
        )
        db_session.add(
            PlanStep(
                plan_id=child_plan.id,
                stage="init",
                sort_order=0,
                step_key="child-init",
                script_name=sample_script[0].name,
                script_version=sample_script[0].version,
                timeout_seconds=300,
                enabled=True,
            )
        )
        db_session.commit()
        db_session.refresh(pr)

        child = trigger_next_plan_sync(pr, db_session)

        assert child is not None
        assert child.plan_id == child_plan.id
        assert child.parent_plan_run_id == pr.id
        assert child.status == "QUEUED"


def test_select_chain_devices_includes_fresh_offline_excludes_stale_and_busy():
    """#1822：非 COMPLETED job 沿用状态规则——心跳窗口内 OFFLINE 入列；过期 OFFLINE / BUSY / ERROR 排除。"""
    from backend.services.plan_chain_trigger import _select_chain_devices

    now = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)
    fresh = now - timedelta(seconds=60)
    stale = now - timedelta(seconds=900)
    rows = [
        (1, "FAILED", "ONLINE", None),
        (2, "FAILED", "OFFLINE", fresh),
        (3, "FAILED", "OFFLINE", stale),
        (4, "FAILED", "BUSY", fresh),
        (5, "FAILED", "OFFLINE", None),
        (6, "FAILED", "ERROR", fresh),
    ]
    device_ids, excluded = _select_chain_devices(
        rows, now=now, grace_seconds=300,
    )
    assert device_ids == [1, 2]
    excluded_ids = {e["device_id"] for e in excluded}
    assert excluded_ids == {3, 4, 5, 6}
    by_id = {e["device_id"]: e for e in excluded}
    assert by_id[3]["reason"] == "offline_stale"
    assert by_id[5]["reason"] == "offline_no_last_seen"
    assert by_id[4]["status"] == "BUSY"


def test_select_chain_devices_completed_job_overrides_busy_and_offline():
    """#2648：父段 job COMPLETED → 无条件入列。

    生产实证（run 421→422）：链触发在父 run 终态化 3 秒后执行，21 台 job
    COMPLETED 但 device.status 仍 BUSY（teardown 收尾），被永久踢出链且
    不可回补。修复后 COMPLETED 优先于任何瞬时 status（含 BUSY/过期 OFFLINE），
    可用性交给准入层终检。
    """
    from backend.services.plan_chain_trigger import _select_chain_devices

    now = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
    stale = now - timedelta(seconds=900)
    rows = [
        (11, "COMPLETED", "BUSY", stale),
        (12, "COMPLETED", "OFFLINE", stale),
        (13, "COMPLETED", "ONLINE", None),
        (14, "ABORTED", "BUSY", stale),
    ]
    device_ids, excluded = _select_chain_devices(
        rows, now=now, grace_seconds=300,
    )
    assert device_ids == [11, 12, 13]
    assert [e["device_id"] for e in excluded] == [14]
    assert excluded[0]["job_status"] == "ABORTED"


def test_select_chain_devices_naive_last_seen_treated_as_utc():
    """无 tzinfo 的 last_seen 按 UTC 解释，不误判为过期。"""
    from backend.services.plan_chain_trigger import _select_chain_devices

    now = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)
    naive_fresh = datetime(2026, 9, 13, 11, 59, 0)  # 60s ago if UTC
    ids, excluded = _select_chain_devices(
        [(9, "FAILED", "OFFLINE", naive_fresh)], now=now, grace_seconds=300,
    )
    assert ids == [9]
    assert excluded == []


class TestChainTriggerSettleWindow:
    """#2755：即时链触发路径的最小稳定窗（r431 后 2s 触发→40.6% init 失败）。

    判据：窗口内**跳过**（不设 flag、不建 child、日志可观测），由 reconciler 下一
    tick 以默认 `respect_settle=False` 补偿；补偿路径与幂等回放不受窗约束。
    """

    @staticmethod
    def _seed_parent(db_session, sample_device, sample_host, sample_script, *,
                     ended_at, child_plan_steps=True):
        child_plan = Plan(name="settle-child")
        parent_plan = Plan(name="settle-parent", next_plan_id=None)
        db_session.add_all([parent_plan, child_plan])
        db_session.flush()
        parent_plan.next_plan_id = child_plan.id
        pr = PlanRun(
            plan_id=parent_plan.id, status="SUCCESS",
            plan_snapshot={"plan": {"id": parent_plan.id, "next_plan_id": child_plan.id}, "steps": []},
            run_type="MANUAL", triggered_by="test",
            started_at=(ended_at or datetime.now(timezone.utc)),
            # ended_at 为 None 时 started_at 兜底当前（列 NOT NULL）——本族用例只
            # 关心 ended_at 缺失是否阻断触发，started_at 只是占位。
            ended_at=ended_at,
        )
        db_session.add(pr)
        db_session.flush()
        db_session.add(JobInstance(
            plan_run_id=pr.id, plan_id=parent_plan.id,
            device_id=sample_device.id, host_id=sample_host.id,
            status=JobStatus.COMPLETED.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        ))
        if child_plan_steps:
            from backend.models.plan import PlanStep
            db_session.add(PlanStep(
                plan_id=child_plan.id, stage="init", sort_order=0,
                step_key="s-init", script_name=sample_script[0].name,
                script_version=sample_script[0].version,
                timeout_seconds=60, enabled=True,
            ))
        db_session.commit()
        db_session.refresh(pr)
        return pr

    def _settle180(self, monkeypatch):
        from types import SimpleNamespace
        monkeypatch.setattr(
            "backend.services.plan_chain_trigger.get_scheduler_settings",
            lambda: SimpleNamespace(chain_trigger_settle_seconds=180),
        )

    def test_within_window_skips_without_side_effects(
        self, db_session, sample_device, sample_host, sample_script, monkeypatch, caplog,
    ):
        import logging
        from datetime import datetime, timezone
        self._settle180(monkeypatch)
        parent = self._seed_parent(db_session, sample_device, sample_host, sample_script,
                                   ended_at=datetime.now(timezone.utc))
        with caplog.at_level(logging.INFO):
            out = trigger_next_plan_sync(parent, db_session, respect_settle=True)
        assert out is None
        db_session.expire_all()
        stored = db_session.get(PlanRun, parent.id)
        assert stored.next_plan_triggered is False
        assert db_session.query(PlanRun).filter(
            PlanRun.parent_plan_run_id == parent.id).count() == 0
        assert "plan_chain_trigger_settling" in caplog.text

    def test_past_window_triggers_normally(
        self, db_session, sample_device, sample_host, sample_script, monkeypatch,
    ):
        from datetime import datetime, timedelta, timezone
        self._settle180(monkeypatch)
        parent = self._seed_parent(
            db_session, sample_device, sample_host, sample_script,
            ended_at=datetime.now(timezone.utc) - timedelta(seconds=600),
        )
        child = trigger_next_plan_sync(parent, db_session, respect_settle=True)
        assert child is not None
        assert child.status == "QUEUED"

    def test_function_default_respects_no_window(
        self, db_session, sample_device, sample_host, sample_script, monkeypatch,
    ):
        """函数默认 `respect_settle=False`：直接调用的旧调用方行为不变；
        窗语义由调用点声明（即时路径/补偿 helper 传 True）。"""
        from datetime import datetime, timezone
        self._settle180(monkeypatch)
        parent = self._seed_parent(db_session, sample_device, sample_host, sample_script,
                                   ended_at=datetime.now(timezone.utc))
        child = trigger_next_plan_sync(parent, db_session)
        assert child is not None

    def test_reconcile_helper_waits_within_window(
        self, db_session, sample_device, sample_host, sample_script, monkeypatch, caplog,
    ):
        """#2755 评审修正：补偿 helper 也吃窗——否则 reconciler 的 60s tick 会在
        窗内把即时路径刚跳过的 parent 立刻发出，settle 只剩一个 tick 的宽度
        （有效窗 ≈60s 而非配置值）。反证：把 `reconcile_chain_trigger_sync` 末尾
        的 `respect_settle=True` 退回默认，本测试即红。"""
        import logging
        from datetime import datetime, timezone
        self._settle180(monkeypatch)
        parent = self._seed_parent(db_session, sample_device, sample_host, sample_script,
                                   ended_at=datetime.now(timezone.utc))
        with caplog.at_level(logging.INFO):
            child = reconcile_chain_trigger_sync(parent.id, db_session)
        assert child is None
        db_session.expire_all()
        stored = db_session.get(PlanRun, parent.id)
        assert stored.next_plan_triggered is False
        assert db_session.query(PlanRun).filter(
            PlanRun.parent_plan_run_id == parent.id).count() == 0
        assert "plan_chain_trigger_settling" in caplog.text

    def test_reconcile_helper_dispatches_past_window(
        self, db_session, sample_device, sample_host, sample_script, monkeypatch,
    ):
        """过窗的老 parent 经补偿 helper 照常补发——「中断触发修复」语义零损失。"""
        from datetime import datetime, timedelta, timezone
        self._settle180(monkeypatch)
        parent = self._seed_parent(
            db_session, sample_device, sample_host, sample_script,
            ended_at=datetime.now(timezone.utc) - timedelta(seconds=600),
        )
        child = reconcile_chain_trigger_sync(parent.id, db_session)
        assert child is not None
        assert child.status == "QUEUED"

    def test_reconcile_helper_repairs_orphaned_flag_within_window_without_child(
        self, db_session, sample_device, sample_host, sample_script, monkeypatch,
    ):
        """修复形态（flag=true 但无 child）在窗内：重置 flag 但**不立即补发**，
        留给下一 tick 窗过后再发——不得借修复路径绕过窗。"""
        from datetime import datetime, timezone
        self._settle180(monkeypatch)
        parent = self._seed_parent(db_session, sample_device, sample_host, sample_script,
                                   ended_at=datetime.now(timezone.utc))
        db_session.execute(
            update(PlanRun).where(PlanRun.id == parent.id)
            .values(next_plan_triggered=True)
        )
        db_session.commit()
        child = reconcile_chain_trigger_sync(parent.id, db_session)
        assert child is None
        db_session.expire_all()
        stored = db_session.get(PlanRun, parent.id)
        assert stored.next_plan_triggered is False  # flag 已修复
        assert db_session.query(PlanRun).filter(
            PlanRun.parent_plan_run_id == parent.id).count() == 0  # 但未补发

    def test_missing_ended_at_does_not_block(
        self, db_session, sample_device, sample_host, sample_script, monkeypatch,
    ):
        self._settle180(monkeypatch)
        parent = self._seed_parent(db_session, sample_device, sample_host, sample_script,
                                   ended_at=None)
        child = trigger_next_plan_sync(parent, db_session, respect_settle=True)
        assert child is not None

    def test_existing_child_returns_despite_window(
        self, db_session, sample_device, sample_host, sample_script, monkeypatch,
    ):
        """幂等回放不吃窗：child 已存在时直接返回既有（settle 不得挡住回放）。"""
        from datetime import datetime, timezone
        self._settle180(monkeypatch)
        parent = self._seed_parent(db_session, sample_device, sample_host, sample_script,
                                   ended_at=datetime.now(timezone.utc))
        child_plan = db_session.get(Plan, parent.plan_snapshot["plan"]["next_plan_id"])
        existing = PlanRun(
            plan_id=child_plan.id, status="QUEUED",
            plan_snapshot=parent.plan_snapshot, run_type="CHAIN",
            triggered_by="test", parent_plan_run_id=parent.id,
            chain_index=(parent.chain_index or 0) + 1,
        )
        db_session.add(existing)
        db_session.commit()
        db_session.refresh(parent)
        out = trigger_next_plan_sync(parent, db_session, respect_settle=True)
        assert out is not None and out.id == existing.id
