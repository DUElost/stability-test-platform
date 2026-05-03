# -*- coding: utf-8 -*-
"""
Cron Schedule Checker — polls task_schedules and dispatches WorkflowRuns.

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
from backend.models.schedule import TaskSchedule

logger = logging.getLogger(__name__)

CRON_POLL_INTERVAL = float(os.getenv("CRON_POLL_INTERVAL", "30"))
PATROL_TIMEOUT_MINUTES = int(os.getenv("PATROL_TIMEOUT_MINUTES", "10"))
WORKFLOW_RUN_RETENTION_DAYS = int(os.getenv("WORKFLOW_RUN_RETENTION_DAYS", "3"))


def _compute_next_run(cron_expression: str, after: datetime) -> datetime:
    """Compute next run time using croniter."""
    from croniter import croniter
    cron = croniter(cron_expression, after)
    return cron.get_next(datetime)


async def _dispatch_workflow_async(wf_def_id: int, device_ids: list, db) -> None:
    """Dispatch a workflow using the provided async session."""
    from backend.services.dispatcher import dispatch_workflow, DispatchError
    try:
        await dispatch_workflow(
            workflow_def_id=wf_def_id,
            device_ids=device_ids,
            failure_threshold=0.5,
            triggered_by="cron",
            db=db,
        )
    except DispatchError as exc:
        logger.error("cron_dispatch_workflow_error wf_def_id=%s: %s", wf_def_id, exc)


async def _fire_schedule(db, sched: "TaskSchedule", now: datetime) -> None:
    """Evaluate and fire a single TaskSchedule row."""
    if sched.workflow_definition_id:
        from backend.models.workflow import WorkflowRun

        stale_cutoff = now - timedelta(minutes=PATROL_TIMEOUT_MINUTES)
        try:
            active_count_result = await db.execute(
                select(WorkflowRun)
                .where(
                    WorkflowRun.workflow_definition_id == sched.workflow_definition_id,
                    WorkflowRun.status == "RUNNING",
                    WorkflowRun.started_at > stale_cutoff,
                )
            )
            active_count = len(active_count_result.scalars().all())
            if active_count > 0:
                logger.info(
                    "cron_skip_overlap schedule_id=%s wf_def_id=%s active_runs=%d",
                    sched.id, sched.workflow_definition_id, active_count,
                )
                sched.next_run_at = _compute_next_run(sched.cron_expression, now)
                return
        except Exception as e:
            logger.warning(
                "overlap_check_failed schedule_id=%s, skipping dispatch (fail-closed): %s",
                sched.id, e,
            )
            sched.next_run_at = _compute_next_run(sched.cron_expression, now)
            return

        device_ids = sched.device_ids or []
        await _dispatch_workflow_async(sched.workflow_definition_id, device_ids, db)
        logger.info(
            "cron_workflow_dispatched schedule_id=%s wf_def_id=%s",
            sched.id, sched.workflow_definition_id,
        )
    else:
        logger.error(
            "cron_schedule_skip_no_workflow schedule_id=%s name=%s — "
            "workflow_definition_id is required; legacy Task creation "
            "path has been removed (see ADR-0008)",
            sched.id, sched.name,
        )

    sched.last_run_at = now
    sched.next_run_at = _compute_next_run(sched.cron_expression, now)
    logger.info(
        "cron_schedule_updated schedule_id=%s next_run=%s",
        sched.id, sched.next_run_at,
    )


async def check_and_fire_schedules() -> None:
    """One tick of the cron schedule checker.

    Queries ``TaskSchedule`` rows whose ``next_run_at`` has passed and fires
    each eligible schedule.  Called by APScheduler ``IntervalTrigger``.
    """
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        # next_run_at is TIMESTAMP WITHOUT TIME ZONE; strip tz for comparison
        now_naive = now.replace(tzinfo=None)
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

        if schedules:
            await db.commit()
            logger.info("cron_scheduler_fired count=%d", len(schedules))


def run_retention_cleanup() -> None:
    """Delete completed WorkflowRuns older than WORKFLOW_RUN_RETENTION_DAYS.

    Runs as an independent APScheduler job (sync, runs in thread-pool).
    """
    from backend.models.workflow import WorkflowRun
    from backend.models.job import JobInstance, StepTrace

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=WORKFLOW_RUN_RETENTION_DAYS)

    with SessionLocal() as db:
        try:
            stale_runs = (
                db.query(WorkflowRun)
                .filter(
                    WorkflowRun.status.in_(["SUCCESS", "FAILED", "PARTIAL_SUCCESS", "DEGRADED"]),
                    WorkflowRun.started_at < cutoff,
                )
                .limit(100)
                .all()
            )
            if not stale_runs:
                return

            run_ids = [r.id for r in stale_runs]
            db.query(StepTrace).filter(
                StepTrace.job_id.in_(
                    select(JobInstance.id).where(JobInstance.workflow_run_id.in_(run_ids))
                )
            ).delete(synchronize_session=False)
            db.query(JobInstance).filter(
                JobInstance.workflow_run_id.in_(run_ids)
            ).delete(synchronize_session=False)
            db.query(WorkflowRun).filter(
                WorkflowRun.id.in_(run_ids)
            ).delete(synchronize_session=False)
            db.commit()
            logger.info("retention_cleanup deleted runs=%d", len(stale_runs))
        except Exception:
            logger.warning("retention_cleanup failed", exc_info=True)
            db.rollback()
