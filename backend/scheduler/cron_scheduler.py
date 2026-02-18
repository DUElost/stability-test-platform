# -*- coding: utf-8 -*-
"""
Cron Scheduler Daemon — polls task_schedules and creates Task rows on schedule.
"""

import logging
import os
import threading
import time
from datetime import datetime

from sqlalchemy.orm import Session

from backend.core.database import SessionLocal
from backend.models.schemas import Task, TaskSchedule, TaskStatus

logger = logging.getLogger(__name__)

CRON_POLL_INTERVAL = float(os.getenv("CRON_POLL_INTERVAL", "30"))


def _compute_next_run(cron_expression: str, after: datetime) -> datetime:
    """Compute next run time using croniter."""
    from croniter import croniter
    cron = croniter(cron_expression, after)
    return cron.get_next(datetime)


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
                    self._create_task_from_schedule(db, sched, now)
                except Exception:
                    logger.exception("cron_schedule_execute_error schedule_id=%s", sched.id)

            if schedules:
                db.commit()
                logger.info("cron_scheduler_created_tasks count=%d", len(schedules))
        finally:
            db.close()

    def _create_task_from_schedule(self, db: Session, sched: TaskSchedule, now: datetime) -> None:
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

        sched.last_run_at = now
        sched.next_run_at = _compute_next_run(sched.cron_expression, now)
        logger.info(
            "cron_task_created schedule_id=%s task_name=%s next_run=%s",
            sched.id, task.name, sched.next_run_at,
        )


def start_cron_scheduler() -> threading.Thread:
    """Called from FastAPI startup."""
    scheduler = CronScheduler()
    return scheduler.start()
