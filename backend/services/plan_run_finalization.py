# -*- coding: utf-8 -*-
"""#3299 — 父 PlanRun 终态后的唯一副作用编排者（ADR-0052 D3–D4 的聚合者载体）。

**ADR-0052 #3244 落点**：本模块除编排副作用外，还是 D3 合并聚合的执行器宿主
（``drain_plan_run_aggregation_sync/_async``）：按 ``plan_run_id`` 读 pending
标记 → 读 Job 事实重算 → 消费即删 → applied 走 ``finalize_parent_run_*``。
Job 终态事务只写 pending 标记（``job_terminalization``），不再锁/写父级热行。

**问题**：#3292 解开 SAQ 队列 20 模块环后，显露出领域环
``plan_dispatcher_sync ↔ plan_run_abort ↔ plan_run_aggregation ↔ post_completion ↔ plan_chain_trigger``，
靠函数体内 import 维持可加载。「Run 进入终态之后做什么」此前散在三处：
``_finalize_plan_run`` 内联（通知 + 报告缓存刷新）、``_post_aggregation_side_effects_*``
（commit → 链式触发 → dedup 入队）、``post_completion``（链式修复 + RISK_HIGH）。
本模块把它们收进一个显式编排者。

**边界**（第一步 = 纯结构重构，不改事务边界，不等 ADR-0052 裁决）：

- ``plan_run_aggregation`` 只负责计算与落库（状态机迁移、ended_at、result_summary、
  终态指标）；它**不得**再 import 本模块——依赖方向是 编排者 → 聚合器，
  ``apply_plan_run_aggregation*`` 的每个调用方在 applied 后经由本模块反应
  （``finalize_parent_run_*`` / ``announce_parent_terminal``）。
- 否决事件总线（#3299 选定方案）：订阅关系对 import-linter 不可见（正是 #738
  要消除的形态），且 D4 要求副作用有序执行。

**依赖方向纪律**（C5 sibling-acyclic 守护）：本模块可以向下依赖
``plan_chain_trigger``（链触发/恢复）与五模块之外的服务；五模块中只有
``plan_run_abort``（notify 缝）、``post_completion``（恢复入口/RISK_HIGH）、
``job_terminalization``（终态编排）与 ``backend/scheduler`` 的补偿路径可以指向本模块。
顶层 import 取 models 纯定义（enums/列映射）、stdlib（``time``/``defaultdict``）、
``sqlalchemy`` 纯构造子（``select``/``delete``）与 ``plan_run_events``（模块体仅 stdlib，
realtime 在函数内懒取）；其余带副作用的依赖
（sqlalchemy 会话、notification_service、chain_trigger、dedup_scan、thread_pool）一律
函数体内取，使 ``plan_run_abort`` 的 clean-env 契约（#2372：
``tests/test_plan_run_abort_import_contract.py``）不因本模块而变
（#3376 项 2 复核：纯构造子不构成 clean-env 负担，可回顶层；同 PR 下调棘轮基线）。
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select

from backend.models.enums import PlanRunStatus
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun, PlanRunHost, PlanRunPendingAggregation
from backend.services.plan_run_events import emit_plan_run_status

logger = logging.getLogger(__name__)

#: RUN_* 事件类型映射：仅 FAILED 记 RUN_FAILED；SUCCESS/PARTIAL_SUCCESS 都归
#: RUN_COMPLETED（ADR-0048 v1.1：设备失败是施压常态，黄色不断链不判红）。
_NOTIFY_AS_FAILED = {PlanRunStatus.FAILED}


# ── 终态通知 ─────────────────────────────────────────────────────────────────


def notify_plan_run_terminal(
    run: Any,
    *,
    new_status: Any,
    error_message: str,
) -> None:
    """Best-effort PlanRun-level notification (once per terminalization).

    自 ``plan_run_aggregation`` 迁入（#3299）：通知是「终态之后做什么」的副作用，
    归编排者所有；聚合器只算事实。``new_status`` 接受 ``PlanRunStatus`` 或其
    value 字符串（abort / reaper / admission_pump 的直发路径历史上即传字符串）。
    """
    try:
        from backend.services.notification_service import dispatch_notification_async

        status = (
            new_status
            if isinstance(new_status, PlanRunStatus)
            else PlanRunStatus(new_status)
        )
        event_type = (
            "RUN_FAILED" if status in _NOTIFY_AS_FAILED else "RUN_COMPLETED"
        )
        dispatch_notification_async(event_type, {
            "run_id": int(run.id),
            "plan_id": int(getattr(run, "plan_id", 0) or 0),
            "task_name": f"plan-run-{run.id}",
            "task_type": "plan",
            "device_serial": "",
            "error_message": error_message,
        })
    except Exception:
        logger.exception(
            "plan_run_terminal_notification_failed plan_run=%s status=%s",
            getattr(run, "id", None),
            getattr(new_status, "value", new_status),
        )


def _terminal_message_from_run(run: Any, *, no_jobs: bool) -> str:
    """从已落库的终态事实重建 RUN_* 通知文案（#1082/#497 时代的口径原样保留）。

    ``no_jobs`` 由调用方显式传入（聚合空集路径），不靠 result_summary 是否存在
    来反推——stale summary 会把「无 job」文案伪装成正常计数。
    """
    # ``run.status`` 经 PlanRunStateMachine.transition 落库为字符串；若拿到的是
    # ``PlanRunStatus`` 成员，3.11 下 f-string 会渲染成 ``PlanRunStatus.X``，故取 value。
    status = getattr(run, "status", None)
    status_value = getattr(status, "value", status)
    if no_jobs:
        return f"PlanRun {status_value}: no jobs were created for this plan"
    summary = getattr(run, "result_summary", None) or {}
    return (
        f"PlanRun {status_value}: {summary.get('completed', 0)}/"
        f"{summary.get('total', 0)} completed, {summary.get('failed', 0)} failed"
    )


def announce_parent_terminal(run: Any, *, no_jobs: bool = False) -> None:
    """父 Run 终态事实已落库（未提交）后的一致性副作用：通知 + 报告缓存刷新。

    取代原先 ``_finalize_plan_run`` 的内联调用（#3299）。``apply_plan_run_aggregation*``
    返回 True 后由每个编排入口调用，顺序保持原样（commit 之前）。
    """
    notify_plan_run_terminal(
        run,
        new_status=getattr(run, "status", None),
        error_message=_terminal_message_from_run(run, no_jobs=no_jobs),
    )
    # #1082：终态后数据静止 —— 批量重算各 job 的报告缓存，快照从此 = 最终结果。
    # Best-effort 后台执行（重算 N 份报告不阻塞聚合事务）；调度失败放弃本轮，
    # /report/cached 的 live 兜底仍给出正确数据。
    schedule_report_cache_refresh(int(run.id))


# ── 终态后的完整编排（原 job_terminalization._post_aggregation_side_effects_*）──


def _emit_parent_terminal_status(run: Any) -> None:
    """父终态**提交后**推送 ``plan_run_status``（前端立即失效 detail/chain/timeline 等）。

    ADR-0052 D1 后父终态由聚合者异步判定，``/complete`` 处的推送不再命中；
    必须在 commit 之后发，否则前端据此刷新可能读到未提交的旧状态。
    """
    status = getattr(run, "status", None)
    emit_plan_run_status(int(run.id), str(getattr(status, "value", status)))


async def finalize_parent_run_async(
    run: Any,
    db: Any,
    applied: bool,
    *,
    no_jobs: bool = False,
) -> None:
    """Commit parent terminal facts before chain/dedup side effects (#986).

    ``trigger_next_plan`` may ``rollback()`` on prepare failure. If that shares
    the still-open complete/aggregation transaction, parent Job terminalization,
    lease release, and PlanRun aggregation are undone. Commit first so chain
    failure only affects the child attempt; reconciler can retry the chain.
    """
    if not applied:
        return
    from backend.services.plan_chain_trigger import trigger_next_plan
    from backend.services.dedup_scan import (
        should_trigger_dedup,
        enqueue_dedup_terminal_async,
    )

    announce_parent_terminal(run, no_jobs=no_jobs)
    await db.commit()
    _emit_parent_terminal_status(run)
    await trigger_next_plan(run, db, respect_settle=True)
    if should_trigger_dedup(run.status):
        await enqueue_dedup_terminal_async(run.id)
    # ADR-0052 D4：副作用块完整走完 → 置 'done'（独立提交）。崩溃窗口
    # （父终态已提交、本块未走完）留给 counter_reconciler 的 pending 重放。
    if getattr(run, "terminal_effects_state", None) == "pending":
        run.terminal_effects_state = "done"
        await db.commit()


def finalize_parent_run_sync(
    run: Any,
    db: Any,
    applied: bool,
    *,
    no_jobs: bool = False,
) -> None:
    """Sync counterpart of ``finalize_parent_run_async`` (#986)."""
    if not applied:
        return
    from backend.services.plan_chain_trigger import trigger_next_plan_sync
    from backend.services.dedup_scan import (
        should_trigger_dedup,
        enqueue_dedup_terminal_sync,
    )

    announce_parent_terminal(run, no_jobs=no_jobs)
    db.commit()
    _emit_parent_terminal_status(run)
    trigger_next_plan_sync(run, db, respect_settle=True)
    if should_trigger_dedup(run.status):
        enqueue_dedup_terminal_sync(run.id)
    # ADR-0052 D4：同 async 版——走完置 'done'，崩溃窗口留给补偿重放。
    if getattr(run, "terminal_effects_state", None) == "pending":
        run.terminal_effects_state = "done"
        db.commit()


# ── ADR-0052 D3：按 plan_run_id 的合并聚合执行器（#3244）─────────────────────
#
# 唤醒只携带 ``plan_run_id``；每轮一批：锁父行（FOR NO KEY UPDATE）→ 读
# pending 标记（≤batch）→ **读 Job 事实重算** run/host 计数（不引入第二计数源）
# → **同事务**删除已消费标记（§7-2 消费即删）→ applied 则随终态事实同事务落
# 'pending' 副作用标记（写点在 ``_finalize_plan_run``）→ 提交；提交后经
# ``finalize_parent_run_*`` 走副作用（chain / dedup / 通知 / 报告），完成置 'done'。
#
# 排空循环（§7-1 必需项）：SAQ 按 ``agg:{plan_run_id}`` 去重，任务运行期间到达
# 的唤醒会被合并掉——只处理一批就退出会把余下 pending 押给 300s 修复扫描，破坏
# §5-③ 的 120s 收敛。故循环到某轮**取不到标记**才退出（退出前的空查轮覆盖
# 「标记提交于入队之前」的全部交错）。空查之后、SAQ ``_finish`` 之前任务仍在
# incomplete 集合里，此间同 key 唤醒会被去重——该窗口由 ``aggregate_plan_run_task``
# 结束时补的尾随任务（``agg-tail:{id}:{秒}``）覆盖，见 ``backend/tasks/saq_tasks.py``。
#
# 幂等（D3 at-least-once）：计数是**重算**非增量；删除与重算同事务；父终态有
# ``_TERMINAL_PLAN_RUN_STATUSES`` 守卫；副作用块有 'pending'/'done' 标记守卫。
#
# 执行器只有 sync 核心：SAQ 任务与 async 唤醒路径都经 ``asyncio.to_thread`` 调
# ``drain_plan_run_aggregation_sync``（worker 槽位形状与 post_completion 同先例），
# 不给 async 镜像留第二份要同步维护的实现。

#: 单批上限初值（ADR-0052 §7-1，覆盖一次 ~490 Job 的中止波）；
#: 调数不修订 ADR，但须在实施 PR 与 #3244 记录依据。
AGGREGATION_BATCH_LIMIT = int(os.getenv("STP_AGGREGATION_BATCH_LIMIT", "500"))

#: 排空循环安全帽：病态热 Run 不使单任务无限驻留；余量由 reconciler 兜底。
AGGREGATION_DRAIN_MAX_ROUNDS = int(os.getenv("STP_AGGREGATION_DRAIN_MAX_ROUNDS", "50"))


def _recount_host_projection_sync(db: Any, plan_run_id: int, jobs: Sequence[Any]) -> None:
    """per-host 投影重算。锁序与 abort/heartbeat 一致：plan_run → PRH（host_id 升序）。"""
    from backend.services.plan_run_aggregation import recount_host_counters

    prh_rows = (
        db.execute(
            select(PlanRunHost)
            .where(PlanRunHost.plan_run_id == plan_run_id)
            .order_by(PlanRunHost.host_id)
            .with_for_update(key_share=True)
        )
    ).scalars().all()
    if not prh_rows:
        return
    by_host: dict[Any, list] = defaultdict(list)
    for j in jobs:
        if getattr(j, "host_id", None):
            by_host[j.host_id].append(j)
    for prh in prh_rows:
        recount_host_counters(prh, by_host.get(prh.host_id, []))


def _aggregation_round_sync(plan_run_id: int) -> tuple[int, bool]:
    """一批聚合（独立事务）。返回 ``(consumed_marks, applied)``。"""
    from backend.core.database import SessionLocal
    from backend.core.metrics import record_plan_run_aggregation_duration
    from backend.services.plan_run_aggregation import (
        apply_plan_run_aggregation_from_counters,
        recount_plan_run_counters,
    )

    with SessionLocal() as db:
        t0 = time.perf_counter()
        run = (
            db.execute(
                select(PlanRun)
                .where(PlanRun.id == plan_run_id)
                .with_for_update(key_share=True)  # FOR NO KEY UPDATE（#1473）
            )
        ).scalar_one_or_none()
        if run is None:
            # 父行已被 retention 删除——pending 标记随 FK CASCADE 消失，无事可做。
            db.rollback()
            return 0, False
        mark_ids = (
            db.execute(
                select(PlanRunPendingAggregation.job_id)
                .where(PlanRunPendingAggregation.plan_run_id == plan_run_id)
                .order_by(
                    PlanRunPendingAggregation.created_at,
                    PlanRunPendingAggregation.job_id,
                )
                .limit(AGGREGATION_BATCH_LIMIT)
            )
        ).scalars().all()
        if not mark_ids:
            db.rollback()
            return 0, False
        jobs = (
            db.execute(
                select(JobInstance.status, JobInstance.host_id).where(
                    JobInstance.plan_run_id == plan_run_id
                )
            )
        ).all()
        # #3399 裁决 A：聚合器批量补齐是正常路径（本函数就是计数的唯一写入方），
        # 不记 drift 埋点——否则每轮聚合都会把「计数器跟上了新事实」记成漂移，
        # StabilityPlanRunCounterDrift 常态误报。埋点只留 reconciler/补偿路径。
        recount_plan_run_counters(run, jobs, record_drift=False)
        _recount_host_projection_sync(db, plan_run_id, jobs)
        db.execute(
            delete(PlanRunPendingAggregation).where(
                PlanRunPendingAggregation.plan_run_id == plan_run_id,
                PlanRunPendingAggregation.job_id.in_(mark_ids),
            )
        )
        # D3：批次内 terminal == total 才触发父终态（守卫/判定语义不变）。
        applied = apply_plan_run_aggregation_from_counters(run, db=db)
        record_plan_run_aggregation_duration(time.perf_counter() - t0, "aggregate")
        db.commit()
    return len(mark_ids), applied


def drain_plan_run_aggregation_sync(plan_run_id: int) -> int:
    """排空该 Run 的 pending（同步语境；SAQ 任务镜像 + 补偿扫描复用）。"""
    consumed_total = 0
    hit_cap = True
    for _round in range(AGGREGATION_DRAIN_MAX_ROUNDS):
        consumed, applied = _aggregation_round_sync(plan_run_id)
        if consumed == 0:
            hit_cap = False
            break
        consumed_total += consumed
        if applied:
            # 副作用块与下一轮取标记之间无锁关系（父终态守卫幂等），可安全交叠。
            _complete_parent_side_effects_sync(int(plan_run_id))
    if hit_cap:
        logger.warning(
            "aggregate_drain_cap_hit plan_run=%s consumed=%d rounds=%d "
            "— 余量由 SAQ 重试/reconciler 兜底",
            plan_run_id, consumed_total, AGGREGATION_DRAIN_MAX_ROUNDS,
        )
    if consumed_total:
        logger.info(
            "plan_run_aggregated plan_run=%s marks_consumed=%d",
            plan_run_id, consumed_total,
        )
    return consumed_total


def _complete_parent_side_effects_sync(plan_run_id: int) -> None:
    """applied 后的副作用编排（独立会话；#986：终态已提交，链失败不回滚事实）。"""
    from backend.core.database import SessionLocal

    with SessionLocal() as db:
        run = db.get(PlanRun, plan_run_id)
        if run is None:
            return
        if getattr(run, "terminal_effects_state", None) != "pending":
            # 已被其它路径（abort 批量 / reconciler 补偿）收尾——重复唤醒直接收敛。
            return
        finalize_parent_run_sync(run, db, applied=True)


# ── 链式触发恢复（补偿入口）──────────────────────────────────────────────────


def recover_chain_trigger(plan_run_id: Any, db: Any) -> Any:
    """父 Run 链式触发的持久化恢复入口（委托 ``plan_chain_trigger``）。

    post-completion 任务独立于终态请求重试，可恢复「父 CAS 已提交、子 PlanRun
    未创建」的进程崩溃窗口。#3299 起该补偿由本编排者独占调度，``post_completion``
    不再直接 import ``plan_chain_trigger``（那正是五模块环的一条边）。
    """
    from backend.services.plan_chain_trigger import reconcile_chain_trigger_sync

    return reconcile_chain_trigger_sync(plan_run_id, db)


# ── RISK_HIGH 通知（自 plan_run_aggregation 迁入，#3299）────────────────────


def maybe_notify_risk_high(
    db: Any,
    *,
    plan_run_id: int | None,
    risk_summary: dict[str, Any] | None,
) -> bool:
    """Emit RISK_HIGH once when AEE/ANR aggregation reaches level S.

    Deduped via ``run_context.risk_high_notified`` so multi-job post-completion
    does not spam. Returns True when a notification was dispatched.

    归属理由：这是 **Run 级**风险通知（消费 run_context、写 run_context、发
    RUN 级事件），原先住在 ``plan_run_aggregation`` 使 ``post_completion``
    反向 import 聚合器——环的一半。
    """
    if not plan_run_id or not isinstance(risk_summary, dict):
        return False
    if str(risk_summary.get("risk_level", "")).upper() != "S":
        return False

    try:
        from sqlalchemy.orm.attributes import flag_modified

        from backend.services.notification_service import dispatch_notification_async

        pr = db.execute(
            select(PlanRun)
            .where(PlanRun.id == int(plan_run_id))
            .with_for_update(key_share=True)  # SQLAlchemy key_share → PG FOR NO KEY UPDATE (#1473)
        ).scalar_one_or_none()
        if pr is None:
            return False

        run_ctx = dict(pr.run_context or {})
        if run_ctx.get("risk_high_notified"):
            return False

        run_ctx["risk_high_notified"] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "risk_level": "S",
            "counts": (risk_summary.get("counts") or {}),
        }
        pr.run_context = run_ctx
        if hasattr(pr, "_sa_instance_state"):
            flag_modified(pr, "run_context")
        db.commit()

        counts = risk_summary.get("counts") if isinstance(risk_summary.get("counts"), dict) else {}
        by_type = counts.get("by_type") if isinstance(counts.get("by_type"), dict) else {}
        type_bits = ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())[:12])
        dispatch_notification_async("RISK_HIGH", {
            "run_id": int(pr.id),
            "plan_id": int(getattr(pr, "plan_id", 0) or 0),
            "task_name": f"plan-run-{pr.id}",
            "task_type": "plan",
            "risk_summary": (
                f"PlanRun {pr.id} risk_level=S"
                + (f" ({type_bits})" if type_bits else "")
            ),
            "risk_level": "S",
            "counts": counts,
        })
        return True
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception(
            "plan_run_risk_high_notification_failed plan_run=%s", plan_run_id,
        )
        return False


