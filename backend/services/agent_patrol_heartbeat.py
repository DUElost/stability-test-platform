"""Agent patrol cycle 心跳（#1520 垂直切片：agent_api patrol-heartbeat）。

``POST /jobs/{job_id}/patrol-heartbeat``：RUNNING 门禁 → runtime lease →
GREATEST 单调 cycle → 计数/退避/manual_action CAS 清除 → 指标 + 回读。

路由退化为 ``ok(await record_agent_patrol_heartbeat(...))``。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.metrics import record_patrol_heartbeat
from backend.models.enums import JobStatus
from backend.models.job import JobInstance
from backend.services.agent_completion import _get_valid_runtime_lease


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso_or_none(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return _as_utc(dt).isoformat()


class PatrolHeartbeatIn(BaseModel):
    """ADR-0022 D3: per-cycle patrol aggregation upload (no step_trace written).

    Contract:
      cycle_index: monotonic; server uses MAX(existing, payload) so out-of-order
        heartbeats do not regress the cycle counter.
      success_delta / failed_delta: positive integers added to the running totals
        in the same UPDATE.  Either may be 0.  Agent should send delta=1 per
        cycle in the normal path (success XOR failure).
      current_step: best-effort; UI uses it for the device matrix.
      current_failure_streak: Agent-computed value (server overwrites the column).
      next_retry_at: ISO8601 string; null when not in backoff.
      watcher_capability: optional watcher capability live snapshot.
      manual_action_observed: optional echo-back: when Agent has consumed a
        RETRY_NOW or EXIT_REQUESTED, it sends the value here so the server can
        clear the column atomically.
    """

    fencing_token: str
    cycle_index: int
    success_delta: int = 0
    failed_delta: int = 0
    current_step: Optional[str] = None
    current_failure_streak: int = 0
    next_retry_at: Optional[str] = None
    watcher_capability: Optional[str] = None
    manual_action_observed: Optional[str] = None


class PatrolHeartbeatOut(BaseModel):
    """Mirror of the post-update job_instance patrol fields, plus pending
    manual_action so the Agent can short-circuit its sleep loop without a
    separate poll endpoint.
    """

    job_id: int
    patrol_cycle_count: int
    patrol_success_cycle_count: int
    patrol_failed_cycle_count: int
    current_failure_streak: int
    next_retry_at: Optional[str] = None
    manual_action: Optional[str] = None  # pending action for Agent to honor


async def record_agent_patrol_heartbeat(
    db: AsyncSession,
    job_id: int,
    payload: PatrolHeartbeatIn,
) -> PatrolHeartbeatOut:
    """ADR-0022 D2/D3: receive a patrol cycle aggregate, update job_instance
    counter columns atomically, return current pending manual_action.

    Does NOT write to step_trace.  Out-of-order safe: cycle_count is monotonic
    via GREATEST().  Empty deltas are accepted (pure heartbeat / mid-cycle ping).
    """
    job = await db.get(JobInstance, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    # ADR-0022 D10: Job 已非 RUNNING(典型:recycler 已把 status 推到 UNKNOWN)→
    # 直接 409 JOB_NOT_RUNNING,与 L1033 CAS 失配的契约统一。本 slice 仅落 backend
    # ground truth;Agent 端如何消费此 code(理想:停 patrol 循环并触发 /recovery/sync)
    # 留给下一 slice — 当前 patrol_heartbeat_uploader.py 收到 409 只 log + return None,
    # lease-lost 收口由 LeaseRenewer (lease_renewer.py:152-167) 通过续租 409/404
    # 触发 _on_lease_lost 兜底完成。
    if job.status != JobStatus.RUNNING.value:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "JOB_NOT_RUNNING",
                "message": (
                    f"Job {job_id} status={job.status} (not RUNNING); "
                    "trigger /agent/recovery/sync to re-establish lease before next patrol cycle"
                ),
            },
        )

    valid_lease = await _get_valid_runtime_lease(db, job, payload.fencing_token)
    if valid_lease is None:
        raise HTTPException(status_code=409, detail="invalid or expired fencing_token")

    if payload.success_delta < 0 or payload.failed_delta < 0:
        raise HTTPException(status_code=400, detail="delta must be non-negative")
    if payload.cycle_index < 0:
        raise HTTPException(status_code=400, detail="cycle_index must be non-negative")
    if payload.current_failure_streak < 0:
        raise HTTPException(status_code=400, detail="current_failure_streak must be non-negative")

    next_retry_dt = None
    if payload.next_retry_at:
        try:
            next_retry_dt = datetime.fromisoformat(payload.next_retry_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid next_retry_at: {payload.next_retry_at}") from None

    now = datetime.now(timezone.utc)

    # Atomic UPDATE — out-of-order heartbeats use GREATEST() to avoid regression.
    update_values: Dict[str, Any] = {
        "patrol_cycle_count":         func.greatest(JobInstance.patrol_cycle_count, payload.cycle_index),
        "patrol_success_cycle_count": JobInstance.patrol_success_cycle_count + payload.success_delta,
        "patrol_failed_cycle_count":  JobInstance.patrol_failed_cycle_count + payload.failed_delta,
        "current_failure_streak":     payload.current_failure_streak,
        "next_retry_at":              next_retry_dt,
        "last_patrol_heartbeat_at":   now,
        "updated_at":                 now,
    }
    if payload.current_step is not None:
        update_values["current_patrol_step"] = payload.current_step
    capability = (payload.watcher_capability or "").strip()
    if capability:
        update_values["watcher_capability"] = capability[:32]

    # If Agent reports it consumed/observed a manual_action, clear it —
    # but ONLY when DB.manual_action still equals what Agent observed.
    # Why: 否则用户在 Agent observed → heartbeat 抵达之间二次点击 / 切换 (RETRY_NOW↔EXIT_REQUESTED)
    #      会被无条件清除静默吞掉,新意图永远不会被 Agent 看到。SQL CASE 让清除变成"DB 没改 → 清,
    #      DB 已是新意图 → 原样保留",与 ADR-0022 D7 manual_action 单字段语义一致。
    if payload.manual_action_observed:
        update_values["manual_action"] = case(
            (
                JobInstance.manual_action == payload.manual_action_observed,
                None,
            ),
            else_=JobInstance.manual_action,
        )

    # ADR-0022 D10: 写侧 CAS — status='RUNNING' guard 防御「预校验通过、CAS 阶段
    # recycler patrol_stall pass 在 _require_valid_runtime_lease 通过后才 commit」
    # 的 race。0 行返回 → 与改动 A 同 code 同语义,统一 JOB_NOT_RUNNING 出口。
    result = await db.execute(
        update(JobInstance)
        .where(
            JobInstance.id == job_id,
            JobInstance.status == JobStatus.RUNNING.value,
        )
        .values(**update_values)
        .returning(JobInstance.id)
    )
    if result.first() is None:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "JOB_NOT_RUNNING",
                "message": (
                    f"Job {job_id} status flipped during patrol-heartbeat write; "
                    "trigger /agent/recovery/sync to re-establish lease before next patrol cycle"
                ),
            },
        )
    await db.commit()

    record_patrol_heartbeat(
        failed_delta=payload.failed_delta,
        current_failure_streak=payload.current_failure_streak,
    )

    # Re-fetch to return canonical values + any pending manual_action newly set.
    # Use explicit column selection to avoid lazy-load / MissingGreenlet issues
    # on async PostgreSQL when expire_on_commit fires.
    result = await db.execute(
        select(
            JobInstance.patrol_cycle_count,
            JobInstance.patrol_success_cycle_count,
            JobInstance.patrol_failed_cycle_count,
            JobInstance.current_failure_streak,
            JobInstance.next_retry_at,
            JobInstance.manual_action,
        ).where(JobInstance.id == job_id)
    )
    row = result.one()
    return PatrolHeartbeatOut(
        job_id=job_id,
        patrol_cycle_count=row.patrol_cycle_count or 0,
        patrol_success_cycle_count=row.patrol_success_cycle_count or 0,
        patrol_failed_cycle_count=row.patrol_failed_cycle_count or 0,
        current_failure_streak=row.current_failure_streak or 0,
        next_retry_at=_iso_or_none(row.next_retry_at),
        manual_action=row.manual_action,
    )




# 路由 / 既有测试用的端点别名。
patrol_heartbeat = record_agent_patrol_heartbeat
