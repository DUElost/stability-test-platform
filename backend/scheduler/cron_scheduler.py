# -*- coding: utf-8 -*-
"""
Cron Schedule Checker — polls task_schedules and dispatches PlanRuns.

Refactored for APScheduler 4.x: the CronScheduler daemon thread has been
replaced by two standalone functions invoked via APScheduler IntervalTrigger:

- ``check_and_fire_schedules()``  — async, called every CRON_POLL_INTERVAL
- ``run_retention_cleanup()``     — sync, called every hour
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from backend.core.database import AsyncSessionLocal, SessionLocal
from backend.models.schedule import TaskSchedule, schedule_timestamp

logger = logging.getLogger(__name__)

CRON_POLL_INTERVAL = float(os.getenv("CRON_POLL_INTERVAL", "30"))
PATROL_TIMEOUT_MINUTES = int(os.getenv("PATROL_TIMEOUT_MINUTES", "10"))
PLAN_RUN_RETENTION_DAYS = int(os.getenv("PLAN_RUN_RETENTION_DAYS", "3"))
# ADR-0020 §"落地与后续动作 9"：同一 schedule 在该窗口内不重复触发 root PlanRun
SCHEDULE_DEDUP_WINDOW_SECONDS = float(os.getenv("SCHEDULE_DEDUP_WINDOW_SECONDS", "60"))


def _compute_next_run(cron_expression: str, after: datetime) -> datetime:
    """Compute next run time using croniter."""
    from croniter import croniter
    cron = croniter(cron_expression, after)
    return schedule_timestamp(cron.get_next(datetime))


def _next_schedule_run(cron_expression: str, after: datetime) -> datetime:
    return schedule_timestamp(_compute_next_run(cron_expression, after))


async def _dispatch_plan_async(
    plan_id: int, device_ids: list, db, *, schedule_id: int,
) -> None:
    """Dispatch a Plan using the provided async session (ADR-0020).

    schedule_id 写入 PlanRun.run_context 以便后续窗口去重查询。
    """
    from backend.services.plan_dispatcher import dispatch_plan, PlanDispatchError
    try:
        await dispatch_plan(
            plan_id=plan_id,
            device_ids=device_ids,
            triggered_by="cron",
            db=db,
            run_type="SCHEDULE",
            run_context={"schedule_id": schedule_id},
        )
    except PlanDispatchError as exc:
        logger.error("cron_dispatch_plan_error plan_id=%s: %s", plan_id, exc)


async def _recently_triggered_by_schedule(
    db, schedule_id: int, since: datetime,
) -> bool:
    """ADR-0020 schedule 抖动去重：同一 schedule 在 *since* 之后是否已产出 root PlanRun。

    通过 ``run_context @> {"schedule_id": <id>}``（PostgreSQL JSONB containment）
    + 仅匹配 root（``parent_plan_run_id IS NULL``）实现。SQLite 测试场景下
    ``run_context`` 可能是 TEXT 列，回退到字符串包含检查（仅用于测试）。
    """
    from backend.models.plan_run import PlanRun
    from sqlalchemy import text

    bind = db.get_bind() if hasattr(db, "get_bind") else None
    dialect = bind.dialect.name if bind is not None else ""
    if dialect == "postgresql":
        stmt = (
            select(PlanRun.id)
            .where(
                PlanRun.parent_plan_run_id.is_(None),
                PlanRun.started_at >= since,
                PlanRun.run_context.contains({"schedule_id": int(schedule_id)}),
            )
            .limit(1)
        )
    else:
        marker = f'"schedule_id": {int(schedule_id)}'
        stmt = (
            select(PlanRun.id)
            .where(
                PlanRun.parent_plan_run_id.is_(None),
                PlanRun.started_at >= since,
                text("CAST(run_context AS TEXT) LIKE :pat").bindparams(pat=f"%{marker}%"),
            )
            .limit(1)
        )

    row = (await db.execute(stmt)).first()
    return row is not None


async def _fire_schedule(db, sched: "TaskSchedule", now: datetime) -> None:
    """Evaluate and fire a single TaskSchedule row (ADR-0020: Plan-based)."""
    schedule_id = sched.id
    plan_id = sched.plan_id
    cron_expression = sched.cron_expression
    schedule_name = sched.name
    device_ids = list(sched.device_ids or [])

    if plan_id:
        from backend.models.plan_run import PlanRun

        # ── 1. schedule 抖动去重（ADR-0020 §"落地 9"）──
        dedup_since = now - timedelta(seconds=SCHEDULE_DEDUP_WINDOW_SECONDS)
        try:
            if await _recently_triggered_by_schedule(db, schedule_id, dedup_since):
                logger.info(
                    "cron_skip_dedup schedule_id=%s plan_id=%s window=%.1fs",
                    schedule_id, plan_id, SCHEDULE_DEDUP_WINDOW_SECONDS,
                )
                sched.next_run_at = _next_schedule_run(cron_expression, now)
                return
        except Exception:
            await db.rollback()
            logger.warning(
                "cron_dedup_check_failed schedule_id=%s — failing open",
                schedule_id, exc_info=True,
            )

        # ── 2. plan 重叠跳过（避免对同一 plan 同时多窗口运行） ──
        stale_cutoff = now - timedelta(minutes=PATROL_TIMEOUT_MINUTES)
        try:
            active_count_result = await db.execute(
                select(PlanRun)
                .where(
                    PlanRun.plan_id == plan_id,
                    PlanRun.status == "RUNNING",
                    PlanRun.started_at > stale_cutoff,
                )
            )
            active_count = len(active_count_result.scalars().all())
            if active_count > 0:
                logger.info(
                    "cron_skip_overlap schedule_id=%s plan_id=%s active_runs=%d",
                    schedule_id, plan_id, active_count,
                )
                sched.next_run_at = _next_schedule_run(cron_expression, now)
                return
        except Exception as e:
            await db.rollback()
            logger.warning(
                "overlap_check_failed schedule_id=%s, skipping dispatch (fail-closed): %s",
                schedule_id, e,
            )
            sched.next_run_at = _next_schedule_run(cron_expression, now)
            return

        await _dispatch_plan_async(
            plan_id, device_ids, db, schedule_id=schedule_id,
        )
        logger.info(
            "cron_plan_dispatched schedule_id=%s plan_id=%s",
            schedule_id, plan_id,
        )
    else:
        logger.error(
            "cron_schedule_skip_no_plan schedule_id=%s name=%s — "
            "plan_id is required",
            schedule_id, schedule_name,
        )

    sched.last_run_at = schedule_timestamp(now)
    sched.next_run_at = _next_schedule_run(cron_expression, now)
    logger.info(
        "cron_schedule_updated schedule_id=%s next_run=%s",
        schedule_id, sched.next_run_at,
    )


async def check_and_fire_schedules() -> None:
    """One tick of the cron schedule checker.

    Queries ``TaskSchedule`` rows whose ``next_run_at`` has passed and fires
    each eligible schedule.  Called by APScheduler ``IntervalTrigger``.
    """
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        # next_run_at is TIMESTAMP WITHOUT TIME ZONE; strip tz for comparison
        now_naive = schedule_timestamp(now)
        result = await db.execute(
            select(TaskSchedule).where(
                TaskSchedule.enabled == True,  # noqa: E712
                TaskSchedule.next_run_at <= now_naive,
            )
        )
        schedules = result.scalars().all()

        for sched in schedules:
            try:
                await _fire_schedule(db, sched, now)
            except Exception:
                logger.exception("cron_schedule_execute_error schedule_id=%s", sched.id)
                await db.rollback()

        if schedules:
            await db.commit()
            logger.info("cron_scheduler_fired count=%d", len(schedules))


def run_retention_cleanup() -> None:
    """Delete completed PlanRuns older than PLAN_RUN_RETENTION_DAYS (ADR-0020).

    Runs as an independent APScheduler job (sync, runs in thread-pool).
    """
    from backend.models.plan_run import PlanRun
    from backend.models.job import JobInstance, StepTrace

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=PLAN_RUN_RETENTION_DAYS)

    with SessionLocal() as db:
        try:
            stale_runs = (
                db.query(PlanRun)
                .filter(
                    PlanRun.status.in_(["SUCCESS", "FAILED", "PARTIAL_SUCCESS", "DEGRADED"]),
                    PlanRun.started_at < cutoff,
                )
                .limit(100)
                .all()
            )
            if not stale_runs:
                return

            run_ids = [r.id for r in stale_runs]
            db.query(StepTrace).filter(
                StepTrace.job_id.in_(
                    select(JobInstance.id).where(JobInstance.plan_run_id.in_(run_ids))
                )
            ).delete(synchronize_session=False)
            db.query(JobInstance).filter(
                JobInstance.plan_run_id.in_(run_ids)
            ).delete(synchronize_session=False)
            db.query(PlanRun).filter(
                PlanRun.id.in_(run_ids)
            ).delete(synchronize_session=False)
            db.commit()
            logger.info("retention_cleanup deleted runs=%d", len(stale_runs))
        except Exception:
            logger.warning("retention_cleanup failed", exc_info=True)
            db.rollback()
