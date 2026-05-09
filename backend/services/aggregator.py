"""Async PlanRun aggregator entrypoint (ADR-0020).

Triggered by Agent ``/complete`` (via ``backend.api.routes.agent_api``)
and the SAQ post-completion task.  Locates the parent ``PlanRun``, applies
the shared aggregation rule, then triggers the next chain segment if the
PlanRun reached a triggerable terminal status.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.services.plan_run_aggregation import apply_plan_run_aggregation
from backend.services.plan_chain_trigger import trigger_next_plan


class PlanAggregator:
    """ADR-0020: PlanRun terminal aggregation + chain trigger."""

    @staticmethod
    async def on_job_terminal(
        job: JobInstance, db: AsyncSession,
    ) -> tuple[bool, str | None]:
        """Returns ``(applied, new_status)`` — caller should push after commit."""
        run = await db.get(PlanRun, job.plan_run_id)
        if run is None:
            return False, None

        result = await db.execute(
            select(JobInstance).where(JobInstance.plan_run_id == run.id)
        )
        jobs = result.scalars().all()

        applied = apply_plan_run_aggregation(run, jobs)
        if applied:
            await trigger_next_plan(run, db)
        return applied, run.status if applied else None
