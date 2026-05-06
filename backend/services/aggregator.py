from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.job import JobInstance
from backend.models.workflow import WorkflowRun
from backend.services.workflow_aggregation import apply_workflow_aggregation


class WorkflowAggregator:
    @staticmethod
    async def on_job_terminal(job: JobInstance, db: AsyncSession) -> None:
        run = await db.get(WorkflowRun, job.workflow_run_id)
        if run is None:
            return

        result = await db.execute(
            select(JobInstance).where(JobInstance.workflow_run_id == run.id)
        )
        jobs = result.scalars().all()

        apply_workflow_aggregation(run, jobs)
