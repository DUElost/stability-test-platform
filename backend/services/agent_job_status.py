"""Agent Job 兼容 status 端点（#1520 垂直切片：POST /jobs/{id}/status）。

仅接受 RUNNING：Claim 已原子完成 PENDING→RUNNING，重复 RUNNING 为保活
no-op；终态必须走 ``/complete``。

路由退化为 ``ok(await update_agent_job_status(...))``。
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.enums import JobStatus
from backend.models.job import JobInstance
from backend.services.agent_step_status import require_valid_runtime_lease
from backend.services.errors import BadRequest, Conflict, NotFound


class JobStatusUpdate(BaseModel):
    status: str
    reason: str = ""
    fencing_token: str


async def update_agent_job_status(
    db: AsyncSession,
    job_id: int,
    payload: JobStatusUpdate,
) -> dict:
    """Transition job status via JobStateMachine (compat: RUNNING-only)."""
    job = await db.get(JobInstance, job_id)
    if job is None:
        raise NotFound("job not found")

    await require_valid_runtime_lease(db, job, payload.fencing_token)

    try:
        new_status = JobStatus(payload.status.upper())
    except ValueError:
        raise BadRequest(f"unknown status: {payload.status}") from None

    if new_status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ABORTED}:
        raise Conflict({
            "code": "TERMINAL_STATUS_REQUIRES_COMPLETE",
            "message": "terminal status must be reported through /jobs/{job_id}/complete",
        })
    if new_status != JobStatus.RUNNING:
        raise Conflict({
            "code": "INVALID_JOB_TRANSITION",
            "message": "status endpoint only accepts RUNNING",
        })

    # Compatibility endpoint is now heartbeat-only.  Claim already performs
    # PENDING→RUNNING atomically with lease acquisition, so repeated RUNNING is
    # a no-op rather than a second state transition.
    job.updated_at = datetime.now(timezone.utc)
    if payload.reason:
        job.status_reason = payload.reason

    await db.commit()
    return {"job_id": job_id, "status": job.status}
