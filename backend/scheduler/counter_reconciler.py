"""ADR-0026 §6 — low-frequency counter reconciliation sweep.

Compares ``plan_run`` O(1) counters against ``COUNT(*)`` of child jobs and
rewrites drifted rows. Self-heals silent double-bumps / missed bumps when an
old code path skipped the terminalization service.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from backend.core.database import SessionLocal
from backend.models.enums import PlanRunStatus
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.services.job_terminalization import recount_plan_run_counters

logger = logging.getLogger(__name__)

# Only scan recently-active / still-open runs by default to keep the sweep cheap.
COUNTER_RECONCILE_LOOKBACK_HOURS = int(
    os.getenv("STP_COUNTER_RECONCILE_LOOKBACK_HOURS", "48")
)
COUNTER_RECONCILE_BATCH = int(os.getenv("STP_COUNTER_RECONCILE_BATCH", "200"))

_OPEN_STATUSES = {
    PlanRunStatus.RUNNING.value,
    PlanRunStatus.QUEUED.value,
    PlanRunStatus.PRECHECK.value,
}


def reconcile_plan_run_counters_once(
    *,
    lookback_hours: int | None = None,
    batch_size: int | None = None,
) -> dict:
    """Reconcile up to *batch_size* PlanRuns; return summary counters."""
    lookback = (
        COUNTER_RECONCILE_LOOKBACK_HOURS if lookback_hours is None else lookback_hours
    )
    limit = COUNTER_RECONCILE_BATCH if batch_size is None else batch_size
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback)

    scanned = 0
    drifted = 0
    fixed = 0

    with SessionLocal() as db:
        rows = (
            db.execute(
                select(PlanRun)
                .where(
                    (PlanRun.status.in_(_OPEN_STATUSES))
                    | (PlanRun.ended_at.is_(None))
                    | (PlanRun.ended_at >= cutoff)
                    | (PlanRun.started_at >= cutoff)
                )
                .order_by(PlanRun.id.desc())
                .limit(limit)
                .with_for_update(key_share=True, skip_locked=True)
            )
        ).scalars().all()

        for run in rows:
            scanned += 1
            jobs = (
                db.query(JobInstance)
                .filter(JobInstance.plan_run_id == run.id)
                .all()
            )
            # Skip empty QUEUED/PRECHECK (no jobs yet — counters stay 0).
            if not jobs and run.status in (
                PlanRunStatus.QUEUED.value,
                PlanRunStatus.PRECHECK.value,
            ):
                continue
            result = recount_plan_run_counters(run, jobs)
            if result["drifted"]:
                drifted += 1
                fixed += 1
                logger.warning(
                    "plan_run_counter_drift plan_run=%d before=%s after=%s",
                    run.id, result["before"], result["after"],
                )

        if fixed:
            db.commit()
        else:
            db.rollback()

    summary = {"scanned": scanned, "drifted": drifted, "fixed": fixed}
    if drifted:
        logger.info("counter_reconcile_done %s", summary)
    else:
        logger.debug("counter_reconcile_done %s", summary)
    return summary
