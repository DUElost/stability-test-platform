"""Plan chain trigger — dispatch failure rollback (sync + async + unexpected)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
    child_plan = Plan(name="chain-child", failure_threshold=0.1)
    parent_plan = Plan(name="chain-parent", failure_threshold=0.1, next_plan_id=None)
    db_session.add_all([parent_plan, child_plan])
    db_session.flush()
    parent_plan.next_plan_id = child_plan.id

    pr = PlanRun(
        plan_id=parent_plan.id,
        status="SUCCESS",
        failure_threshold=0.1,
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
    def test_child_row_and_parent_flag_commit_before_gate_enqueue(
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

        with patch(
            "backend.services.plan_chain_trigger.enqueue_sync",
        ) as enqueue:
            child = trigger_next_plan_sync(parent, db_session)

        assert child is not None
        db_session.expire_all()
        stored_parent = db_session.get(PlanRun, parent.id)
        stored_child = db_session.get(PlanRun, child.id)
        assert stored_parent.next_plan_triggered is True
        assert stored_child.parent_plan_run_id == parent.id
        assert stored_child.run_context["dispatch_state"]["enqueue_key"] == (
            f"precheck:{stored_child.id}"
        )
        assert (
            db_session.query(JobInstance)
            .filter(JobInstance.plan_run_id == stored_child.id)
            .count()
            == 0
        )
        enqueue.assert_called_once()

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
            failure_threshold=0.1,
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

        def _redispatch(refreshed_parent, db):
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
            failure_threshold=0.1,
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
            failure_threshold=0.1,
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


def _device_result(*device_ids):
    result = MagicMock()
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
        child_plan = Plan(name="chain-child-fallback", failure_threshold=0.1)
        parent_plan = Plan(name="chain-parent-fallback", failure_threshold=0.1)
        db_session.add_all([parent_plan, child_plan])
        db_session.flush()
        parent_plan.next_plan_id = child_plan.id

        pr = PlanRun(
            plan_id=parent_plan.id,
            status="SUCCESS",
            failure_threshold=0.1,
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

        with patch(
            "backend.services.plan_chain_trigger.enqueue_sync",
        ):
            child = trigger_next_plan_sync(pr, db_session)

        assert child is not None
        assert child.plan_id == child_plan.id
        assert child.parent_plan_run_id == pr.id
