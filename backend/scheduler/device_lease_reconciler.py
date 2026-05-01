"""Device Lease Reconciler — ADR-0019 Phase 4a/4b.

The **sole handler** of lease expiration.  Replaces watchdog's
``_check_device_lock_expiration`` which is now disabled.

Two-phase lease expiry:
  Phase 1: RUNNING + expired-ACTIVE-lease → UNKNOWN (lease stays ACTIVE, device blocked)
  Phase 2: UNKNOWN + grace expired → release_lease + FAILED

Also handles:
  - Stale UNKNOWN jobs whose lease is already gone
  - Terminal jobs with lingering ACTIVE leases (D5)

Entry point: ``device_lease_reconcile_once()`` invoked by APScheduler
IntervalTrigger (see ``app_scheduler.py``).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from backend.core.database import AsyncSessionLocal
from backend.core.metrics import (
    reconciler_runs,
    reconciler_actions,
    expired_active_leases_gauge,
    unknown_jobs_gauge,
)
from backend.models.device_lease import DeviceLease
from backend.models.enums import JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device
from backend.models.job import JobInstance
from backend.services.aggregator import WorkflowAggregator
from backend.services.lease_manager import LeaseProjectionError, release_lease
from backend.services.state_machine import InvalidTransitionError, JobStateMachine

logger = logging.getLogger(__name__)

_UNKNOWN_GRACE_SECONDS = int(os.getenv("UNKNOWN_GRACE_SECONDS", "300"))

# ── Phase 4b: terminal statuses for D5 cleanup.  Does NOT include UNKNOWN —
#    UNKNOWN must go through the grace-period branch.
_FINAL_STATUSES: set[str] = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.ABORTED.value,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Check 1: expired ACTIVE leases → UNKNOWN / FAILED
# ═══════════════════════════════════════════════════════════════════════════════

async def _reconcile_expired_leases(db) -> tuple[int, int, int]:
    """Process all expired ACTIVE JOB leases.

    Returns (unknown_count, failed_count, terminal_released_count).
    """
    now = datetime.now(timezone.utc)

    expired = (await db.execute(
        select(DeviceLease).where(
            DeviceLease.status == LeaseStatus.ACTIVE.value,
            DeviceLease.lease_type == LeaseType.JOB.value,
            DeviceLease.expires_at < now,
        )
    )).scalars().all()

    unknown_count = 0
    failed_count = 0
    terminal_released_count = 0

    for lease in expired:
        job_id = lease.job_id
        device_id = lease.device_id

        if job_id is None:
            # Orphan lease (no associated job) — release it directly
            lease.status = LeaseStatus.RELEASED.value
            lease.released_at = now
            logger.warning(
                "reconciler_orphan_lease_released device=%s job=None", device_id,
            )
            terminal_released_count += 1
            continue

        try:
            job = await db.get(JobInstance, job_id)
        except Exception:
            logger.warning("reconciler_job_load_failed job=%s", job_id, exc_info=True)
            continue

        if job is None:
            # Orphan lease (job deleted, but FK should prevent this) — release it
            try:
                await release_lease(db, device_id, job_id, LeaseType.JOB)
            except LeaseProjectionError:
                await _fallback_release_lease(db, lease)
            logger.warning(
                "reconciler_orphan_lease_released device=%s job=%s", device_id, job_id,
            )
            terminal_released_count += 1
            continue

        if job.status in _FINAL_STATUSES:
            # D5: terminal job with lingering ACTIVE lease
            try:
                await release_lease(db, device_id, job_id, LeaseType.JOB)
            except LeaseProjectionError:
                await _fallback_release_lease(db, lease)
            logger.warning(
                "reconciler_terminal_job_active_lease device=%s job=%s status=%s",
                device_id, job_id, job.status,
            )
            terminal_released_count += 1
            continue

        if job.status == JobStatus.RUNNING.value:
            # Phase 1: RUNNING → UNKNOWN, keep lease ACTIVE (blocking)
            try:
                JobStateMachine.transition(job, JobStatus.UNKNOWN, "lease_expired")
                job.ended_at = now  # REQUIRED: grace period & recovery depend on this
                await db.flush()
                logger.warning(
                    "reconciler_lease_expired_running_to_unknown device=%s job=%s",
                    device_id, job_id,
                )
                unknown_count += 1
            except InvalidTransitionError:
                logger.debug(
                    "reconciler_skip_invalid_transition device=%s job=%s status=%s",
                    device_id, job_id, job.status,
                )
            continue

        if job.status == JobStatus.UNKNOWN.value:
            # Phase 2: UNKNOWN + grace expired → release + FAILED
            grace_deadline = now - timedelta(seconds=_UNKNOWN_GRACE_SECONDS)
            if job.ended_at and job.ended_at < grace_deadline:
                try:
                    await release_lease(db, device_id, job_id, LeaseType.JOB)
                except LeaseProjectionError:
                    await _fallback_release_lease(db, lease)

                try:
                    JobStateMachine.transition(job, JobStatus.FAILED, "unknown_grace_timeout")
                    await WorkflowAggregator.on_job_terminal(job, db)
                    logger.warning(
                        "reconciler_unknown_grace_released device=%s job=%s ended_at=%s",
                        device_id, job_id, job.ended_at,
                    )
                    failed_count += 1
                except InvalidTransitionError:
                    pass
            # else: still within grace — do nothing
            continue

        # Other statuses (PENDING, PENDING_TOOL, etc.) — skip

    return unknown_count, failed_count, terminal_released_count


# ═══════════════════════════════════════════════════════════════════════════════
# Check 2: stale UNKNOWN jobs whose lease is already gone
# ═══════════════════════════════════════════════════════════════════════════════

async def _reconcile_stale_unknown_jobs(db) -> int:
    """Finalize UNKNOWN jobs past grace whose ACTIVE lease has already
    been released (e.g. by watchdog before it was disabled, or by a prior
    Reconciler pass that failed to transition the job).
    """
    now = datetime.now(timezone.utc)
    grace_deadline = now - timedelta(seconds=_UNKNOWN_GRACE_SECONDS)

    stale = (await db.execute(
        select(JobInstance).where(
            JobInstance.status == JobStatus.UNKNOWN.value,
            JobInstance.ended_at < grace_deadline,
        )
    )).scalars().all()

    failed = 0
    for job in stale:
        try:
            # If there's still an ACTIVE lease, release it
            active_lease = (await db.execute(
                select(DeviceLease).where(
                    DeviceLease.device_id == job.device_id,
                    DeviceLease.job_id == job.id,
                    DeviceLease.lease_type == LeaseType.JOB.value,
                    DeviceLease.status == LeaseStatus.ACTIVE.value,
                )
            )).scalars().first()

            if active_lease is not None:
                try:
                    await release_lease(db, job.device_id, job.id, LeaseType.JOB)
                except LeaseProjectionError:
                    await _fallback_release_lease(db, active_lease)

            JobStateMachine.transition(job, JobStatus.FAILED, "unknown_grace_timeout")
            await WorkflowAggregator.on_job_terminal(job, db)
            failed += 1
            logger.warning(
                "reconciler_stale_unknown_finalized device=%s job=%s ended_at=%s",
                job.device_id, job.id, job.ended_at,
            )
        except InvalidTransitionError:
            pass

    return failed


# ═══════════════════════════════════════════════════════════════════════════════
# Check 3: terminal jobs with lingering ACTIVE leases (D5)
# ═══════════════════════════════════════════════════════════════════════════════

async def _reconcile_terminal_job_active_leases(db) -> int:
    """Release ACTIVE JOB leases for jobs that are already in a terminal state.

    Uses an explicit JOIN to find (lease, job) pairs where the lease is still
    ACTIVE but the job has finished.  Does NOT change the job status.
    """
    rows = (await db.execute(
        select(DeviceLease, JobInstance.status)
        .join(JobInstance, JobInstance.id == DeviceLease.job_id)
        .where(
            DeviceLease.status == LeaseStatus.ACTIVE.value,
            DeviceLease.lease_type == LeaseType.JOB.value,
            JobInstance.status.in_(_FINAL_STATUSES),
        )
    )).all()

    released = 0
    for lease, _job_status in rows:
        try:
            await release_lease(db, lease.device_id, lease.job_id, LeaseType.JOB)
        except LeaseProjectionError:
            await _fallback_release_lease(db, lease)

        logger.warning(
            "reconciler_terminal_job_active_lease_released device=%s job=%s status=%s",
            lease.device_id, lease.job_id, _job_status,
        )
        released += 1

    return released


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

async def _fallback_release_lease(db, lease: DeviceLease) -> None:
    """Fallback: release a lease via ORM attribute mutation.

    Used when release_lease() raises LeaseProjectionError (device projection
    failed).  Avoids asyncpg MissingGreenlet issues by mutating the ORM object
    directly instead of issuing a Core UPDATE after savepoint rollback.
    """
    dl = await db.get(DeviceLease, lease.id)
    if dl is not None and dl.status == LeaseStatus.ACTIVE.value:
        dl.status = LeaseStatus.RELEASED.value
        dl.released_at = datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

async def device_lease_reconcile_once() -> None:
    """Run all reconciler checks in a fixed order.

    Each check runs in its own transaction: commit on success, rollback on
    failure.  One check failing does not block the next.
    """
    checks: list[tuple[str, callable]] = [
        ("expired_leases", _reconcile_expired_leases),
        ("stale_unknown", _reconcile_stale_unknown_jobs),
        ("terminal_job_active_lease", _reconcile_terminal_job_active_leases),
    ]

    for label, check_fn in checks:
        async with AsyncSessionLocal() as db:
            try:
                result = await check_fn(db)
                await db.commit()
                _record_check(label, "success", result)
            except Exception:
                await db.rollback()
                logger.exception("reconciler_check_failed check=%s", label)
                _record_check(label, "error", None)


def _record_check(label: str, outcome: str, result) -> None:
    """Record reconciler metrics for a single check."""
    try:
        reconciler_runs.labels(check=label, outcome=outcome).inc()

        if label == "expired_leases" and result is not None:
            unknown, failed, terminal = result
            if unknown:
                reconciler_actions.labels(action="to_unknown", reason="lease_expired").inc(unknown)
            if failed:
                reconciler_actions.labels(action="to_failed", reason="unknown_grace_timeout").inc(failed)
            if terminal:
                reconciler_actions.labels(
                    action="release_lease", reason="terminal_job_active_lease"
                ).inc(terminal)
        elif label == "stale_unknown" and result:
            reconciler_actions.labels(action="to_failed", reason="unknown_grace_timeout").inc(result)
        elif label == "terminal_job_active_lease" and result:
            reconciler_actions.labels(
                action="release_lease", reason="terminal_job_active_lease"
            ).inc(result)
    except Exception:
        logger.debug("reconciler_metrics_failed", exc_info=True)
