"""Async PlanRun aggregator entrypoint (ADR-0020 / ADR-0026 §6).

Triggered by Agent ``/complete`` (via ``backend.api.routes.agent_api``)
and the SAQ post-completion task.  Delegates to the single terminalization
service (counter bump + O(1) aggregation).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.job import JobInstance
from backend.services import job_terminalization as _terminalization


class PlanAggregator:
    """ADR-0020: PlanRun terminal aggregation + chain trigger."""

    @staticmethod
    async def on_job_terminal(
        job: JobInstance, db: AsyncSession,
    ) -> tuple[bool, str | None]:
        """Returns ``(applied, new_status)`` — caller should push after commit."""
        return await _terminalization.on_job_terminal(job, db)
