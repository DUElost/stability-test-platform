"""ADR-0026 §6 / ADR-0052 D2/D4 — low-frequency reconciliation + recovery sweep.

两条修复通道（ADR-0052 明确：**不参与正常时延预算**，正常聚合走 SAQ 唤醒）：

1. 计数漂移：对比 ``plan_run`` 计数与 ``job_instance`` 事实并重写（recount）——
   **仅对无待聚合标记的 run**（#3399：有标记 ⇒ 聚合器负责，抢跑会把正常滞后
   记成漂移）。
2. 聚合触发恢复（#3244）：
   - pending 表有积压（唤醒入队失败 / Redis 抖动 / worker 崩溃）→ 内联排空该
     Run（聚合器幂等重算 + 消费即删）；
   - ``terminal_effects_state='pending'`` 残留（聚合已提交、副作用块未走完的
     崩溃窗口）→ 经编排者重放副作用块（chain/dedup/通知各有守卫，块级由
     pending/done 标记拦住重复）；#3376：加 2 分钟时间门槛（不做 CAS），
     避开与正在执行中的正常副作用块重叠。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from backend.core.database import SessionLocal
from backend.models.enums import PlanRunStatus
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun, PlanRunPendingAggregation
from backend.services.plan_run_aggregation import (
    apply_plan_run_aggregation_from_counters,
    recount_plan_run_counters,
)
from backend.services.plan_run_finalization import (
    announce_parent_terminal,
    drain_plan_run_aggregation_sync,
    finalize_parent_run_sync,
)

from backend.core.settings.scheduler import get_scheduler_settings

logger = logging.getLogger(__name__)

# Only scan recently-active / still-open runs by default to keep the sweep cheap.

_OPEN_STATUSES = {
    PlanRunStatus.RUNNING.value,
    PlanRunStatus.QUEUED.value,
    PlanRunStatus.PRECHECK.value,
}

#: #3376 裁决（时间门槛，不做 CAS）：副作用块的正常编排在秒级内完成，2 分钟内的
#: ``pending`` 行大概率仍在正常路径上——过门槛才视为崩溃窗口补偿，避免与正在执行
#: 的副作用块重叠。恢复语义不变（该通道本不参与正常时延预算，ADR-0052 D2）；
#: 门槛以内/以外的罕见重叠仍有下游保护（链 CAS、dedup key、通知幂等、报告刷新幂等）。
TERMINAL_EFFECTS_REPLAY_MIN_AGE_S = 120


def reconcile_plan_run_counters_once(
    *,
    lookback_hours: int | None = None,
    batch_size: int | None = None,
) -> dict:
    """Reconcile up to *batch_size* PlanRuns; return summary counters.

    ADR-0027 P3-1: only the elected scheduler leader runs the sweep.
    """
    from backend.core.leader_election import hold_scheduler_leadership

    with hold_scheduler_leadership("counter_reconcile") as is_leader:
        if not is_leader:
            return {"scanned": 0, "drifted": 0, "fixed": 0, "aggregated": 0, "skipped_not_leader": 1}
        summary = _reconcile_plan_run_counters_body(
            lookback_hours=lookback_hours,
            batch_size=batch_size,
        )
        # ADR-0052 D4 恢复通道在主事务之外跑（drain 自开会话、锁同序 plan_run→
        # PRH；放锁后再触发避免与主循环的 skip_locked 互踩）。
        summary.update(_replay_stale_aggregation_triggers(batch_size=batch_size))
        return summary


def _has_pending_aggregation(db, plan_run_id: int) -> bool:
    """该 Run 是否仍有待聚合标记（#3399 规格补充：有标记 ⇒ 跳过本 run）。

    聚合器（SAQ 唤醒 / 排空）才是这些标记的处理者，且聚合器一旦跑完，
    计数已由它自己读事实重算——reconciler 抢在它前面 recount，只会把
    「还没轮到聚合器」这段正常滞后记成漂移（误报换个路径复发）。
    存在性判断走复合主键前缀，成本一次索引探测。
    """
    return (
        db.execute(
            select(PlanRunPendingAggregation.plan_run_id)
            .where(PlanRunPendingAggregation.plan_run_id == plan_run_id)
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


def _reconcile_plan_run_counters_body(
    *,
    lookback_hours: int | None = None,
    batch_size: int | None = None,
) -> dict:
    lookback = (
        get_scheduler_settings().stp_counter_reconcile_lookback_hours if lookback_hours is None else lookback_hours
    )
    limit = get_scheduler_settings().stp_counter_reconcile_batch if batch_size is None else batch_size
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback)

    scanned = 0
    skipped_pending = 0
    drifted = 0
    fixed = 0
    aggregated = 0

    with SessionLocal() as db:
        rows = (
            db.execute(
                select(PlanRun)
                .where(
                    (PlanRun.status.in_(_OPEN_STATUSES))
                    | (PlanRun.ended_at.is_(None))
                    | (PlanRun.ended_at >= cutoff)
                    | (PlanRun.started_at >= cutoff)
                )
                .order_by(PlanRun.id.desc())
                .limit(limit)
                .with_for_update(key_share=True, skip_locked=True)
            )
        ).scalars().all()

        for run in rows:
            scanned += 1
            # #3399：仍有待聚合标记 ⇒ 计数滞后是聚合器跑之前的正常态，跳过。
            # 标记本身由 _replay_stale_aggregation_triggers 负责排空（唤醒丢失
            # 计数走 replayed_total，不在这里记 drift）。
            if _has_pending_aggregation(db, run.id):
                skipped_pending += 1
                continue
            jobs = (
                db.query(JobInstance)
                .filter(JobInstance.plan_run_id == run.id)
                .all()
            )
            # Skip empty QUEUED/PRECHECK (no jobs yet — counters stay 0).
            if not jobs and run.status in (
                PlanRunStatus.QUEUED.value,
                PlanRunStatus.PRECHECK.value,
            ):
                continue
            result = recount_plan_run_counters(run, jobs)
            if result["drifted"]:
                drifted += 1
                fixed += 1
                logger.warning(
                    "plan_run_counter_drift plan_run=%d before=%s after=%s",
                    run.id, result["before"], result["after"],
                )
                # #789: recount alone leaves RUNNING runs stuck — re-aggregate when
                # counters now show all jobs terminal.
                if int(run.total_job_count or 0) > 0:
                    if apply_plan_run_aggregation_from_counters(run):
                        # #3299：聚合器已是纯函数——补偿路径落终态后同样要经
                        # 编排者发 RUN_* 通知与 #1082 报告刷新（原为 apply_* 内联）。
                        announce_parent_terminal(run)
                        # ADR-0052 D4：本路径的副作用（通知/报告）已执行；
                        # chain/dedup 归 post_completion 的 recover_chain_trigger
                        # 补偿（形状先例：#3299 前该路径同样不触发链）。置 done
                        # 随本轮批提交落库，不给恢复扫描留假阳性。
                        run.terminal_effects_state = "done"
                        aggregated += 1

        if fixed:
            db.commit()
        else:
            db.rollback()

    summary = {
        "scanned": scanned,
        "skipped_pending": skipped_pending,
        "drifted": drifted,
        "fixed": fixed,
        "aggregated": aggregated,
    }
    if drifted:
        logger.info("counter_reconcile_done %s", summary)
    else:
        logger.debug("counter_reconcile_done %s", summary)
    return summary


def _replay_stale_aggregation_triggers(*, batch_size: int) -> dict:
    """ADR-0052 恢复通道：pending 积压重放 + 终态副作用残留重放。

    正常情况下两查询都应为空（唤醒链路健康）；非空即计数入指标、排空/重放。
    """
    from backend.core.metrics import (
        plan_run_aggregation_replayed_total,
        plan_run_pending_aggregation_depth,
    )

    replayed_drains = 0
    replayed_effects = 0

    # 观测：总深度（Gauge set，本轮 leader 独写，无跨进程竞态）。
    try:
        with SessionLocal() as db:
            depth = int(
                db.execute(
                    select(func.count()).select_from(PlanRunPendingAggregation)
                ).scalar()
                or 0
            )
        plan_run_pending_aggregation_depth.set(depth)
    except Exception:
        logger.exception("pending_aggregation_depth_gauge_failed")
        depth = 0

    if depth:
        with SessionLocal() as db:
            run_ids = (
                db.execute(
                    select(PlanRunPendingAggregation.plan_run_id)
                    .group_by(PlanRunPendingAggregation.plan_run_id)
                    .order_by(
                        func.min(PlanRunPendingAggregation.created_at),
                        PlanRunPendingAggregation.plan_run_id,
                    )
                    .limit(batch_size)
                )
            ).scalars().all()
        for rid in run_ids:
            logger.warning(
                "pending_aggregation_replay plan_run=%d — 唤醒丢失，leader 内联排空",
                rid,
            )
            plan_run_aggregation_replayed_total.labels(kind="pending_drain").inc()
            try:
                drain_plan_run_aggregation_sync(int(rid))
                replayed_drains += 1
            except Exception:
                logger.exception("pending_aggregation_replay_failed plan_run=%d", rid)

    # 副作用块残留（聚合已提交、finalize 块崩溃的窗口）。partial index 走
    # idx_plan_run_terminal_effects_pending。逐行独立会话（finalize 自提交，
    # 与批内行锁不同生命周期——对齐 drain 的会话形状）。
    # #3376 裁决：时间门槛（不做 CAS）——``ended_at`` 与 ``pending`` 在同一终态
    # 事务写死（plan_run_aggregation._finalize_plan_run），秒级内跑完的副作用块
    # 不该被恢复扫描抢跑；``ended_at < cutoff`` 对 NULL 不成立（畸形行不重放）。
    effects_cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=TERMINAL_EFFECTS_REPLAY_MIN_AGE_S
    )
    with SessionLocal() as db:
        stale_ids = (
            db.execute(
                select(PlanRun.id)
                .where(PlanRun.terminal_effects_state == "pending")
                .where(PlanRun.ended_at < effects_cutoff)
                .order_by(PlanRun.id)
                .limit(batch_size)
            )
        ).scalars().all()
    for rid in stale_ids:
        with SessionLocal() as db:
            run = db.get(PlanRun, int(rid))
            if run is None or getattr(run, "terminal_effects_state", None) != "pending":
                continue  # 已被其它路径收尾
            logger.warning(
                "terminal_effects_replay plan_run=%d — 父终态已提交、副作用块重放",
                rid,
            )
            plan_run_aggregation_replayed_total.labels(kind="terminal_effects").inc()
            try:
                finalize_parent_run_sync(run, db, applied=True)
                replayed_effects += 1
            except Exception:
                logger.exception("terminal_effects_replay_failed plan_run=%d", rid)
                try:
                    db.rollback()
                except Exception:
                    pass

    return {"replayed_drains": replayed_drains, "replayed_effects": replayed_effects}
