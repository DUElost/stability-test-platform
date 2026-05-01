"""
Job Recycler — timeout recovery for JobInstance lifecycle.

Complements the session watchdog and Reconciler by handling:
- PENDING timeout: agent never claimed the job → FAILED + release lease
- RUNNING timeout: job lost contact → UNKNOWN (lease stays ACTIVE; Reconciler finalizes)
- Artifact file pruning: delete physical files referenced by old StepTrace records

Host heartbeat timeout is handled by session_watchdog.py.
Lease expiration is handled by device_lease_reconciler.py (ADR-0019 Phase 4b).

Entry point: ``recycle_once()`` is invoked by APScheduler IntervalTrigger
(see ``app_scheduler.py``).
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from backend.core.database import SessionLocal
from backend.realtime.socketio_server import schedule_emit
from backend.core.metrics import (
    recycler_runs,
    recycler_timeouts,
    recycler_duration,
    device_lock_released,
    task_run_state_changes,
    task_run_total,
)
from backend.models.enums import JobStatus, LeaseType
from backend.models.job import JobInstance, StepTrace
from backend.services.lease_manager import LeaseProjectionError, release_lease_sync
from backend.services.state_machine import JobStateMachine, InvalidTransitionError

logger = logging.getLogger(__name__)

DISPATCHED_TIMEOUT_SECONDS = int(os.getenv("RUN_DISPATCHED_TIMEOUT_SECONDS", "120"))
RUNNING_HEARTBEAT_TIMEOUT_SECONDS = int(os.getenv("RUN_HEARTBEAT_TIMEOUT_SECONDS", "900"))
ARTIFACT_RETENTION_DAYS = int(os.getenv("ARTIFACT_RETENTION_DAYS", "30"))



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


def _mark_pending_timeout(db, job: JobInstance, now: datetime, reason: str) -> None:
    """PENDING timeout → FAILED + release lease (defensive, normally no-op).

    ADR-0019 Phase 4c: PENDING handling unchanged — agent never claimed,
    so no lease existed in the normal path. release_lease_sync is kept as
    a safety net.
    """
    _terminal = {
        JobStatus.COMPLETED.value, JobStatus.FAILED.value,
        JobStatus.ABORTED.value, JobStatus.UNKNOWN.value,
    }
    if job.status in _terminal:
        return
    if job.status != JobStatus.PENDING.value:
        return  # Phase 4c: only PENDING; RUNNING handled by _mark_running_timeout

    old_status = job.status
    try:
        JobStateMachine.transition(job, JobStatus.RUNNING, "recycler_auto_claim")
        JobStateMachine.transition(job, JobStatus.FAILED, reason)
    except InvalidTransitionError:
        logger.info(
            "recycler_skip_transition job=%d current=%s reason=%s",
            job.id, job.status, reason,
        )
        return

    job.ended_at = now
    try:
        release_lease_sync(db, job.device_id, job.id, LeaseType.JOB)
    except LeaseProjectionError:
        logger.warning(
            "recycler_projection_failed: device=%d job=%d — "
            "device table out of sync, continuing without projection",
            job.device_id, job.id,
        )
        from sqlalchemy import update as _update
        from backend.models.device_lease import DeviceLease
        from backend.models.enums import LeaseStatus
        db.execute(
            _update(DeviceLease)
            .where(
                DeviceLease.device_id == job.device_id,
                DeviceLease.job_id == job.id,
                DeviceLease.lease_type == LeaseType.JOB.value,
                DeviceLease.status == LeaseStatus.ACTIVE.value,
            )
            .values(
                status=LeaseStatus.RELEASED.value,
                released_at=now,
            )
        )

    try:
        from backend.services.aggregator_sync import workflow_aggregator_sync
        workflow_aggregator_sync(job, db)
    except Exception as e:
        logger.warning("recycler_aggregation_failed job=%d: %s", job.id, e)

    task_run_state_changes.labels(from_state=old_status, to_state="FAILED").inc()
    task_run_total.labels(status="failed", task_type="workflow").inc()
    device_lock_released.labels(reason="timeout").inc()
    recycler_timeouts.labels(timeout_type="dispatched").inc()

    logger.warning(
        "job_timeout",
        extra={
            "job_id": job.id,
            "workflow_run_id": job.workflow_run_id,
            "old_status": old_status,
            "reason": reason,
        },
    )

    schedule_emit("job_update", {
        "type": "JOB_UPDATE",
        "payload": {
            "job_id": job.id,
            "workflow_run_id": job.workflow_run_id,
            "status": "FAILED",
            "error_code": "TIMEOUT",
            "message": reason,
        },
    }, namespace="/dashboard")


def _mark_running_timeout(db, job: JobInstance, now: datetime, reason: str) -> None:
    """RUNNING timeout → UNKNOWN (ADR-0019 Phase 4c).

    Lease stays ACTIVE — the device remains blocked. Reconciler will
    finalize (UNKNOWN→FAILED + release lease) after the grace period.

    Does NOT call release_lease_sync, workflow aggregation, or emit
    FAILED notification.
    """
    if job.status != JobStatus.RUNNING.value:
        return

    old_status = job.status
    try:
        JobStateMachine.transition(job, JobStatus.UNKNOWN, reason)
    except InvalidTransitionError:
        logger.info(
            "recycler_skip_transition job=%d current=%s reason=%s",
            job.id, job.status, reason,
        )
        return

    job.ended_at = now

    task_run_state_changes.labels(from_state=old_status, to_state="UNKNOWN").inc()
    recycler_timeouts.labels(timeout_type="running").inc()

    logger.warning(
        "job_timeout_to_unknown",
        extra={
            "job_id": job.id,
            "workflow_run_id": job.workflow_run_id,
            "old_status": old_status,
            "reason": reason,
        },
    )

    schedule_emit("job_update", {
        "type": "JOB_UPDATE",
        "payload": {
            "job_id": job.id,
            "workflow_run_id": job.workflow_run_id,
            "status": "UNKNOWN",
            "error_code": "TIMEOUT",
            "message": reason,
        },
    }, namespace="/dashboard")


# ---------------------------------------------------------------------------
# Main recycler pass
# ---------------------------------------------------------------------------

_POST_COMPLETION_GRACE_SECONDS = int(os.getenv("POST_COMPLETION_GRACE_SECONDS", "120"))


def _fill_deferred_post_completions(db, now: datetime) -> int:
    """Enqueue post-completion via SAQ for terminal jobs the primary path missed.

    Waits POST_COMPLETION_GRACE_SECONDS after ended_at before triggering,
    giving the agent's outbox drain a window to be the first writer.
    """
    from backend.tasks.saq_worker import enqueue_sync

    grace_deadline = now - timedelta(seconds=_POST_COMPLETION_GRACE_SECONDS)
    terminal_statuses = [
        JobStatus.COMPLETED.value, JobStatus.FAILED.value,
        JobStatus.ABORTED.value,
    ]
    orphan_jobs = (
        db.query(JobInstance)
        .filter(
            JobInstance.status.in_(terminal_statuses),
            JobInstance.post_processed_at.is_(None),
            JobInstance.ended_at.isnot(None),
            JobInstance.ended_at < grace_deadline,
        )
        .limit(10)
        .all()
    )

    filled = 0
    for job in orphan_jobs:
        try:
            enqueue_sync(
                "post_completion_task",
                key=f"pc:{job.id}",
                timeout=120,
                retries=3,
                job_id=job.id,
            )
            filled += 1
            logger.info("deferred_post_completion_enqueued job=%d", job.id)

            event_type = "RUN_FAILED" if job.status == JobStatus.FAILED.value else "RUN_COMPLETED"
            enqueue_sync(
                "send_notification_task",
                key=f"notif:{job.id}:{event_type}",
                event_type=event_type,
                context={
                    "run_id": job.id,
                    "task_id": job.workflow_run_id,
                    "task_name": f"job-{job.id}",
                    "task_type": "workflow",
                    "error_message": job.status_reason or "",
                    "device_serial": str(job.device_id),
                },
            )
        except Exception:
            logger.exception("deferred_post_completion_enqueue_failed job=%d", job.id)

    return filled


def recycle_once() -> None:
    start_time = time.time()
    now = datetime.now(timezone.utc)
    pending_deadline = now - timedelta(seconds=DISPATCHED_TIMEOUT_SECONDS)
    running_deadline = now - timedelta(seconds=RUNNING_HEARTBEAT_TIMEOUT_SECONDS)

    with SessionLocal() as db:
        # 1) PENDING timeout — agent never claimed the job → FAILED
        expired_pending = (
            db.query(JobInstance)
            .filter(
                JobInstance.status == JobStatus.PENDING.value,
                JobInstance.created_at < pending_deadline,
            )
            .all()
        )

        for job in expired_pending:
            _mark_pending_timeout(
                db, job, now, "pending_timeout: agent never claimed job",
            )

        # 2) RUNNING timeout → UNKNOWN (Phase 4c). Lease stays ACTIVE.
        expired_running = (
            db.query(JobInstance)
            .filter(
                JobInstance.status == JobStatus.RUNNING.value,
                JobInstance.updated_at < running_deadline,
            )
            .all()
        )

        for job in expired_running:
            _mark_running_timeout(
                db, job, now, "running_timeout: no completion within window",
            )

        if expired_pending or expired_running:
            db.commit()

        # 3) Deferred post-completion for orphan terminal jobs
        filled = _fill_deferred_post_completions(db, now)
        if filled:
            logger.info("deferred_post_completions_filled count=%d", filled)

        # 4) Prune old artifact files
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

