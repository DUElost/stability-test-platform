import logging
import os
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import or_, text

from ..core.database import SessionLocal
from ..models.schemas import DeviceStatus, RunStatus, Task, TaskRun, TaskStatus

logger = logging.getLogger(__name__)

DISPATCHED_TIMEOUT_SECONDS = int(os.getenv("RUN_DISPATCHED_TIMEOUT_SECONDS", "120"))
RUNNING_HEARTBEAT_TIMEOUT_SECONDS = int(os.getenv("RUN_HEARTBEAT_TIMEOUT_SECONDS", "900"))
RECYCLE_INTERVAL_SECONDS = int(os.getenv("RUN_RECYCLE_INTERVAL_SECONDS", "30"))


def _release_device_lock(db, device_id: int, run_id: int) -> None:
    db.execute(
        text(
            """
            UPDATE devices
            SET status = CASE
                    WHEN status = :busy_status THEN :online_status
                    ELSE status
                END,
                lock_run_id = NULL,
                lock_expires_at = NULL
            WHERE id = :device_id AND lock_run_id = :run_id
            """
        ),
        {
            "device_id": device_id,
            "run_id": run_id,
            "busy_status": DeviceStatus.BUSY.value,
            "online_status": DeviceStatus.ONLINE.value,
        },
    )


def _mark_timeout(db, run: TaskRun, now: datetime, reason: str) -> None:
    if run.status in {RunStatus.FINISHED, RunStatus.FAILED, RunStatus.CANCELED}:
        return
    run.status = RunStatus.FAILED
    run.error_code = "TIMEOUT"
    run.error_message = reason
    run.finished_at = now
    task = db.get(Task, run.task_id)
    if task and task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED}:
        task.status = TaskStatus.FAILED
    _release_device_lock(db, run.device_id, run.id)
    logger.warning(
        "run_timeout",
        extra={
            "run_id": run.id,
            "task_id": run.task_id,
            "status": run.status.value,
            "reason": reason,
        },
    )


def recycle_once() -> None:
    now = datetime.utcnow()
    dispatched_deadline = now - timedelta(seconds=DISPATCHED_TIMEOUT_SECONDS)
    running_deadline = now - timedelta(seconds=RUNNING_HEARTBEAT_TIMEOUT_SECONDS)
    with SessionLocal() as db:
        expired_dispatched = (
            db.query(TaskRun)
            .filter(TaskRun.status == RunStatus.DISPATCHED, TaskRun.created_at < dispatched_deadline)
            .all()
        )
        expired_running = (
            db.query(TaskRun)
            .filter(
                TaskRun.status == RunStatus.RUNNING,
                or_(
                    TaskRun.last_heartbeat_at < running_deadline,
                    (TaskRun.last_heartbeat_at.is_(None) & TaskRun.started_at.isnot(None) & (TaskRun.started_at < running_deadline)),
                    (TaskRun.last_heartbeat_at.is_(None) & TaskRun.started_at.is_(None) & (TaskRun.created_at < running_deadline)),
                ),
            )
            .all()
        )
        expired_runs = expired_dispatched + expired_running
        for run in expired_runs:
            reason = "dispatched timeout" if run.status == RunStatus.DISPATCHED else "running heartbeat timeout"
            _mark_timeout(db, run, now, reason)
        if expired_runs:
            db.commit()


def _recycler_loop() -> None:
    logger.info("recycler_started")
    while True:
        try:
            recycle_once()
        except Exception:
            logger.exception("recycler_failed")
        time.sleep(RECYCLE_INTERVAL_SECONDS)


def start_recycler() -> threading.Thread:
    thread = threading.Thread(target=_recycler_loop, name="run-recycler", daemon=True)
    thread.start()
    return thread
