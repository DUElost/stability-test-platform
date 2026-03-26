"""
Job Recycler — timeout recovery for JobInstance lifecycle.

Complements the session watchdog by handling:
- PENDING timeout: agent never claimed the job within DISPATCHED_TIMEOUT_SECONDS
- RUNNING timeout: job started but no completion within RUNNING_HEARTBEAT_TIMEOUT_SECONDS
- Artifact file pruning: delete physical files referenced by old StepTrace completion records

Host heartbeat timeout and device lock expiration are handled by session_watchdog.py.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from sqlalchemy import or_

from backend.core.database import SessionLocal
from backend.api.routes.websocket import schedule_broadcast
from backend.core.metrics import (
    recycler_runs,
    recycler_timeouts,
    recycler_duration,
    device_lock_released,
    task_run_state_changes,
    task_run_total,
)
from backend.models.enums import JobStatus
from backend.models.job import JobInstance, StepTrace
from backend.services.device_lock import release_lock_sync
from backend.services.state_machine import JobStateMachine, InvalidTransitionError

logger = logging.getLogger(__name__)

DISPATCHED_TIMEOUT_SECONDS = int(os.getenv("RUN_DISPATCHED_TIMEOUT_SECONDS", "120"))
RUNNING_HEARTBEAT_TIMEOUT_SECONDS = int(os.getenv("RUN_HEARTBEAT_TIMEOUT_SECONDS", "900"))
ARTIFACT_RETENTION_DAYS = int(os.getenv("ARTIFACT_RETENTION_DAYS", "30"))
RECYCLE_INTERVAL_SECONDS = int(os.getenv("RUN_RECYCLE_INTERVAL_SECONDS", "30"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_json_loads(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _mark_timeout(db, job: JobInstance, now: datetime, reason: str) -> None:
    """Transition a stuck JobInstance to FAILED and release its device lock."""
    terminal = {JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.ABORTED.value}
    if job.status in terminal:
        return

    old_status = job.status
    try:
        # State machine: PENDING cannot go directly to FAILED; two-step required
        if job.status == JobStatus.PENDING.value:
            JobStateMachine.transition(job, JobStatus.RUNNING, "recycler_auto_claim")
        JobStateMachine.transition(job, JobStatus.FAILED, reason)
    except InvalidTransitionError:
        # Force-set if state machine rejects (e.g., concurrent watchdog transition)
        job.status = JobStatus.FAILED.value
        job.status_reason = reason
        job.updated_at = now

    job.ended_at = now
    release_lock_sync(db, job.device_id, job.id)

    # Metrics
    task_run_state_changes.labels(from_state=old_status, to_state="FAILED").inc()
    task_run_total.labels(status="failed", task_type="workflow").inc()
    device_lock_released.labels(reason="timeout").inc()
    if "pending" in reason.lower():
        recycler_timeouts.labels(timeout_type="dispatched").inc()
    else:
        recycler_timeouts.labels(timeout_type="running").inc()

    logger.warning(
        "job_timeout",
        extra={
            "job_id": job.id,
            "workflow_run_id": job.workflow_run_id,
            "old_status": old_status,
            "reason": reason,
        },
    )

    # WebSocket broadcast
    schedule_broadcast("/ws/dashboard", {
        "type": "JOB_UPDATE",
        "payload": {
            "job_id": job.id,
            "workflow_run_id": job.workflow_run_id,
            "status": "FAILED",
            "error_code": "TIMEOUT",
            "message": reason,
        },
    })

    # Fire-and-forget notification
    # NOTE: run_post_completion_async is NOT called here because JobInstance
    # lacks report_json/jira_draft_json/post_processed_at columns.
    # This will be restored after Alembic migration adds those columns.
    # See ADR-0008 "Post-Completion Pipeline Migration" section.
    from backend.services.notification_service import dispatch_notification_async
    dispatch_notification_async("RUN_FAILED", {
        "run_id": job.id,
        "task_id": job.workflow_run_id,
        "task_name": f"job-{job.id}",
        "task_type": "workflow",
        "error_message": reason,
        "device_serial": str(job.device_id),
    })


# ---------------------------------------------------------------------------
# Main recycler pass
# ---------------------------------------------------------------------------

def recycle_once() -> None:
    start_time = time.time()
    now = datetime.now(timezone.utc)
    pending_deadline = now - timedelta(seconds=DISPATCHED_TIMEOUT_SECONDS)
    running_deadline = now - timedelta(seconds=RUNNING_HEARTBEAT_TIMEOUT_SECONDS)

    with SessionLocal() as db:
        # 1) PENDING timeout — agent never claimed the job
        expired_pending = (
            db.query(JobInstance)
            .filter(
                JobInstance.status == JobStatus.PENDING.value,
                JobInstance.created_at < pending_deadline,
            )
            .all()
        )

        # 2) RUNNING timeout — job started but no completion within window
        # Uses started_at as proxy; session_watchdog handles host-level heartbeat.
        expired_running = (
            db.query(JobInstance)
            .filter(
                JobInstance.status == JobStatus.RUNNING.value,
                or_(
                    (JobInstance.started_at.isnot(None)) & (JobInstance.started_at < running_deadline),
                    (JobInstance.started_at.is_(None)) & (JobInstance.created_at < running_deadline),
                ),
            )
            .all()
        )

        expired_jobs = expired_pending + expired_running
        for job in expired_jobs:
            reason = (
                "pending_timeout: agent never claimed job"
                if job.status == JobStatus.PENDING.value
                else "running_timeout: no completion within window"
            )
            _mark_timeout(db, job, now, reason)

        if expired_jobs:
            db.commit()

        # 3) Prune old artifact files
        _prune_steptrace_artifacts(db, now)

    duration = time.time() - start_time
    recycler_duration.observe(duration)
    recycler_runs.inc()


# ---------------------------------------------------------------------------
# Artifact pruning
# ---------------------------------------------------------------------------

def _prune_steptrace_artifacts(db, now: datetime) -> None:
    """Delete physical artifact files referenced by old StepTrace completion records.

    Only deletes file:// URIs. StepTrace rows are preserved (audit records).
    """
    cutoff = now - timedelta(days=ARTIFACT_RETENTION_DAYS)

    old_traces = (
        db.query(StepTrace)
        .filter(
            StepTrace.step_id == "__job__",
            StepTrace.event_type == "RUN_COMPLETE",
            StepTrace.created_at < cutoff,
        )
        .all()
    )

    if not old_traces:
        return

    file_deleted_count = 0
    for trace in old_traces:
        snapshot = _safe_json_loads(trace.output)
        artifact = snapshot.get("artifact") if isinstance(snapshot, dict) else None
        if not artifact or not isinstance(artifact, dict):
            continue
        storage_uri = artifact.get("storage_uri")
        if not storage_uri:
            continue
        try:
            parsed = urlparse(storage_uri)
            if parsed.scheme.lower() == "file":
                if parsed.netloc and parsed.path:
                    local_path = Path(f"//{parsed.netloc}{unquote(parsed.path)}")
                elif parsed.netloc and not parsed.path:
                    local_path = Path(unquote(parsed.netloc))
                else:
                    local_path = Path(unquote(parsed.path))
                if local_path.exists() and local_path.is_file():
                    os.remove(local_path)
                    file_deleted_count += 1
        except Exception as e:
            logger.warning(f"Failed to delete artifact file {storage_uri}: {e}")

    if file_deleted_count:
        logger.info(
            "steptrace_artifacts_pruned",
            extra={"traces_scanned": len(old_traces), "files_deleted": file_deleted_count},
        )


# ---------------------------------------------------------------------------
# Daemon thread
# ---------------------------------------------------------------------------

def _recycler_loop() -> None:
    logger.info("recycler_started")
    while True:
        try:
            recycle_once()
        except Exception:
            logger.exception("recycler_failed")
        time.sleep(RECYCLE_INTERVAL_SECONDS)


def start_recycler() -> threading.Thread:
    thread = threading.Thread(target=_recycler_loop, name="job-recycler", daemon=True)
    thread.start()
    return thread
