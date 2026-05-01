"""Session watchdog — consolidated background task for session lifecycle.

Handles:
  1. Host heartbeat timeout  → mark OFFLINE, jobs → UNKNOWN (lease stays ACTIVE)
  2. UNKNOWN grace period    → job → FAILED after grace window
  3. PENDING_TOOL timeout    → job → FAILED

Device lock expiration is now handled by Reconciler
(backend.scheduler.device_lease_reconciler), the sole handler of lease
expiration since ADR-0019 Phase 4b.

Entry point: ``session_watchdog_once()`` is invoked by APScheduler
IntervalTrigger (see ``app_scheduler.py``).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from backend.core.database import AsyncSessionLocal
from backend.models.enums import HostStatus, JobStatus
from backend.models.host import Host
from backend.models.job import JobInstance
from backend.services.aggregator import WorkflowAggregator
from backend.services.state_machine import InvalidTransitionError, JobStateMachine

logger = logging.getLogger(__name__)

_HOST_HEARTBEAT_TIMEOUT = int(os.getenv("HOST_HEARTBEAT_TIMEOUT_SECONDS", "120"))
_UNKNOWN_GRACE_SECONDS = int(os.getenv("UNKNOWN_GRACE_SECONDS", "300"))
_PENDING_TOOL_TIMEOUT_SECONDS = int(os.getenv("PENDING_TOOL_TIMEOUT_SECONDS", "600"))


async def _check_host_heartbeat_timeouts(db) -> tuple[int, int]:
    """Mark hosts OFFLINE if heartbeat exceeded, transition RUNNING jobs → UNKNOWN.

    Returns (hosts_marked_offline, jobs_transitioned).
    """
    threshold = datetime.now(timezone.utc) - timedelta(seconds=_HOST_HEARTBEAT_TIMEOUT)
    dead_hosts = (await db.execute(
        select(Host).where(
            Host.last_heartbeat < threshold,
            Host.status == HostStatus.ONLINE.value,
        )
    )).scalars().all()

    hosts_offline = 0
    affected_jobs = 0
    for host in dead_hosts:
        running_jobs = (await db.execute(
            select(JobInstance).where(
                JobInstance.host_id == host.id,
                JobInstance.status == JobStatus.RUNNING.value,
            )
        )).scalars().all()

        for job in running_jobs:
            try:
                JobStateMachine.transition(job, JobStatus.UNKNOWN, "host_heartbeat_timeout")
                job.ended_at = datetime.now(timezone.utc)
                affected_jobs += 1
            except InvalidTransitionError:
                pass

        host.status = HostStatus.OFFLINE.value
        hosts_offline += 1
        logger.warning(
            "watchdog_host_timeout: host=%s jobs_to_unknown=%d", host.id, len(running_jobs),
        )

    return hosts_offline, affected_jobs


async def _check_unknown_grace_period(db) -> int:
    """Transition UNKNOWN jobs to FAILED after grace period expires."""
    grace_deadline = datetime.now(timezone.utc) - timedelta(seconds=_UNKNOWN_GRACE_SECONDS)
    stuck_jobs = (await db.execute(
        select(JobInstance).where(
            JobInstance.status == JobStatus.UNKNOWN.value,
            JobInstance.ended_at < grace_deadline,
        )
    )).scalars().all()

    failed = 0
    for job in stuck_jobs:
        try:
            JobStateMachine.transition(job, JobStatus.FAILED, "unknown_grace_timeout")
            await WorkflowAggregator.on_job_terminal(job, db)
            failed += 1
            logger.warning(
                "watchdog_grace_expired: job=%d ended_at=%s", job.id, job.ended_at,
            )
        except InvalidTransitionError:
            pass

    return failed


async def _check_pending_tool_timeout(db) -> int:
    """Transition PENDING_TOOL jobs to FAILED after timeout."""
    deadline = datetime.now(timezone.utc) - timedelta(seconds=_PENDING_TOOL_TIMEOUT_SECONDS)
    stuck_jobs = (await db.execute(
        select(JobInstance).where(
            JobInstance.status == JobStatus.PENDING_TOOL.value,
            JobInstance.updated_at < deadline,
        )
    )).scalars().all()

    failed = 0
    for job in stuck_jobs:
        try:
            # PENDING_TOOL → PENDING → RUNNING → FAILED (via state machine)
            JobStateMachine.transition(job, JobStatus.PENDING, "pending_tool_timeout_reset")
            JobStateMachine.transition(job, JobStatus.RUNNING, "pending_tool_timeout_claim")
            JobStateMachine.transition(job, JobStatus.FAILED, "pending_tool_timeout")
            job.ended_at = datetime.now(timezone.utc)
            await WorkflowAggregator.on_job_terminal(job, db)
            failed += 1
            logger.warning(
                "watchdog_pending_tool_timeout: job=%d updated_at=%s", job.id, job.updated_at,
            )
        except InvalidTransitionError:
            pass

    return failed


async def session_watchdog_once() -> None:
    """Run all watchdog checks in a single pass."""
    async with AsyncSessionLocal() as db:
        hosts_offline, jobs_unknown = await _check_host_heartbeat_timeouts(db)
        jobs_failed = await _check_unknown_grace_period(db)
        pending_tool_failed = await _check_pending_tool_timeout(db)

        has_changes = hosts_offline or jobs_unknown or jobs_failed or pending_tool_failed
        if has_changes:
            await db.commit()
            logger.info(
                "watchdog_pass: hosts_offline=%d jobs_unknown=%d "
                "jobs_failed=%d pending_tool_timeout=%d",
                hosts_offline, jobs_unknown, jobs_failed, pending_tool_failed,
            )

