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
from datetime import datetime, timedelta, timezone
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


def test_aggregator_drain_does_not_record_counter_drift(db_session, sample_device):
    """#3399 裁决 A：聚合器批量补齐计数不记 drift（埋点只留 reconciler/补偿路径）。

    聚合器本身就是计数的唯一写入方，catch-up 必然命中 before≠after；若在此
    埋点，`StabilityPlanRunCounterDrift` 会随每轮聚合成常态告警（生产实测：
    聚合轮次 == drift 记录数）。
    """
    from backend.core.metrics import plan_run_counter_drift_total
    from backend.services import plan_run_finalization as pf

    run, job = _seed_run_with_terminal_job(db_session, sample_device)
    db_session.add(PlanRunPendingAggregation(plan_run_id=run.id, job_id=job.id))
    db_session.commit()

    def _value(mode: str) -> float:
        return plan_run_counter_drift_total.labels(mode=mode)._value.get()

    before = {m: _value(m) for m in ("total", "terminal", "completed", "failed", "aborted")}

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ), patch(
        "backend.services.plan_chain_trigger.trigger_next_plan_sync",
    ), patch(
        "backend.services.dedup_scan.should_trigger_dedup", return_value=False,
    ):
        consumed = pf.drain_plan_run_aggregation_sync(run.id)

    assert consumed == 1
    db_session.expire_all()
    stored = db_session.get(PlanRun, run.id)
    assert stored.terminal_job_count == 1, "计数仍要被聚合器补齐（只是不埋点）"
    assert stored.completed_job_count == 1
    for mode, value in before.items():
        assert _value(mode) == value, f"聚合器不得记录 {mode} 漂移（#3399）"


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
    """D4 恢复矩阵：副作用块崩溃窗口 → 补偿扫描重放；done 后不重复（#3376 门槛外）。"""
    from backend.scheduler.counter_reconciler import (
        TERMINAL_EFFECTS_REPLAY_MIN_AGE_S,
        _replay_stale_aggregation_triggers,
    )
    from backend.models.plan_run import PlanRun as PR

    run, job = _seed_run_with_terminal_job(db_session, sample_device)
    # 模拟「聚合已提交、副作用未跑完」：终态在、标记停在 pending。
    stored = db_session.get(PR, run.id)
    stored.status = PlanRunStatus.SUCCESS.value
    stored.terminal_job_count = 1
    stored.completed_job_count = 1
    stored.terminal_effects_state = "pending"
    # #3376：重放查询带时间门槛——把崩溃窗口的 run 摆到门槛之外。
    stored.ended_at = datetime.now(timezone.utc) - timedelta(
        seconds=TERMINAL_EFFECTS_REPLAY_MIN_AGE_S + 30
    )
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


def test_terminal_effects_replay_respects_min_age_gate(db_session, sample_device):
    """#3376：门槛内的 pending 不重放（不抢跑正常编排）；越过门槛后重放一次并置 done。"""
    from backend.scheduler.counter_reconciler import (
        TERMINAL_EFFECTS_REPLAY_MIN_AGE_S,
        _replay_stale_aggregation_triggers,
    )
    from backend.models.plan_run import PlanRun as PR

    run, job = _seed_run_with_terminal_job(db_session, sample_device)
    stored = db_session.get(PR, run.id)
    stored.status = PlanRunStatus.SUCCESS.value
    stored.terminal_job_count = 1
    stored.completed_job_count = 1
    stored.terminal_effects_state = "pending"
    # 门槛内：正常副作用块可能仍在执行——恢复扫描不得抢跑。
    stored.ended_at = datetime.now(timezone.utc) - timedelta(
        seconds=TERMINAL_EFFECTS_REPLAY_MIN_AGE_S - 30
    )
    db_session.commit()

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ) as notify, patch(
        "backend.services.plan_chain_trigger.trigger_next_plan_sync",
    ), patch(
        "backend.services.dedup_scan.should_trigger_dedup", return_value=False,
    ):
        out = _replay_stale_aggregation_triggers(batch_size=50)
    assert out["replayed_effects"] == 0
    assert notify.call_count == 0
    db_session.expire_all()
    assert db_session.get(PR, run.id).terminal_effects_state == "pending"

    # 越过门槛：按崩溃窗口补偿，重放一次后置 done。
    stored = db_session.get(PR, run.id)
    stored.ended_at = datetime.now(timezone.utc) - timedelta(
        seconds=TERMINAL_EFFECTS_REPLAY_MIN_AGE_S + 30
    )
    db_session.commit()

    with patch(
        "backend.services.notification_service.dispatch_notification_async",
    ) as notify2, patch(
        "backend.services.plan_chain_trigger.trigger_next_plan_sync",
    ), patch(
        "backend.services.dedup_scan.should_trigger_dedup", return_value=False,
    ):
        out2 = _replay_stale_aggregation_triggers(batch_size=50)
    assert out2["replayed_effects"] == 1
    assert notify2.call_count == 1
    db_session.expire_all()
    assert db_session.get(PR, run.id).terminal_effects_state == "done"


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


# ── 尾随唤醒（#3244 复核）：「最后一轮空查 → SAQ _finish」窗口内被去重的唤醒 ──


def _patched_task_env(consumed: int):
    """把 drain 结果与队列替换掉，返回 (queue_mock, patchers)。"""
    from unittest.mock import AsyncMock, MagicMock

    queue = MagicMock()
    queue.enqueue = AsyncMock(return_value=object())
    return queue, (
        patch(
            "backend.services.plan_run_finalization.drain_plan_run_aggregation_sync",
            return_value=consumed,
        ),
        patch("backend.tasks.saq_tasks.get_queue", return_value=queue),
        patch("backend.tasks.saq_tasks.time.time", return_value=1000.4),
    )


async def test_main_aggregation_task_always_enqueues_tail():
    """主任务即便本次消费 0 也要补尾随：窗口内被去重的唤醒与本次消费量无关。"""
    from backend.tasks.saq_tasks import aggregate_plan_run_task

    queue, (p1, p2, p3) = _patched_task_env(consumed=0)
    with p1, p2, p3:
        await aggregate_plan_run_task({}, plan_run_id=77)

    queue.enqueue.assert_awaited_once()
    job = queue.enqueue.await_args.args[0]
    assert job.function == "aggregate_plan_run_task"
    assert job.kwargs == {"plan_run_id": 77, "tail": True}
    assert job.key == "agg-tail:77:1001"
    assert job.scheduled == 1001


async def test_tail_task_continues_only_when_it_consumed():
    from backend.tasks.saq_tasks import aggregate_plan_run_task

    queue, (p1, p2, p3) = _patched_task_env(consumed=0)
    with p1, p2, p3:
        await aggregate_plan_run_task({}, plan_run_id=77, tail=True)
    queue.enqueue.assert_not_awaited()

    queue, (p1, p2, p3) = _patched_task_env(consumed=3)
    with p1, p2, p3:
        await aggregate_plan_run_task({}, plan_run_id=77, tail=True)
    queue.enqueue.assert_awaited_once()


async def test_tail_enqueue_failure_does_not_fail_aggregation():
    """尾随入队失败只告警：本轮聚合已提交，兜底仍是 counter_reconciler。"""
    from unittest.mock import AsyncMock

    from backend.tasks.saq_tasks import aggregate_plan_run_task

    queue, (p1, p2, p3) = _patched_task_env(consumed=2)
    queue.enqueue = AsyncMock(side_effect=RuntimeError("redis down"))
    with p1, p2, p3:
        await aggregate_plan_run_task({}, plan_run_id=77)
