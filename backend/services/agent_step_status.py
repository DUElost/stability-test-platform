"""Agent StepTrace 上报（#1520 垂直切片：/steps + step status）。

覆盖：
- ``POST /steps`` — 批量幂等 StepTrace upsert
- ``POST /jobs/{id}/steps/{step_id}/status`` — 单步 status（派生稳定
  ``trace_event_id`` 后走同一 reconciler）

``_require_valid_runtime_lease`` 亦落于此，供路由侧 ``update_job_status``
re-export 继续使用。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.device_lease import DeviceLease
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.realtime.socketio_server import broadcast_plan_run_status, broadcast_run_job_update
from backend.services.agent_completion import _get_valid_runtime_lease
from backend.services.errors import Conflict, NotFound
from backend.services.reconciler import reconcile_step_traces


class StepTraceIn(BaseModel):
    job_id: int
    step_id: str
    stage: str = "execute"
    event_type: str
    status: str = ""
    output: Optional[str] = None
    error_message: Optional[str] = None
    exit_code: Optional[int] = None
    metadata: Optional[dict] = None
    original_ts: Optional[str] = None
    trace_event_id: Optional[str] = None
    fencing_token: str


class StepStatusIn(BaseModel):
    status: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    metadata: Optional[dict] = None
    trace_event_id: Optional[str] = None
    fencing_token: str


# 既有测试仍从 agent_api 以私有名导入。
_StepStatusIn = StepStatusIn


async def require_valid_runtime_lease(
    db: AsyncSession,
    job: JobInstance,
    fencing_token: str,
) -> DeviceLease:
    valid_lease = await _get_valid_runtime_lease(db, job, fencing_token)
    if valid_lease is None:
        raise Conflict("invalid or expired fencing_token")
    return valid_lease


_require_valid_runtime_lease = require_valid_runtime_lease


async def _broadcast_transitioned_jobs(
    db: AsyncSession,
    transitioned_jobs: list[int],
) -> None:
    for tj_id in transitioned_jobs:
        job = await db.get(JobInstance, tj_id)
        if job is not None:
            await broadcast_run_job_update(job.plan_run_id, tj_id, job.status)
            pr = await db.get(PlanRun, job.plan_run_id)
            if pr is not None and pr.status in {
                "SUCCESS", "PARTIAL_SUCCESS", "FAILED",
            }:
                await broadcast_plan_run_status(pr.id, pr.status)


async def upload_agent_step_traces(
    db: AsyncSession,
    traces: List[StepTraceIn],
) -> dict:
    """Batch idempotent StepTrace upsert (Agent replay on reconnect)."""
    host_id = "unknown"
    for trace in traces:
        job = await db.get(JobInstance, trace.job_id)
        if job is None:
            raise NotFound("job not found")
        await require_valid_runtime_lease(db, job, trace.fencing_token)

    raw = [t.model_dump() for t in traces]
    result = await reconcile_step_traces(host_id, raw, db)
    await _broadcast_transitioned_jobs(db, result["transitioned_jobs"])
    return {"inserted": result["inserted"], "total": len(traces)}


async def update_agent_job_step_status(
    db: AsyncSession,
    job_id: int,
    step_id: str,
    payload: StepStatusIn,
) -> dict:
    """Update a single step status — upserted as StepTrace."""
    trace_event_id = (payload.trace_event_id or "").strip()
    if not trace_event_id:
        # 单步 status 端点没有独立批次 id：用载荷内容派生稳定 id，
        # 同一逻辑迁移（如 RUNNING→FAILED）各自幂等，重试不重复插入。
        metadata_json = (
            json.dumps(payload.metadata, sort_keys=True, default=str)
            if payload.metadata else ""
        )
        digest = hashlib.sha256(
            (
                f"{job_id}\0{step_id}\0{payload.status}\0"
                f"{payload.started_at or ''}\0{payload.exit_code or ''}\0"
                f"{payload.error_message or ''}\0{metadata_json}"
            ).encode("utf-8")
        ).hexdigest()[:24]
        trace_event_id = f"status:{digest}"
    trace = {
        "job_id": job_id,
        "step_id": step_id,
        "stage": "execute",
        "event_type": "status_update",
        "status": payload.status,
        "exit_code": payload.exit_code,
        "metadata": payload.metadata,
        "error_message": payload.error_message,
        "trace_event_id": trace_event_id,
        "original_ts": payload.started_at or datetime.now(timezone.utc).isoformat(),
    }
    job = await db.get(JobInstance, job_id)
    if job is None:
        raise NotFound("job not found")
    await require_valid_runtime_lease(db, job, payload.fencing_token)

    result = await reconcile_step_traces("agent", [trace], db)
    await _broadcast_transitioned_jobs(db, result["transitioned_jobs"])
    return {"job_id": job_id, "step_id": step_id, "status": payload.status}
