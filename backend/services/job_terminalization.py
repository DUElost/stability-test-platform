"""ADR-0026 §6 — single job terminalization + O(1) counter bump.

Every path that first puts a Job into COMPLETED / FAILED / ABORTED must call
``on_job_terminal`` (async) or ``on_job_terminal_sync`` afterwards in the
**same transaction**. The service:

1. Locks ``plan_run`` with ``FOR NO KEY UPDATE`` (deadlock-safe vs FK KEY SHARE)
2. Atomically increments the five O(1) counters on ``plan_run`` (+ ``plan_run_host``)
3. When ``terminal_job_count == total_job_count`` (and total > 0), applies
   PlanRun aggregation from counters — no full sibling-job SELECT
4. Falls back to the legacy full-job scan when ``total_job_count == 0``
   (pre-P2 / empty runs)

Idempotency is the caller's duty: only invoke on the *first* transition into
a terminal job status (``complete_job`` already short-circuits replays).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.core.metrics import record_plan_run_aggregation_duration
from backend.models.enums import JobStatus
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun, PlanRunHost
from backend.services.plan_run_aggregation import (
    apply_plan_run_aggregation,
    apply_plan_run_aggregation_from_counters,
)

logger = logging.getLogger(__name__)

_TERMINAL = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.ABORTED.value,
}


def _bump_counters(run: PlanRun, job: JobInstance) -> None:
    """Increment plan_run (+ matching plan_run_host) counters for *job*."""
    status = job.status
    run.terminal_job_count = int(run.terminal_job_count or 0) + 1
    if status == JobStatus.COMPLETED.value:
        run.completed_job_count = int(run.completed_job_count or 0) + 1
    elif status == JobStatus.FAILED.value:
        run.failed_job_count = int(run.failed_job_count or 0) + 1
    elif status == JobStatus.ABORTED.value:
        run.aborted_job_count = int(run.aborted_job_count or 0) + 1


def _bump_host_counters(prh: PlanRunHost, job: JobInstance) -> None:
    status = job.status
    prh.terminal_job_count = int(prh.terminal_job_count or 0) + 1
    if status == JobStatus.COMPLETED.value:
        prh.completed_job_count = int(prh.completed_job_count or 0) + 1
    elif status == JobStatus.FAILED.value:
        prh.failed_job_count = int(prh.failed_job_count or 0) + 1
    elif status == JobStatus.ABORTED.value:
        prh.aborted_job_count = int(prh.aborted_job_count or 0) + 1


async def on_job_terminal(
    job: JobInstance, db: AsyncSession,
) -> tuple[bool, Optional[str]]:
    """Async entry — Agent ``/complete``, session_watchdog, lease reconciler."""
    if job.status not in _TERMINAL:
        logger.warning(
            "on_job_terminal_skipped_non_terminal job=%s status=%s",
            job.id, job.status,
        )
        return False, None

    run = (
        await db.execute(
            select(PlanRun)
            .where(PlanRun.id == job.plan_run_id)
            .with_for_update(key_share=True)
        )
    ).scalar_one_or_none()
    if run is None:
        return False, None

    _bump_counters(run, job)

    if job.host_id:
        prh = (
            await db.execute(
                select(PlanRunHost).where(
                    PlanRunHost.plan_run_id == run.id,
                    PlanRunHost.host_id == job.host_id,
                )
            )
        ).scalar_one_or_none()
        if prh is not None:
            _bump_host_counters(prh, job)

    async def _load_jobs():
        result = await db.execute(
            select(JobInstance).where(JobInstance.plan_run_id == run.id)
        )
        return result.scalars().all()

    total = int(run.total_job_count or 0)
    if total > 0:
        t0 = time.perf_counter()
        applied = apply_plan_run_aggregation_from_counters(run)
        record_plan_run_aggregation_duration(
            time.perf_counter() - t0, "counters",
        )
        if applied:
            from backend.services.plan_chain_trigger import trigger_next_plan
            from backend.services.dedup_scan import (
                should_trigger_dedup,
                enqueue_dedup_terminal_async,
            )
            await trigger_next_plan(run, db)
            if should_trigger_dedup(run.status):
                await enqueue_dedup_terminal_async(run.id)
        return applied, run.status if applied else None

    jobs = await _load_jobs()
    t0 = time.perf_counter()
    applied = apply_plan_run_aggregation(run, jobs)
    record_plan_run_aggregation_duration(
        time.perf_counter() - t0, "full_scan",
    )
    if applied:
        from backend.services.plan_chain_trigger import trigger_next_plan
        from backend.services.dedup_scan import (
            should_trigger_dedup,
            enqueue_dedup_terminal_async,
        )
        await trigger_next_plan(run, db)
        if should_trigger_dedup(run.status):
            await enqueue_dedup_terminal_async(run.id)
    return applied, run.status if applied else None


def on_job_terminal_sync(
    job: JobInstance,
    db: Session,
    *,
    run: Optional[PlanRun] = None,
) -> tuple[bool, Optional[str]]:
    """Sync entry — recycler / abort (optionally with *run* already locked)."""
    if job.status not in _TERMINAL:
        logger.warning(
            "on_job_terminal_sync_skipped_non_terminal job=%s status=%s",
            job.id, job.status,
        )
        return False, None

    if run is None:
        run = db.execute(
            select(PlanRun)
            .where(PlanRun.id == job.plan_run_id)
            .with_for_update(key_share=True)
        ).scalar_one_or_none()
    if run is None:
        return False, None

    _bump_counters(run, job)

    if job.host_id:
        prh = db.execute(
            select(PlanRunHost).where(
                PlanRunHost.plan_run_id == run.id,
                PlanRunHost.host_id == job.host_id,
            )
        ).scalar_one_or_none()
        if prh is not None:
            _bump_host_counters(prh, job)

    total = int(run.total_job_count or 0)
    if total > 0:
        t0 = time.perf_counter()
        applied = apply_plan_run_aggregation_from_counters(run)
        record_plan_run_aggregation_duration(
            time.perf_counter() - t0, "counters",
        )
        if applied:
            from backend.services.plan_chain_trigger import trigger_next_plan_sync
            from backend.services.dedup_scan import (
                should_trigger_dedup,
                enqueue_dedup_terminal_sync,
            )
            trigger_next_plan_sync(run, db)
            if should_trigger_dedup(run.status):
                enqueue_dedup_terminal_sync(run.id)
        return applied, run.status if applied else None

    jobs = (
        db.query(JobInstance)
        .filter(JobInstance.plan_run_id == run.id)
        .all()
    )
    t0 = time.perf_counter()
    applied = apply_plan_run_aggregation(run, jobs)
    record_plan_run_aggregation_duration(
        time.perf_counter() - t0, "full_scan",
    )
    if applied:
        from backend.services.plan_chain_trigger import trigger_next_plan_sync
        from backend.services.dedup_scan import (
            should_trigger_dedup,
            enqueue_dedup_terminal_sync,
        )
        trigger_next_plan_sync(run, db)
        if should_trigger_dedup(run.status):
            enqueue_dedup_terminal_sync(run.id)
    return applied, run.status if applied else None


def recount_plan_run_counters(run: PlanRun, jobs: list[Any]) -> dict[str, int]:
    """Recompute counter fields from *jobs*; return before/after drift info."""
    completed = sum(1 for j in jobs if j.status == JobStatus.COMPLETED.value)
    failed = sum(1 for j in jobs if j.status == JobStatus.FAILED.value)
    aborted = sum(1 for j in jobs if j.status == JobStatus.ABORTED.value)
    terminal = completed + failed + aborted
    total = len(jobs)

    before = {
        "total_job_count": int(run.total_job_count or 0),
        "terminal_job_count": int(run.terminal_job_count or 0),
        "completed_job_count": int(run.completed_job_count or 0),
        "failed_job_count": int(run.failed_job_count or 0),
        "aborted_job_count": int(run.aborted_job_count or 0),
    }
    after = {
        "total_job_count": total,
        "terminal_job_count": terminal,
        "completed_job_count": completed,
        "failed_job_count": failed,
        "aborted_job_count": aborted,
    }
    run.total_job_count = total
    run.terminal_job_count = terminal
    run.completed_job_count = completed
    run.failed_job_count = failed
    run.aborted_job_count = aborted
    return {"before": before, "after": after, "drifted": before != after}
