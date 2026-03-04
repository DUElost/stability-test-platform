# -*- coding: utf-8 -*-
"""
Cron Scheduler Daemon — polls task_schedules and creates Task rows or dispatches
WorkflowRuns on schedule.
"""

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.core.database import SessionLocal
from backend.models.schemas import Task, TaskSchedule, TaskStatus

logger = logging.getLogger(__name__)

CRON_POLL_INTERVAL = float(os.getenv("CRON_POLL_INTERVAL", "30"))
PATROL_TIMEOUT_MINUTES = int(os.getenv("PATROL_TIMEOUT_MINUTES", "10"))
WORKFLOW_RUN_RETENTION_DAYS = int(os.getenv("WORKFLOW_RUN_RETENTION_DAYS", "3"))


def _compute_next_run(cron_expression: str, after: datetime) -> datetime:
    """Compute next run time using croniter."""
    from croniter import croniter
    cron = croniter(cron_expression, after)
    return cron.get_next(datetime)


async def _dispatch_workflow_async(wf_def_id: int, device_ids: list) -> None:
    from backend.core.database import AsyncSessionLocal
    from backend.services.dispatcher import dispatch_workflow, DispatchError
    async with AsyncSessionLocal() as db:
        try:
            await dispatch_workflow(
                workflow_def_id=wf_def_id,
                device_ids=device_ids,
                failure_threshold=0.5,
                triggered_by="cron",
                db=db,
            )
            await db.commit()
        except DispatchError as exc:
            logger.error("cron_dispatch_workflow_error wf_def_id=%s: %s", wf_def_id, exc)


class CronScheduler:
    """Background daemon that creates tasks from cron schedules."""

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self._run_loop, name="cron-scheduler", daemon=True)
        thread.start()
        logger.info("cron_scheduler_started interval=%s", CRON_POLL_INTERVAL)
        return thread

    def _run_loop(self) -> None:
        while True:
            try:
                self._tick()
            except Exception:
                logger.exception("cron_scheduler_tick_error")
            time.sleep(CRON_POLL_INTERVAL)

    def _tick(self) -> None:
        db: Session = SessionLocal()
        try:
            now = datetime.utcnow()
            schedules = (
                db.query(TaskSchedule)
                .filter(TaskSchedule.enabled == True, TaskSchedule.next_run_at <= now)
                .all()
            )

            for sched in schedules:
                try:
                    self._fire_schedule(db, sched, now)
                except Exception:
                    logger.exception("cron_schedule_execute_error schedule_id=%s", sched.id)

            if schedules:
                db.commit()
                logger.info("cron_scheduler_fired count=%d", len(schedules))

            # Cleanup stale completed WorkflowRuns beyond retention period
            self._cleanup_old_runs(db, now)
        finally:
            db.close()

    def _cleanup_old_runs(self, db: Session, now: datetime) -> None:
        """Delete completed WorkflowRuns older than WORKFLOW_RUN_RETENTION_DAYS."""
        try:
            from backend.models.workflow import WorkflowRun
            cutoff = now - timedelta(days=WORKFLOW_RUN_RETENTION_DAYS)
            stale_runs = (
                db.query(WorkflowRun)
                .filter(
                    WorkflowRun.status.in_(["SUCCESS", "FAILED", "PARTIAL_SUCCESS", "DEGRADED"]),
                    WorkflowRun.started_at < cutoff,
                )
                .limit(100)
                .all()
            )
            if stale_runs:
                for run in stale_runs:
                    db.delete(run)
                db.commit()
                logger.info("cron_cleanup_old_runs deleted=%d", len(stale_runs))
        except Exception:
            logger.warning("cron_cleanup_old_runs failed", exc_info=True)
            db.rollback()

    def _fire_schedule(self, db: Session, sched: TaskSchedule, now: datetime) -> None:
        if sched.workflow_definition_id:
            # Overlap protection: skip if same workflow has a recent RUNNING run
            # Fail-closed: if the check itself errors, skip dispatch to avoid
            # amplifying concurrent overlap risk.
            try:
                from backend.models.workflow import WorkflowRun
                stale_cutoff = now - timedelta(minutes=PATROL_TIMEOUT_MINUTES)
                active_count = db.query(WorkflowRun).filter(
                    WorkflowRun.workflow_definition_id == sched.workflow_definition_id,
                    WorkflowRun.status == "RUNNING",
                    WorkflowRun.started_at > stale_cutoff,
                ).count()
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

            # Normal dispatch
            device_ids = sched.device_ids or []
            asyncio.run(_dispatch_workflow_async(sched.workflow_definition_id, device_ids))
            logger.info(
                "cron_workflow_dispatched schedule_id=%s wf_def_id=%s",
                sched.id, sched.workflow_definition_id,
            )
        else:
            # Legacy path: create Task row
            task = Task(
                name=f"[cron] {sched.name} - {now.strftime('%Y%m%d_%H%M')}",
                type=sched.task_type,
                tool_id=sched.tool_id,
                template_id=sched.task_template_id,
                params=sched.params or {},
                target_device_id=sched.target_device_id,
                status=TaskStatus.PENDING,
                priority=0,
            )
            db.add(task)
            logger.info(
                "cron_task_created schedule_id=%s task_name=%s",
                sched.id, task.name,
            )

        sched.last_run_at = now
        sched.next_run_at = _compute_next_run(sched.cron_expression, now)
        logger.info(
            "cron_schedule_updated schedule_id=%s next_run=%s",
            sched.id, sched.next_run_at,
        )


def start_cron_scheduler() -> threading.Thread:
    """Called from FastAPI startup."""
    scheduler = CronScheduler()
    return scheduler.start()
