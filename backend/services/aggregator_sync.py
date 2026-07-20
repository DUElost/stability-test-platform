"""Synchronous plan-run aggregation for use in sync contexts (recycler thread)."""

from sqlalchemy.orm import Session

from backend.models.job import JobInstance
from backend.services import job_terminalization as _terminalization


def plan_aggregator_sync(job: JobInstance, db: Session) -> None:
    """Aggregate a PlanRun after a child JobInstance reaches terminal state."""
    _terminalization.on_job_terminal_sync(job, db)
