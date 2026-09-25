"""Agent Job 保活与锁续期（#1520 垂直切片：job heartbeat / extend_lock）。

覆盖：
- ``POST /jobs/{job_id}/heartbeat`` — RUNNING 保活（禁止终态）
- ``POST /jobs/{job_id}/extend_lock`` — Job→Lease 全序续期（#1980）

路由退化为 ``ok(await record_agent_job_heartbeat / extend_agent_job_lock(...))``。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.enums import JobStatus, LeaseType
from backend.models.host import Device
from backend.models.job import JobInstance
from backend.services.agent_completion import _RUN_TO_JOB, _get_valid_runtime_lease
from backend.services.errors import Conflict, NotFound
from backend.services.lease_manager import extend_lease

_DEVICE_LOCK_LEASE_SECONDS = int(os.getenv("DEVICE_LOCK_LEASE_SECONDS", "600"))


class JobHeartbeatIn(BaseModel):
    status: str = "RUNNING"
    started_at: Optional[str] = None
    fencing_token: str  # ADR-0019 Phase 2b: 必填


class ExtendLockIn(BaseModel):
    fencing_token: str  # ADR-0019 Phase 2b: 必填


# 既有测试仍从 agent_api 以私有名导入。
_JobHeartbeatIn = JobHeartbeatIn
_ExtendLockIn = ExtendLockIn


async def record_agent_job_heartbeat(
    db: AsyncSession,
    job_id: int,
    payload: JobHeartbeatIn,
) -> dict:
    """Keep an already claimed RUNNING job alive."""
    job = await db.get(JobInstance, job_id)
    if job is None:
        raise NotFound("job not found")

    # ADR-0019 Phase 4b: validate fencing_token via _get_valid_runtime_lease
    valid_lease = await _get_valid_runtime_lease(
        db,
        job,
        payload.fencing_token,
        allowed_job_statuses={JobStatus.RUNNING.value},
    )
    if valid_lease is None:
        raise Conflict("invalid or expired fencing_token")

    target = _RUN_TO_JOB.get(payload.status.upper(), JobStatus.RUNNING)
    if target != JobStatus.RUNNING:
        raise Conflict({
            "code": "TERMINAL_STATUS_REQUIRES_COMPLETE",
            "message": "job heartbeat cannot finalize a job; use /complete",
        })
    now = datetime.now(timezone.utc)
    if job.status == JobStatus.RUNNING.value:
        if not job.started_at:
            job.started_at = now
        job.updated_at = now

    await db.commit()
    return {"job_id": job_id, "status": job.status}


async def extend_agent_job_lock(
    db: AsyncSession,
    job_id: int,
    payload: ExtendLockIn,
) -> dict:
    """Extend device lock lease for a running job.

    #1980：先锁 Job 行，再碰 Lease —— 与 complete_job / extend_leases_batch /
    _reconcile_expired_leases 保持同一全序（Job → Lease）。
    """
    job = (await db.execute(
        select(JobInstance)
        .where(JobInstance.id == job_id)
        .with_for_update()
    )).scalars().first()
    if job is None:
        raise NotFound("job not found")

    device = await db.get(Device, job.device_id)
    if device is None:
        raise NotFound("device not found")

    # ADR-0019 Phase 4b: validate fencing_token via _get_valid_runtime_lease
    valid_lease = await _get_valid_runtime_lease(db, job, payload.fencing_token)
    if valid_lease is None:
        raise Conflict("invalid or expired fencing_token")

    renewed = await extend_lease(
        db, job.device_id, job_id, LeaseType.JOB, _DEVICE_LOCK_LEASE_SECONDS,
    )
    if not renewed:
        raise Conflict("device locked by another job")

    now = datetime.now(timezone.utc)
    job.updated_at = now
    await db.commit()
    expires_at = now + timedelta(seconds=_DEVICE_LOCK_LEASE_SECONDS)
    return {"job_id": job_id, "expires_at": expires_at.isoformat()}
