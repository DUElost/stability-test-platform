"""PlanRun 手动重试 / 退出业务逻辑（ADR-0022 D7；#1520 首个垂直切片）。

路由退化为「解析 → 调服务 → 序列化」；本模块承担状态校验、设备可达性
门禁、manual_action 迁移、审计、指标、失效事件与事务提交。异常沿用
``HTTPException``（与 #1519 下沉符号一致的服务层既有先例）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.core.audit import record_audit
from backend.core.metrics import record_patrol_manual_action
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.services.plan_run_events import emit_job_status_invalidation
from backend.services.plan_run_queries import (
    MANUAL_ACTION_JOB_STATUSES,
    device_currently_disconnected,
    load_job_in_run,
)

logger = logging.getLogger(__name__)


def manual_retry_job_sync(
    db: Session,
    run_id: int,
    job_id: int,
    *,
    reason: Optional[str] = None,
    actor_id: Optional[int] = None,
    actor_username: Optional[str] = None,
) -> JobInstance:
    """ADR-0022 D7: clear backoff and force the next patrol cycle to run now.

    Sets ``next_retry_at = now()`` and ``manual_action = 'RETRY_NOW'`` so the
    Agent picks it up on the next heartbeat.  **Does not reset**
    ``current_failure_streak`` — diagnostic information is preserved.
    """
    job = load_job_in_run(db, run_id, job_id)
    if job.status not in MANUAL_ACTION_JOB_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"job must be RUNNING for manual retry; current status is {job.status}",
        )

    device = db.get(Device, job.device_id) if job.device_id else None
    host_status: str | None = None
    if job.host_id:
        host_row = db.get(Host, job.host_id)
        host_status = host_row.status if host_row else None
    if device_currently_disconnected(device, host_status):
        raise HTTPException(
            status_code=409,
            detail=(
                "device ADB is not reachable; manual retry cannot restore "
                "connection — check USB or reboot the device"
            ),
        )

    # Why: 同向 manual_action 已等待 Agent 消费时,重复点击不再二次写 audit / emit / counter。
    #      合法语义:用户连点 N 次 retry,后端只该留 1 条审计 + 1 次 emit;EXIT_REQUESTED 切到
    #      RETRY_NOW 是真正的意图变更,不在此处短路。
    if job.manual_action == "RETRY_NOW":
        return job

    effective_reason = reason or "manual_retry"
    now = datetime.now(timezone.utc)

    job.next_retry_at = now
    job.manual_action = "RETRY_NOW"
    job.updated_at = now
    db.flush()

    record_audit(
        db,
        action="patrol_manual_retry",
        resource_type="job_instance",
        resource_id=job_id,
        details={
            "plan_run_id": run_id,
            "reason": effective_reason,
            "current_failure_streak": job.current_failure_streak or 0,
            "triggered_by": actor_username,
        },
        user_id=actor_id,
        username=actor_username,
    )
    db.commit()
    db.refresh(job)

    logger.info(
        "patrol_manual_retry plan_run=%d job=%d streak=%d",
        run_id, job_id, job.current_failure_streak or 0,
    )
    record_patrol_manual_action("manual_retry")
    emit_job_status_invalidation(run_id, job_id, job.status, "manual_retry")
    return job


def manual_exit_job_sync(
    db: Session,
    run_id: int,
    job_id: int,
    *,
    reason: Optional[str] = None,
    actor_id: Optional[int] = None,
    actor_username: Optional[str] = None,
) -> JobInstance:
    """ADR-0022 D7: request that the Agent skip the rest of patrol and abort.

    Sets ``manual_action = 'EXIT_REQUESTED'``.  The Agent observes this on the
    next heartbeat and exits the patrol loop **without running teardown** (BO4).
    Recycler / device lease release ensures the device returns to the pool.

    The job's status remains RUNNING here; it transitions to ABORTED once the
    Agent reports the terminal state via /jobs/{id}/complete (or via Recycler's
    stall detection).
    """
    job = load_job_in_run(db, run_id, job_id)
    if job.status not in MANUAL_ACTION_JOB_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"job must be RUNNING for manual exit; current status is {job.status}",
        )

    # Why: 与 manual_retry 对称 — 同向 EXIT_REQUESTED 已等待 Agent 消费时短路,避免连点
    #      产生多条审计 + 多次 emit。RETRY_NOW 切 EXIT_REQUESTED 是真正的意图变更不短路。
    if job.manual_action == "EXIT_REQUESTED":
        return job

    effective_reason = reason or "manual_exit"
    now = datetime.now(timezone.utc)

    job.manual_action = "EXIT_REQUESTED"
    if not job.status_reason:
        job.status_reason = f"patrol_manual_exit_pending: {effective_reason}"
    job.updated_at = now
    db.flush()

    record_audit(
        db,
        action="patrol_manual_exit",
        resource_type="job_instance",
        resource_id=job_id,
        details={
            "plan_run_id": run_id,
            "reason": effective_reason,
            "current_failure_streak": job.current_failure_streak or 0,
            "triggered_by": actor_username,
        },
        user_id=actor_id,
        username=actor_username,
    )
    db.commit()
    db.refresh(job)

    logger.info(
        "patrol_manual_exit plan_run=%d job=%d streak=%d",
        run_id, job_id, job.current_failure_streak or 0,
    )
    record_patrol_manual_action("manual_exit")
    emit_job_status_invalidation(run_id, job_id, job.status, "manual_exit_pending")
    return job
