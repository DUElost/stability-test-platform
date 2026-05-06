from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.services.workflow_aggregation import apply_workflow_aggregation
from backend.services.plan_chain_trigger import trigger_next_plan


class WorkflowAggregator:
    """Deprecated alias for PlanAggregator (ADR-0020)."""

    @staticmethod
    async def on_job_terminal(job: JobInstance, db: AsyncSession) -> None:
        run = await db.get(PlanRun, job.plan_run_id)
        if run is None:
            return

        result = await db.execute(
            select(JobInstance).where(JobInstance.plan_run_id == run.id)
        )
        jobs = result.scalars().all()

        applied = apply_workflow_aggregation(run, jobs)
        if applied:
            await trigger_next_plan(run, db)
