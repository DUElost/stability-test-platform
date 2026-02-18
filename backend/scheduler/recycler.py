import logging
import os
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import or_, text

from backend.core.database import SessionLocal
from backend.api.routes.websocket import schedule_broadcast
from backend.core.metrics import (
    recycler_runs,
    recycler_timeouts,
    recycler_duration,
    device_lock_released,
    host_heartbeat_missed,
    task_run_state_changes,
    task_run_total,
)
from backend.models.schemas import Device, DeviceStatus, HostStatus, RunStatus, Task, TaskRun, TaskStatus

logger = logging.getLogger(__name__)

DISPATCHED_TIMEOUT_SECONDS = int(os.getenv("RUN_DISPATCHED_TIMEOUT_SECONDS", "120"))
RUNNING_HEARTBEAT_TIMEOUT_SECONDS = int(os.getenv("RUN_HEARTBEAT_TIMEOUT_SECONDS", "900"))
METRIC_RETENTION_DAYS = int(os.getenv("METRIC_RETENTION_DAYS", "7"))
HOST_HEARTBEAT_TIMEOUT_SECONDS = int(os.getenv("HOST_HEARTBEAT_TIMEOUT_SECONDS", "300"))
RECYCLE_INTERVAL_SECONDS = int(os.getenv("RUN_RECYCLE_INTERVAL_SECONDS", "30"))


def _check_host_heartbeat_timeout(db, now: datetime) -> int:
    """Mark hosts as OFFLINE if no heartbeat received within timeout period."""
    from backend.models.schemas import Host

    offline_deadline = now - timedelta(seconds=HOST_HEARTBEAT_TIMEOUT_SECONDS)

    expired_hosts = (
        db.query(Host)
        .filter(
            Host.status == HostStatus.ONLINE,
            or_(
                Host.last_heartbeat.is_(None),
                Host.last_heartbeat < offline_deadline,
            ),
        )
        .all()
    )

    for host in expired_hosts:
        host.status = HostStatus.OFFLINE
        host_heartbeat_missed.labels(host_id=str(host.id)).inc()
        recycler_timeouts.labels(timeout_type="host").inc()
        logger.info(
            "host_offline_by_heartbeat_timeout",
            extra={
                "host_id": host.id,
                "host_name": host.name,
                "last_heartbeat": host.last_heartbeat.isoformat() if host.last_heartbeat else None,
            },
        )
    return len(expired_hosts)


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
    old_status = run.status
    run.status = RunStatus.FAILED
    run.error_code = "TIMEOUT"
    run.error_message = reason
    run.finished_at = now
    task = db.get(Task, run.task_id)
    if task and task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED}:
        task.status = TaskStatus.FAILED
    _release_device_lock(db, run.device_id, run.id)
    # Record metrics
    task_run_state_changes.labels(from_state=old_status.value, to_state="failed").inc()
    task_run_total.labels(status="failed", task_type=task.type if task else "unknown").inc()
    device_lock_released.labels(reason="timeout").inc()
    if "dispatched" in reason:
        recycler_timeouts.labels(timeout_type="dispatched").inc()
    else:
        recycler_timeouts.labels(timeout_type="running").inc()
    logger.warning(
        "run_timeout",
        extra={
            "run_id": run.id,
            "task_id": run.task_id,
            "status": run.status.value,
            "reason": reason,
        },
    )
    # Broadcast timeout event via WebSocket
    schedule_broadcast("/ws/dashboard", {
        "type": "RUN_UPDATE",
        "payload": {
            "run_id": run.id,
            "task_id": run.task_id,
            "status": "FAILED",
            "error_code": "TIMEOUT",
            "message": reason,
        },
    })
    # Fire-and-forget: auto-generate report + JIRA draft
    from backend.services.post_completion import run_post_completion_async
    run_post_completion_async(run.id)

    # Dispatch RUN_FAILED notification for timeout
    from backend.services.notification_service import dispatch_notification_async
    # 获取设备序列号（通过关联的 device 对象）
    device_serial = run.device.serial if run.device else str(run.device_id)
    dispatch_notification_async("RUN_FAILED", {
        "run_id": run.id,
        "task_id": run.task_id,
        "task_name": task.name if task else "unknown",
        "task_type": task.type if task else "unknown",
        "error_message": reason,
        "device_serial": device_serial,
    })


def _check_device_lock_expiration(db, now: datetime) -> int:
    """
    检查并释放过期的设备锁
    当 Agent 续期失败或崩溃时，锁会过期，需要回收
    """
    expired_locks = (
        db.query(Device)
        .filter(
            Device.lock_run_id.isnot(None),
            or_(
                Device.lock_expires_at.is_(None),
                Device.lock_expires_at < now,
            ),
        )
        .all()
    )

    released_count = 0
    for device in expired_locks:
        run_id = device.lock_run_id

        # 查找关联的 RUNNING 任务并标记为失败
        if run_id:
            run = db.get(TaskRun, run_id)
            if run and run.status == RunStatus.RUNNING:
                _mark_timeout(
                    db, run, now,
                    "device lock expired, agent may be offline"
                )

        # 释放设备锁
        device.status = DeviceStatus.ONLINE
        device.lock_run_id = None
        device.lock_expires_at = None
        released_count += 1

        # Record metrics
        device_lock_released.labels(reason="expired").inc()
        recycler_timeouts.labels(timeout_type="device_lock").inc()

        logger.warning(
            "device_lock_expired_released",
            extra={
                "device_id": device.id,
                "device_serial": device.serial,
                "run_id": run_id,
            },
        )

    return released_count


def recycle_once() -> None:
    start_time = time.time()
    now = datetime.utcnow()
    dispatched_deadline = now - timedelta(seconds=DISPATCHED_TIMEOUT_SECONDS)
    running_deadline = now - timedelta(seconds=RUNNING_HEARTBEAT_TIMEOUT_SECONDS)

    with SessionLocal() as db:
        # Check host heartbeat timeout first
        offline_hosts = _check_host_heartbeat_timeout(db, now)

        # Check device lock expiration (for lock renewal mechanism)
        expired_locks = _check_device_lock_expiration(db, now)

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
        if expired_runs or offline_hosts or expired_locks:
            db.commit()

        # Prune old metric snapshots
        _prune_metric_snapshots(db, now)

    # Record recycler metrics
    duration = time.time() - start_time
    recycler_duration.observe(duration)
    recycler_runs.inc()


def _prune_metric_snapshots(db, now: datetime) -> None:
    """Delete device metric snapshots older than retention period."""
    cutoff = now - timedelta(days=METRIC_RETENTION_DAYS)
    result = db.execute(
        text("DELETE FROM device_metric_snapshots WHERE timestamp < :cutoff"),
        {"cutoff": cutoff},
    )
    if result.rowcount > 0:
        db.commit()
        logger.info("metric_snapshots_pruned", extra={"count": result.rowcount})


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
