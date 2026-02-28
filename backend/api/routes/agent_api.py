"""Agent API: job claim, status update, step trace upload, heartbeat.

Authentication: X-Agent-Secret header (compared to AGENT_SECRET env var).
"""

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.response import ApiResponse, err, ok
from backend.core.database import get_async_db
from backend.models.enums import HostStatus, JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.services.aggregator import WorkflowAggregator
from backend.services.reconciler import reconcile_step_traces
from backend.services.state_machine import InvalidTransitionError, JobStateMachine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

_AGENT_SECRET = os.getenv("AGENT_SECRET", "")
_TERMINAL = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.ABORTED.value,
    JobStatus.UNKNOWN.value,
}


def _verify_agent(x_agent_secret: Optional[str] = Header(None, alias="X-Agent-Secret")):
    if _AGENT_SECRET and x_agent_secret != _AGENT_SECRET:
        raise HTTPException(status_code=401, detail="invalid agent secret")


# ── Schemas ───────────────────────────────────────────────────────────────────

class ClaimRequest(BaseModel):
    host_id: str
    capacity: int = 10


class JobOut(BaseModel):
    id: int
    workflow_run_id: int
    task_template_id: int
    device_id: int
    host_id: Optional[str]
    status: str
    pipeline_def: dict


class JobStatusUpdate(BaseModel):
    status: str
    reason: str = ""


class StepTraceIn(BaseModel):
    job_id: int
    step_id: str
    stage: str = "execute"
    event_type: str
    status: str = ""
    output: Optional[str] = None
    error_message: Optional[str] = None
    original_ts: Optional[str] = None


class HeartbeatRequest(BaseModel):
    host_id: str
    tool_catalog_version: str = ""
    load: Dict[str, Any] = {}


class BackpressureInfo(BaseModel):
    log_rate_limit: Optional[int]


class HeartbeatResponse(BaseModel):
    tool_catalog_outdated: bool
    backpressure: BackpressureInfo


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/jobs/claim", response_model=ApiResponse[List[JobOut]])
async def claim_jobs(
    payload: ClaimRequest,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Find PENDING jobs for devices on this host, claim up to `capacity`."""
    device_ids_result = await db.execute(
        select(Device.id).where(Device.host_id == payload.host_id)
    )
    device_ids = [row[0] for row in device_ids_result.all()]
    if not device_ids:
        return ok([])

    pending_jobs = (await db.execute(
        select(JobInstance)
        .where(
            JobInstance.device_id.in_(device_ids),
            JobInstance.status == JobStatus.PENDING.value,
        )
        .order_by(JobInstance.created_at)
        .limit(payload.capacity)
    )).scalars().all()

    claimed = []
    now = datetime.utcnow()
    for job in pending_jobs:
        try:
            JobStateMachine.transition(job, JobStatus.RUNNING, "claimed_by_agent")
            job.host_id = payload.host_id
            job.started_at = now
            claimed.append(job)
        except InvalidTransitionError:
            continue

    await db.commit()
    return ok([
        JobOut(
            id=j.id, workflow_run_id=j.workflow_run_id,
            task_template_id=j.task_template_id, device_id=j.device_id,
            host_id=j.host_id, status=j.status, pipeline_def=j.pipeline_def,
        )
        for j in claimed
    ])


@router.post("/jobs/{job_id}/status", response_model=ApiResponse[dict])
async def update_job_status(
    job_id: int,
    payload: JobStatusUpdate,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Transition job status via JobStateMachine."""
    job = await db.get(JobInstance, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    try:
        new_status = JobStatus(payload.status.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"unknown status: {payload.status}")

    try:
        JobStateMachine.transition(job, new_status, payload.reason)
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))

    if job.status in _TERMINAL:
        job.ended_at = datetime.utcnow()
        await WorkflowAggregator.on_job_terminal(job, db)

    await db.commit()
    return ok({"job_id": job_id, "status": job.status})


@router.post("/steps", response_model=ApiResponse[dict])
async def upload_step_traces(
    traces: List[StepTraceIn],
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
    x_agent_secret: Optional[str] = Header(None, alias="X-Agent-Secret"),
):
    """Batch idempotent StepTrace upsert (Agent replay on reconnect)."""
    host_id = "unknown"
    raw = [t.model_dump() for t in traces]
    inserted = await reconcile_step_traces(host_id, raw, db)
    return ok({"inserted": inserted, "total": len(traces)})


@router.post("/heartbeat", response_model=ApiResponse[HeartbeatResponse])
async def agent_heartbeat(
    payload: HeartbeatRequest,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """
    Update host last_heartbeat + tool_catalog_version.
    Returns tool_catalog_outdated flag + current backpressure setting.
    """
    host = await db.get(Host, payload.host_id)
    if host is None:
        host = Host(
            id=payload.host_id,
            hostname=payload.host_id,
            status=HostStatus.ONLINE.value,
            created_at=datetime.utcnow(),
        )
        db.add(host)

    outdated = (
        bool(host.tool_catalog_version)
        and host.tool_catalog_version != payload.tool_catalog_version
    )

    host.last_heartbeat = datetime.utcnow()
    host.tool_catalog_version = payload.tool_catalog_version
    host.status = HostStatus.ONLINE.value

    await db.commit()

    backpressure = await _get_backpressure()
    return ok(HeartbeatResponse(
        tool_catalog_outdated=outdated,
        backpressure=BackpressureInfo(log_rate_limit=backpressure),
    ))


async def _get_backpressure() -> Optional[int]:
    """Read current backpressure setting from Redis (set by MQ consumer monitor)."""
    try:
        from backend.main import redis_client
        if redis_client:
            val = await redis_client.get("stp:backpressure:log_rate_limit")
            return int(val) if val else None
    except Exception:
        pass
    return None