# ── #1082 报告缓存终态刷新（自 post_completion 迁入，#3299）─────────────────


def refresh_report_cache_for_plan_run(plan_run_id: int) -> int:
    """#1082：PlanRun 终态后批量重算 job.report_json —— 快照变为最终结果。

    裁决语义（见 Agent Note 2026-09-09-report-cache-snapshot-1082）：
      - 单 Job 完成时缓存「当时整个 PlanRun 的报告快照」（post_completion 原行为）；
      - PlanRun 终态（聚合 applied）时数据才真正静止，此时统一刷新一次，
        此后「快照 = 最新最终结果」，两个口径收敛。

    只刷 post_processed_at 非空的 job（曾生成过报告的）；重算失败逐条跳过不影响
    其他 job。独立 SessionLocal：调用方（线程池 / 聚合事务外）不需要持有会话。
    """
    from backend.core.database import SessionLocal
    from backend.services.report_service import compose_run_report

    refreshed = 0
    with SessionLocal() as db:
        jobs = (
            db.query(JobInstance)
            .filter(
                JobInstance.plan_run_id == plan_run_id,
                JobInstance.post_processed_at.isnot(None),
            )
            .all()
        )
        for job in jobs:
            try:
                report = compose_run_report(db, job.id)
            except Exception:
                logger.exception(
                    "report_cache_refresh_failed job_id=%d", job.id,
                )
                continue
            if report is None:
                continue
            job.report_json = report.model_dump(mode="json")
            # post_processed_at 语义 = 报告生成时刻：终态刷新后即「最终结果」
            # 的生成时刻，/report/cached 的 cached_at 标注据此展示。
            job.post_processed_at = datetime.now(timezone.utc)
            refreshed += 1
        db.commit()
    if refreshed:
        logger.info(
            "report_cache_refreshed plan_run=%d count=%d", plan_run_id, refreshed,
        )
    return refreshed


def schedule_report_cache_refresh(plan_run_id: int) -> None:
    """#1082：终态后调度批量刷新报告缓存（best-effort，绝不外溢）。

    TESTING=1（pytest 环境）跳过：后台线程与「按用例 TRUNCATE」的数据库隔离
    模型天然竞争，会污染无关用例；需要验证刷新逻辑的用例直接调用
    ``refresh_report_cache_for_plan_run``。
    """
    if os.getenv("TESTING") == "1":
        return
    try:
        from backend.core.thread_pool import submit as _pool_submit

        _pool_submit(refresh_report_cache_for_plan_run, int(plan_run_id))
    except Exception:
        logger.debug(
            "report_cache_refresh_scheduling_failed plan_run=%s", plan_run_id,
        )
