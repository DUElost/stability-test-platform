"""ADR-0052（#3244）终态事实与父 Run 聚合解耦——结构守卫 + 行为守卫。

盯五类回归：
1. D1 终态事务回到「锁父行/自增计数/ACK 逐 Job 写」的旧形状（静态守卫）；
2. D3 排空循环失效（只消费一批就退出 ⇒ 尾批押给 300s 修复扫描，破 §5-③）；
3. D3 消费即删 + 重算幂等（重复 drain 收益为 0）；
4. D4 副作用残留重放 + done 拦截（不重复执行）；
5. D5 ACK 停写（agent_completion 的 ABORTED 分支不得再读改写父行 JSON）。
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select

from backend.models.enums import JobStatus, PlanRunStatus
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun, PlanRunPendingAggregation


# ── 1. D1/D5 静态守卫：终态事务不得再触父行 ─────────────────────────────────


def _module_tree(rel: str) -> ast.Module:
    p = pathlib.Path(__file__).resolve().parents[3] / rel
    return ast.parse(p.read_text(encoding="utf-8"), filename=str(p))


def _code_src(rel: str) -> str:
    """模块源码去掉 docstring 的 AST 重打印（# 注释天然不在 AST 里）。

    守卫盯的是**代码**——docstring 里对旧形状的说明性提及不是回归，不该误伤。
    """
    tree = _module_tree(rel)
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                n.body
                and isinstance(n.body[0], ast.Expr)
                and isinstance(n.body[0].value, ast.Constant)
                and isinstance(n.body[0].value.value, str)
            ):
                n.body = n.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _calls_to(node: ast.AST, attr: str) -> int:
    hits = 0
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr == attr:
                hits += 1
            elif isinstance(f, ast.Name) and f.id == attr:
                hits += 1
    return hits


def test_job_terminalization_does_not_lock_or_bump_parent():
    """终态服务里不得再出现父行锁与计数写（ADR-0052 D1 的结构锚点）。"""
    tree = _module_tree("backend/services/job_terminalization.py")
    src = _code_src("backend/services/job_terminalization.py")
    assert _calls_to(tree, "with_for_update") == 0, (
        "ADR-0052 D1：Job 终态事务不得再 SELECT ... FOR NO KEY UPDATE plan_run"
    )
    for banned in (
        "_bump_counters",
        "apply_plan_run_aggregation",
        "acknowledged_job_ids",
    ):
        assert banned not in src, f"ADR-0052：终态服务不应再引用 {banned}"


def test_agent_completion_aborted_branch_writes_no_ack():
    """D5：ABORTED 分支的逐 Job acknowledged_job_ids 读改写必须保持删除。"""
    src = _code_src("backend/services/agent_completion.py")
    assert "acknowledged_job_ids" not in src
    # ACK 的历史读兼容仍在 abort 入口（初始空数组 + 保留合并）——迁移时勿一并删。
    abort_src = _code_src("backend/services/plan_run_abort.py")
    assert "acknowledged_job_ids" in abort_src


# ── 行为夹具 ─────────────────────────────────────────────────────────────────


def _seed_run_with_terminal_job(db_session, sample_device):
    now = datetime.now(timezone.utc)
    plan = Plan(name="agg-3244")
    db_session.add(plan)
    db_session.flush()
    run = PlanRun(
        plan_id=plan.id,
        status=PlanRunStatus.RUNNING.value,
        plan_snapshot={"name": plan.name, "steps": []},
        run_type="MANUAL",
        started_at=now,
        total_job_count=1,
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
    db_session.commit()
    return run, job


def _insert_marks(db_session, run_id, n, start=900000):
    for i in range(n):
        db_session.add(PlanRunPendingAggregation(
            plan_run_id=run_id, job_id=start + i,
        ))
    db_session.commit()


def test_drain_loop_consumes_multiple_batches_until_empty(db_session, sample_device, monkeypatch):
    """§7-1：聚合者必须排空到无标记才退出——单批上限不是一轮上限。"""
    from backend.services import plan_run_finalization as pf

    monkeypatch.setattr(pf, "AGGREGATION_BATCH_LIMIT", 3)
    run, job = _seed_run_with_terminal_job(db_session, sample_device)
    # 真实标记 + 11 个纯触发标记（job 表没有对应行不影响重算——事实源是 job 表）。
    db_session.add(PlanRunPendingAggregation(plan_run_id=run.id, job_id=job.id))
    db_session.commit()
    _insert_marks(db_session, run.id, 11)

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ), patch(
        "backend.services.plan_chain_trigger.trigger_next_plan_sync",
    ), patch(
        "backend.services.dedup_scan.should_trigger_dedup", return_value=False,
    ):
        consumed = pf.drain_plan_run_aggregation_sync(run.id)
    assert consumed == 12  # 3+3+3+3 四批（其中一轮取 0 → 排空退出）

    db_session.expire_all()
    left = db_session.execute(
        select(PlanRunPendingAggregation).where(
            PlanRunPendingAggregation.plan_run_id == run.id
        )
    ).scalars().all()
    assert left == []
    stored = db_session.get(PlanRun, run.id)
    assert stored.status == PlanRunStatus.SUCCESS.value
    assert stored.terminal_job_count == 1

    # 幂等：排空后再 drain 一轮，零消费、零改写。
    assert pf.drain_plan_run_aggregation_sync(run.id) == 0


def test_drain_cap_logs_and_leaves_tail_for_recovery(db_session, sample_device, monkeypatch):
    """安全帽：病态热 Run 不使单任务无限驻留；余量留给 SAQ 重试/修复扫描。"""
    from backend.services import plan_run_finalization as pf

    monkeypatch.setattr(pf, "AGGREGATION_BATCH_LIMIT", 2)
    monkeypatch.setattr(pf, "AGGREGATION_DRAIN_MAX_ROUNDS", 2)
    run, job = _seed_run_with_terminal_job(db_session, sample_device)
    db_session.add(PlanRunPendingAggregation(plan_run_id=run.id, job_id=job.id))
    _insert_marks(db_session, run.id, 6)

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ), patch(
        "backend.services.plan_chain_trigger.trigger_next_plan_sync",
    ), patch(
        "backend.services.dedup_scan.should_trigger_dedup", return_value=False,
    ):
        consumed = pf.drain_plan_run_aggregation_sync(run.id)
    assert consumed == 4  # 2 轮 × 上限 2，安全帽截断
    db_session.expire_all()
    left = db_session.execute(
        select(PlanRunPendingAggregation.job_id).where(
            PlanRunPendingAggregation.plan_run_id == run.id
        )
    ).scalars().all()
    assert len(list(left)) == 3  # 余量仍在：恢复路径（唤醒/扫描）继续消费


def test_reconcile_recovery_replays_effects_once(db_session, sample_device):
    """D4 恢复矩阵：副作用块崩溃窗口 → 补偿扫描重放；done 后不重复。"""
    from backend.scheduler.counter_reconciler import _replay_stale_aggregation_triggers
    from backend.models.plan_run import PlanRun as PR

    run, job = _seed_run_with_terminal_job(db_session, sample_device)
    # 模拟「聚合已提交、副作用未跑完」：终态在、标记停在 pending。
    stored = db_session.get(PR, run.id)
    stored.status = PlanRunStatus.SUCCESS.value
    stored.terminal_job_count = 1
    stored.completed_job_count = 1
    stored.terminal_effects_state = "pending"
    db_session.commit()

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ) as notify, patch(
        "backend.services.plan_chain_trigger.trigger_next_plan_sync",
    ), patch(
        "backend.services.dedup_scan.should_trigger_dedup", return_value=False,
    ):
        out = _replay_stale_aggregation_triggers(batch_size=50)
        assert out["replayed_effects"] >= 1
        assert notify.call_count == 1
        db_session.expire_all()
        assert db_session.get(PR, run.id).terminal_effects_state == "done"

        # 再来一轮：done 拦截，不重复通知、不重复链。
        out2 = _replay_stale_aggregation_triggers(batch_size=50)
        assert out2["replayed_effects"] == 0
        assert notify.call_count == 1


def test_pending_backlog_drained_by_recovery_sweep(db_session, sample_device):
    """恢复通道 (a)：唤醒丢失留下的 pending 行由 leader sweep 内联排空。"""
    from backend.scheduler.counter_reconciler import _replay_stale_aggregation_triggers

    run, job = _seed_run_with_terminal_job(db_session, sample_device)
    db_session.add(PlanRunPendingAggregation(plan_run_id=run.id, job_id=job.id))
    db_session.commit()

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ), patch(
        "backend.services.plan_chain_trigger.trigger_next_plan_sync",
    ), patch(
        "backend.services.dedup_scan.should_trigger_dedup", return_value=False,
    ):
        out = _replay_stale_aggregation_triggers(batch_size=50)
    assert out["replayed_drains"] >= 1
    db_session.expire_all()
    stored = db_session.get(PlanRun, run.id)
    assert stored.status == PlanRunStatus.SUCCESS.value
    assert stored.terminal_effects_state == "done"
    left = db_session.execute(
        select(PlanRunPendingAggregation).where(
            PlanRunPendingAggregation.plan_run_id == run.id
        )
    ).scalars().all()
    assert list(left) == []
