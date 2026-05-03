from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.enums import JobStatus, WorkflowStatus
from backend.models.job import JobInstance
from backend.models.workflow import WorkflowRun

_TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ABORTED, JobStatus.UNKNOWN}


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

        if not all(JobStatus(j.status) in _TERMINAL for j in jobs):
            return

        total   = len(jobs)
        if total == 0:
            run.status = WorkflowStatus.FAILED.value
            run.ended_at = datetime.now(timezone.utc)
            return

        failed  = sum(1 for j in jobs if JobStatus(j.status) in {JobStatus.FAILED, JobStatus.ABORTED})
        unknown = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.UNKNOWN)

        if unknown > 0:
            run.status = WorkflowStatus.DEGRADED.value
        elif failed == 0:
            run.status = WorkflowStatus.SUCCESS.value
        elif failed / total <= run.failure_threshold:
            run.status = WorkflowStatus.PARTIAL_SUCCESS.value
        else:
            run.status = WorkflowStatus.FAILED.value

        run.ended_at = datetime.now(timezone.utc)

        completed = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.COMPLETED)
        run.result_summary = {
            "total": total,
            "completed": completed,
            "failed": failed,
            "unknown": unknown,
            "pass_rate": round(completed / total, 4) if total else 0,
        }
