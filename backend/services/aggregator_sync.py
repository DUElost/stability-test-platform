"""Synchronous plan-run aggregation for use in sync contexts (recycler thread)."""

from sqlalchemy.orm import Session

from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.services.workflow_aggregation import apply_workflow_aggregation
from backend.services.plan_chain_trigger import trigger_next_plan_sync


def plan_aggregator_sync(job: JobInstance, db: Session) -> None:
    """ADR-0020: Replaces workflow_aggregator_sync.  Uses PlanRun."""
    run = db.get(PlanRun, job.plan_run_id)
    if run is None:
        return

    jobs = (
        db.query(JobInstance)
        .filter(JobInstance.plan_run_id == run.id)
        .all()
    )

    applied = apply_workflow_aggregation(run, jobs)
    if applied:
        trigger_next_plan_sync(run, db)
