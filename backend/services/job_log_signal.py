"""JobLogSignal helpers — observation layer (ADR-0028 / #213 Track D)."""

from __future__ import annotations

from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.job import JobLogSignal

# Call sites that intentionally scope by PlanRun jobs and therefore exclude
# ``job_id IS NULL`` orphans (#212 P1-7 / #213 D3). Keep in sync when adding
# new PlanRun-scoped signal aggregations.
ORPHAN_EXCLUDING_CALL_SITES: tuple[str, ...] = (
    "backend/api/routes/plan_runs.py:watcher-summary / aee breakdown",
    "backend/api/routes/plan_runs.py:event timeline / deduped AEE events",
    "backend/services/log_observation.py:aggregate_risk_summary",
)


def count_orphan_log_signals(db: Session) -> int:
    """Signals whose Job was deleted (``job_id IS NULL``)."""
    return int(
        db.execute(
            select(func.count(JobLogSignal.id)).where(JobLogSignal.job_id.is_(None))
        ).scalar()
        or 0
    )


def list_orphan_log_signals(
    db: Session,
    *,
    skip: int = 0,
    limit: int = 100,
) -> Sequence[JobLogSignal]:
    """Newest-first orphan signals for admin inspection."""
    return db.execute(
        select(JobLogSignal)
        .where(JobLogSignal.job_id.is_(None))
        .order_by(JobLogSignal.detected_at.desc(), JobLogSignal.id.desc())
        .offset(max(0, skip))
        .limit(max(1, min(limit, 500)))
    ).scalars().all()
