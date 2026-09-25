# -*- coding: utf-8 -*-
"""#3299 — 父 PlanRun 终态后的唯一副作用编排者（ADR-0052 D3–D4 的聚合者载体）。

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
顶层 import 只取 models 纯定义（enums/列映射），其余带副作用的依赖
（sqlalchemy 会话、notification_service、chain_trigger、dedup_scan、thread_pool）一律
函数体内取，使 ``plan_run_abort`` 的 clean-env 契约（#2372：
``tests/test_plan_run_abort_import_contract.py``）不因本模块而变。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from backend.models.enums import PlanRunStatus
from backend.models.job import JobInstance

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
    status_value = getattr(run, "status", None)
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
    await trigger_next_plan(run, db, respect_settle=True)
    if should_trigger_dedup(run.status):
        await enqueue_dedup_terminal_async(run.id)


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
    trigger_next_plan_sync(run, db, respect_settle=True)
    if should_trigger_dedup(run.status):
        enqueue_dedup_terminal_sync(run.id)


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
        from sqlalchemy import select
        from sqlalchemy.orm.attributes import flag_modified

        from backend.models.plan_run import PlanRun
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
