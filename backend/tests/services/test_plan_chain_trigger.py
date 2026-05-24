"""Plan chain trigger — dispatch failure rollback (sync + async + unexpected)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.enums import JobStatus
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services.plan_chain_trigger import (
    trigger_next_plan,
    trigger_next_plan_sync,
)
from backend.services.plan_dispatcher import PlanDispatchError as AsyncPlanDispatchError
from backend.services.plan_dispatcher_core import PlanDispatchError


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
        plan_snapshot={"plan_id": parent_plan.id},
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
    def test_dispatch_failure_rolls_back_next_plan_triggered(
        self, db_session, sample_device, sample_host,
    ):
        pr = _seed_successful_parent_run(db_session, sample_device, sample_host)

        with patch(
            "backend.services.plan_chain_trigger.dispatch_plan_sync",
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
            "backend.services.plan_chain_trigger.dispatch_plan_sync",
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

        with patch(
            "backend.services.plan_chain_trigger.dispatch_plan_sync",
            side_effect=PlanDispatchError("dispatch gate failed"),
        ):
            result = trigger_next_plan_sync(pr, db_session)

        assert result is None
        db_session.expire_all()
        refreshed = db_session.get(PlanRun, pr.id)
        # 关键不变量:child 已存在 → flag 保持 True,防止下次 aggregator INSERT 撞 unique
        assert refreshed.next_plan_triggered is True, (
            "child PlanRun 已落库,parent flag 必须保持 True 防止重试撞 unique 索引"
        )
        assert refreshed.result_summary["chain_dispatch_failed"]["child_already_created"] is True


# ── Async 路径 — Mock-based 验证 catch + rollback 决策分支 ───────────────


def _build_mock_async_session(plan_run_id: int, plan):
    """构造 AsyncSession mock — 满足 trigger_next_plan 的 db.get / db.execute / db.commit。"""
    mock_db = MagicMock()
    # db.get(Plan, ...) 返回 plan;后续 _rollback 内 db.get(PlanRun, ...) 由测试 patch rollback 跳过
    mock_db.get = AsyncMock(return_value=plan)

    device_rows = [SimpleNamespace(device_id=1), SimpleNamespace(device_id=2)]
    device_result = MagicMock()
    device_result.all.return_value = device_rows

    update_result = MagicMock()
    update_result.scalar.return_value = plan_run_id  # CAS 成功

    mock_db.execute = AsyncMock(side_effect=[device_result, update_result])
    mock_db.commit = AsyncMock()
    return mock_db


@pytest.mark.asyncio
async def test_async_dispatch_failure_rolls_back():
    """async 路径 PlanDispatchError 也走 rollback(对照 sync 已有覆盖)。"""
    pr = PlanRun(
        id=42, plan_id=10, status="SUCCESS",
        chain_index=0, root_plan_run_id=None, triggered_by="test",
        next_plan_triggered=False, result_summary=None,
    )
    plan = Plan(id=10, name="p", next_plan_id=20)
    mock_db = _build_mock_async_session(pr.id, plan)

    with patch(
        "backend.services.plan_chain_trigger.dispatch_plan",
        new=AsyncMock(side_effect=AsyncPlanDispatchError("no devices")),
    ), patch(
        "backend.services.plan_chain_trigger._rollback_chain_trigger_async",
        new=AsyncMock(),
    ) as rb:
        result = await trigger_next_plan(pr, mock_db)

    assert result is None
    rb.assert_awaited_once()
    # 第二实参是 plan_run_id
    assert rb.call_args.args[1] == 42


@pytest.mark.asyncio
async def test_async_unexpected_exception_also_rolls_back():
    """#4: async 兜底 — 非 PlanDispatchError 系统异常同样 rollback,不让 aggregator 挂。"""
    pr = PlanRun(
        id=99, plan_id=10, status="SUCCESS",
        chain_index=0, root_plan_run_id=None, triggered_by="test",
        next_plan_triggered=False, result_summary=None,
    )
    plan = Plan(id=10, name="p", next_plan_id=20)
    mock_db = _build_mock_async_session(pr.id, plan)

    with patch(
        "backend.services.plan_chain_trigger.dispatch_plan",
        new=AsyncMock(side_effect=ConnectionError("postgres link lost")),
    ), patch(
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
    """CAS 并发去重:UPDATE...WHERE next_plan_triggered.is_(False) 只有一个 winner;
    输家 scalar() 返回 None,不进入 dispatch 分支。"""
    pr = PlanRun(
        id=7, plan_id=10, status="SUCCESS",
        chain_index=0, root_plan_run_id=None, triggered_by="test",
    )
    plan = Plan(id=10, name="p", next_plan_id=20)

    mock_db = MagicMock()
    mock_db.get = AsyncMock(return_value=plan)
    device_rows = [SimpleNamespace(device_id=1)]
    device_result = MagicMock()
    device_result.all.return_value = device_rows
    update_result = MagicMock()
    update_result.scalar.return_value = None  # CAS 输家
    mock_db.execute = AsyncMock(side_effect=[device_result, update_result])
    mock_db.commit = AsyncMock()

    with patch(
        "backend.services.plan_chain_trigger.dispatch_plan", new=AsyncMock(),
    ) as disp:
        result = await trigger_next_plan(pr, mock_db)

    assert result is None
    disp.assert_not_awaited()  # 输家不应触发 dispatch
