"""Synchronous workflow aggregation for use in sync contexts (recycler thread)."""

from sqlalchemy.orm import Session

from backend.models.job import JobInstance
from backend.models.workflow import WorkflowRun
from backend.services.workflow_aggregation import apply_workflow_aggregation


def workflow_aggregator_sync(job: JobInstance, db: Session) -> None:
    run = db.get(WorkflowRun, job.workflow_run_id)
    if run is None:
        return

    jobs = (
        db.query(JobInstance)
        .filter(JobInstance.workflow_run_id == run.id)
        .all()
    )

    apply_workflow_aggregation(run, jobs)
