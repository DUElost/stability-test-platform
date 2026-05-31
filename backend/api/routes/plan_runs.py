"""PlanRun API — ADR-0020.

Provides PlanRun list/detail/jobs/summary endpoints.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import get_current_active_user, User
from backend.core.artifact_paths import (
    ArtifactPathError,
    ArtifactPathNotFoundError,
    get_local_artifact_roots,
    resolve_local_artifact_path,
)
from backend.core.audit import record_audit
from backend.core.database import get_db
from backend.core.metrics import record_patrol_manual_action
from backend.models.audit import AuditLog
from backend.models.enums import DeviceStatus, HostStatus, JobStatus, LeaseStatus, PlanRunStatus
from backend.models.host import Device, Host
from backend.models.device_lease import DeviceLease
from backend.core.job_timeout_config import (
    DISPATCHED_TIMEOUT_SECONDS,
    UNKNOWN_GRACE_SECONDS,
)
from backend.models.job import JobArtifact, JobInstance, JobLogSignal, StepTrace
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.services.plan_run_abort import (
    PlanRunAbortError,
    abort_plan_run,
)
from backend.services.plan_precheck import (
    PlanRunDispatchRetryError,
    retry_plan_run_dispatch,
)
from backend.services.plan_run_export import (
    build_plan_run_export,
    plan_run_export_to_markdown,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["plan-runs"])


# ── Schemas ──────────────────────────────────────────────────────────────

class StepTraceOut(BaseModel):
    id: int
    job_id: int
    step_id: str
    stage: str
    event_type: str
    status: str
    output: Optional[str] = None
    error_message: Optional[str] = None
    original_ts: str
    created_at: str

    class Config:
        from_attributes = True


class JobInstanceOut(BaseModel):
    id: int
    plan_run_id: Optional[int] = None
    plan_id: Optional[int] = None
    device_id: int
    device_serial: Optional[str] = None
    host_id: Optional[str] = None
    status: str
    status_reason: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    created_at: Optional[str] = None
    step_traces: list[StepTraceOut] = []

    class Config:
        from_attributes = True


class PlanRunOut(BaseModel):
    id: int
    plan_id: int
    status: str
    failure_threshold: float
    run_type: str
    triggered_by: Optional[str] = None
    started_at: str
    ended_at: Optional[str] = None
    result_summary: Optional[dict] = None
    # ADR-0021: dispatch gate progress lives under run_context.precheck.
    run_context: Optional[dict] = None
    plan_snapshot: Optional[dict] = None
    parent_plan_run_id: Optional[int] = None
    root_plan_run_id: Optional[int] = None
    chain_index: int = 0
    next_plan_triggered: bool = False
    jobs: list[JobInstanceOut] = []

    class Config:
        from_attributes = True


# ── Helpers ──────────────────────────────────────────────────────────────

def _iso(v) -> str | None:
    if v is None:
        return None
    return v.isoformat()


def _plan_run_out(pr: PlanRun, jobs: list[JobInstanceOut] | None = None) -> PlanRunOut:
    return PlanRunOut(
        id=pr.id,
        plan_id=pr.plan_id,
        status=pr.status,
        failure_threshold=pr.failure_threshold,
        run_type=pr.run_type,
        triggered_by=pr.triggered_by,
        started_at=_iso(pr.started_at) or "",
        ended_at=_iso(pr.ended_at),
        result_summary=pr.result_summary,
        run_context=pr.run_context,
        plan_snapshot=pr.plan_snapshot,
        parent_plan_run_id=pr.parent_plan_run_id,
        root_plan_run_id=pr.root_plan_run_id,
        chain_index=pr.chain_index or 0,
        next_plan_triggered=bool(pr.next_plan_triggered),
        jobs=jobs or [],
    )


def _step_out(t: StepTrace) -> StepTraceOut:
    return StepTraceOut(
        id=t.id, job_id=t.job_id, step_id=t.step_id, stage=t.stage,
        event_type=t.event_type, status=t.status, output=t.output,
        error_message=t.error_message,
        original_ts=_iso(t.original_ts) or "",
        created_at=_iso(t.created_at) or "",
    )


def _job_out(job: JobInstance, traces: list, device_serial: str | None = None) -> JobInstanceOut:
    return JobInstanceOut(
        id=job.id, plan_run_id=job.plan_run_id, plan_id=job.plan_id,
        device_id=job.device_id, device_serial=device_serial,
        host_id=job.host_id, status=job.status,
        status_reason=job.status_reason,
        started_at=_iso(job.started_at),
        ended_at=_iso(job.ended_at),
        created_at=_iso(job.created_at),
        step_traces=[_step_out(t) for t in traces],
    )


# ── Endpoints ────────────────────────────────────────────────────────────

@router.get("/plan-runs", response_model=ApiResponse[list[PlanRunOut]])
def list_plan_runs(
    skip: int = 0,
    limit: int = 50,
    plan_id: Optional[int] = None,
    status: Optional[PlanRunStatus] = Query(default=None),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    q = select(PlanRun).order_by(PlanRun.started_at.desc())
    if plan_id is not None:
        q = q.where(PlanRun.plan_id == plan_id)
    if status is not None:
        q = q.where(PlanRun.status == status.value)
    runs = db.execute(q.offset(skip).limit(limit)).scalars().all()
    return ok([_plan_run_out(r) for r in runs])


@router.get("/plan-runs/{run_id}", response_model=ApiResponse[PlanRunOut])
def get_plan_run(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="plan run not found")
    jobs = db.execute(
        select(JobInstance).where(JobInstance.plan_run_id == run_id)
    ).scalars().all()
    return ok(_plan_run_out(pr, jobs=[_job_out(j, []) for j in jobs]))


@router.get("/plan-runs/{run_id}/jobs", response_model=ApiResponse[list[JobInstanceOut]])
def list_plan_run_jobs(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    jobs = db.execute(
        select(JobInstance).where(JobInstance.plan_run_id == run_id)
    ).scalars().all()
    if not jobs:
        return ok([])

    device_ids = list({j.device_id for j in jobs})
    devices: dict[int, str] = {}
    if device_ids:
        rows = db.execute(
            select(Device.id, Device.serial).where(Device.id.in_(device_ids))
        ).all()
        devices = {r.id: r.serial for r in rows}

    job_ids = [j.id for j in jobs]
    all_traces = db.execute(
        select(StepTrace)
        .where(StepTrace.job_id.in_(job_ids))
        .order_by(StepTrace.original_ts)
    ).scalars().all()
    traces_by_job: dict[int, list] = {}
    for t in all_traces:
        traces_by_job.setdefault(t.job_id, []).append(t)

    return ok([
        _job_out(j, traces_by_job.get(j.id, []), devices.get(j.device_id))
        for j in jobs
    ])


# ── Abort ────────────────────────────────────────────────────────────────


class PlanRunAbortIn(BaseModel):
    reason: Optional[str] = None


@router.post(
    "/plan-runs/{run_id}/abort", response_model=ApiResponse[dict]
)
def abort_plan_run_endpoint(
    run_id: int,
    payload: Optional[PlanRunAbortIn] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """ADR-0021 D7 — abort a PlanRun.

    Returns 409 if the PlanRun is already terminal.  Otherwise releases
    active leases / marks PENDING jobs ABORTED / closes the run, then
    returns immediately (Agent drain happens asynchronously).
    """
    reason = (payload.reason if payload else None) or "aborted_by_user"
    try:
        summary = abort_plan_run(
            run_id,
            db=db,
            reason=reason,
            triggered_by=current_user.username if current_user else "api",
            audit_user_id=current_user.id if current_user else None,
            audit_username=current_user.username if current_user else None,
        )
    except PlanRunAbortError as exc:
        msg = str(exc)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=409, detail=msg)
    return ok(summary)


@router.post(
    "/plan-runs/{run_id}/retry-dispatch",
    response_model=ApiResponse[dict],
)
def retry_plan_run_dispatch_endpoint(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Re-enqueue the dispatch gate after a precheck / sync failure."""
    try:
        summary = retry_plan_run_dispatch(
            run_id,
            db=db,
            triggered_by=current_user.username if current_user else "api",
        )
    except PlanRunDispatchRetryError as exc:
        msg = str(exc)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        if "queue unavailable" in msg:
            raise HTTPException(status_code=503, detail=msg)
        raise HTTPException(status_code=409, detail=msg)
    return ok(summary)


# ── ADR-0022: Manual retry / exit for patrol-backoff jobs ───────────────────


_NON_TERMINAL_JOB_STATUSES = {
    JobStatus.PENDING.value,
    JobStatus.RUNNING.value,
}


class JobManualActionIn(BaseModel):
    reason: Optional[str] = None


class JobManualActionOut(BaseModel):
    job_id: int
    plan_run_id: int
    action: str          # 'manual_retry' | 'manual_exit'
    status: str          # job status after the action
    manual_action: Optional[str] = None
    next_retry_at: Optional[str] = None
    current_failure_streak: int = 0


def _load_job_in_run(db: Session, run_id: int, job_id: int) -> JobInstance:
    job = db.get(JobInstance, job_id)
    if job is None or job.plan_run_id != run_id:
        raise HTTPException(status_code=404, detail="job not found in this plan run")
    return job


def _emit_job_status_invalidation(
    run_id: int, job_id: int, status: str, reason: str
) -> None:
    """ADR-0021 C5c: notify the frontend that a job's row needs a refetch.

    Used by the sync manual-retry / manual-exit endpoints.  We deliberately
    use ``schedule_emit`` (thread-safe bridge) because these handlers run on
    sync sessions and must not await.  The payload mirrors the agent-emitted
    ``job_status`` event so the frontend's existing handler can reuse it as
    a pure invalidation hint — no DB state is conveyed in the payload.
    """
    try:
        from backend.realtime.socketio_server import schedule_emit
    except Exception:
        return
    try:
        schedule_emit(
            "job_status",
            {
                "type": "JOB_STATUS",
                "payload": {
                    "job_id": int(job_id),
                    "status": status,
                    "reason": reason,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            namespace="/dashboard",
            room=f"plan_run:{run_id}",
        )
    except Exception:
        logger.debug("emit_job_status_invalidation_failed", exc_info=True)


@router.post(
    "/plan-runs/{run_id}/jobs/{job_id}/manual-retry",
    response_model=ApiResponse[JobManualActionOut],
)
def manual_retry_job(
    run_id: int,
    job_id: int,
    payload: Optional[JobManualActionIn] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """ADR-0022 D7: clear backoff and force the next patrol cycle to run now.

    Sets ``next_retry_at = now()`` and ``manual_action = 'RETRY_NOW'`` so the
    Agent picks it up on the next heartbeat.  **Does not reset**
    ``current_failure_streak`` — diagnostic information is preserved.
    """
    job = _load_job_in_run(db, run_id, job_id)
    if job.status not in _NON_TERMINAL_JOB_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"job is in terminal status {job.status}; cannot retry",
        )

    # Why: 同向 manual_action 已等待 Agent 消费时,重复点击不再二次写 audit / emit / counter。
    #      合法语义:用户连点 N 次 retry,后端只该留 1 条审计 + 1 次 emit;EXIT_REQUESTED 切到
    #      RETRY_NOW 是真正的意图变更,不在此处短路。
    if job.manual_action == "RETRY_NOW":
        return ok(JobManualActionOut(
            job_id=job_id,
            plan_run_id=run_id,
            action="manual_retry",
            status=job.status,
            manual_action=job.manual_action,
            next_retry_at=_iso(job.next_retry_at),
            current_failure_streak=job.current_failure_streak or 0,
        ))

    reason = (payload.reason if payload else None) or "manual_retry"
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
            "reason": reason,
            "current_failure_streak": job.current_failure_streak or 0,
            "triggered_by": current_user.username if current_user else None,
        },
        user_id=current_user.id if current_user else None,
        username=current_user.username if current_user else None,
    )
    db.commit()
    db.refresh(job)

    logger.info(
        "patrol_manual_retry plan_run=%d job=%d streak=%d",
        run_id, job_id, job.current_failure_streak or 0,
    )
    record_patrol_manual_action("manual_retry")
    _emit_job_status_invalidation(run_id, job_id, job.status, "manual_retry")

    return ok(JobManualActionOut(
        job_id=job_id,
        plan_run_id=run_id,
        action="manual_retry",
        status=job.status,
        manual_action=job.manual_action,
        next_retry_at=_iso(job.next_retry_at),
        current_failure_streak=job.current_failure_streak or 0,
    ))


@router.post(
    "/plan-runs/{run_id}/jobs/{job_id}/manual-exit",
    response_model=ApiResponse[JobManualActionOut],
)
def manual_exit_job(
    run_id: int,
    job_id: int,
    payload: Optional[JobManualActionIn] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """ADR-0022 D7: request that the Agent skip the rest of patrol and abort.

    Sets ``manual_action = 'EXIT_REQUESTED'``.  The Agent observes this on the
    next heartbeat and exits the patrol loop **without running teardown** (BO4).
    Recycler / device lease release ensures the device returns to the pool.

    The job's status remains its current (PENDING/RUNNING) value here; it
    transitions to ABORTED once the Agent reports the terminal state via
    /jobs/{id}/complete (or via Recycler's stall detection).
    """
    job = _load_job_in_run(db, run_id, job_id)
    if job.status not in _NON_TERMINAL_JOB_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"job is in terminal status {job.status}; cannot exit",
        )

    # Why: 与 manual_retry 对称 — 同向 EXIT_REQUESTED 已等待 Agent 消费时短路,避免连点
    #      产生多条审计 + 多次 emit。RETRY_NOW 切 EXIT_REQUESTED 是真正的意图变更不短路。
    if job.manual_action == "EXIT_REQUESTED":
        return ok(JobManualActionOut(
            job_id=job_id,
            plan_run_id=run_id,
            action="manual_exit",
            status=job.status,
            manual_action=job.manual_action,
            next_retry_at=_iso(job.next_retry_at),
            current_failure_streak=job.current_failure_streak or 0,
        ))

    reason = (payload.reason if payload else None) or "manual_exit"
    now = datetime.now(timezone.utc)

    job.manual_action = "EXIT_REQUESTED"
    if not job.status_reason:
        job.status_reason = f"patrol_manual_exit_pending: {reason}"
    job.updated_at = now
    db.flush()

    record_audit(
        db,
        action="patrol_manual_exit",
        resource_type="job_instance",
        resource_id=job_id,
        details={
            "plan_run_id": run_id,
            "reason": reason,
            "current_failure_streak": job.current_failure_streak or 0,
            "triggered_by": current_user.username if current_user else None,
        },
        user_id=current_user.id if current_user else None,
        username=current_user.username if current_user else None,
    )
    db.commit()
    db.refresh(job)

    logger.info(
        "patrol_manual_exit plan_run=%d job=%d streak=%d",
        run_id, job_id, job.current_failure_streak or 0,
    )
    record_patrol_manual_action("manual_exit")
    _emit_job_status_invalidation(run_id, job_id, job.status, "manual_exit_pending")

    return ok(JobManualActionOut(
        job_id=job_id,
        plan_run_id=run_id,
        action="manual_exit",
        status=job.status,
        manual_action=job.manual_action,
        next_retry_at=_iso(job.next_retry_at),
        current_failure_streak=job.current_failure_streak or 0,
    ))


# ── ADR-0021/ADR-0022 C5a₂: PlanRunDetailPage 聚合端点 ──────────────────
#
# 5 个独立 GET 端点供前端分别拉取,所有返回值都是 PlanRun 范围内的聚合视图;
# 注意:
#   - 这些端点是 RUNNING / 终态都可调用的(终态后值定格,前端可缓存)
#   - chain 端点会沿 parent_plan_run_id 链向 root 回溯;next 节点是 Plan.next_plan_id
#     指向的 Plan,是否已触发由 PlanRun.next_plan_triggered 决定
#   - timeline 端点的 step_trace 聚合仅返回 init / patrol / teardown 三阶段的
#     succeeded/failed 计数;ADR-0022 后 patrol 成功步骤不再写 step_trace,
#     真实 patrol 进度从 JobInstance.patrol_*_cycle_count 派生
#   - events 端点融合 4 个数据源:
#     1) step_trace(失败步骤,作为 init/patrol/teardown 阶段事件)
#     2) job_log_signal(watcher 异常,作为 patrol 阶段事件)
#     3) audit_logs(plan_run / job_instance / dispatch_gate,作为 system 事件)
#     4) PlanRun 自身 trigger 事件 + patrol heartbeat 周期摘要
#   - devices 端点的 ui_status 派生规则:
#       COMPLETED                            → completed
#       FAILED / ABORTED                     → failed
#       UNKNOWN                              → unknown (grace / recovery window)
#       PENDING                              → pending
#       RUNNING + manual_action=EXIT_REQ.    → backoff
#       RUNNING + next_retry_at > now        → backoff
#       RUNNING + log_signal_count > 0       → risk
#       RUNNING (其他)                        → running
#   - watcher-summary 默认 60min 窗口,与上一窗口对比得到 trend
#
# 性能保障:依赖 ADR-0022 patrol 心跳聚合 + ADR-0021 C5a₂ 新建的两个
# step_trace 复合索引 (idx_step_trace_job_stage / idx_step_trace_job_status_ts)。

# ── 公共常量 ─────────────────────────────────────────────────────────────

_LIVE_PATROL_HEARTBEAT_WINDOW = timedelta(seconds=180)  # heartbeat 视为活跃的窗口
_DEFAULT_WATCHER_WINDOW_MIN   = 60
_MAX_WATCHER_WINDOW_MIN       = 1440  # 1 天
_MAX_EVENTS_LIMIT             = 500
_DEFAULT_EVENTS_LIMIT         = 100

_FAILED_JOB_STATUSES   = {JobStatus.FAILED.value, JobStatus.ABORTED.value}
_TERMINAL_PR_STATUSES  = {
    PlanRunStatus.SUCCESS.value,
    PlanRunStatus.PARTIAL_SUCCESS.value,
    PlanRunStatus.FAILED.value,
    PlanRunStatus.DEGRADED.value,
}


def _require_plan_run(db: Session, run_id: int) -> PlanRun:
    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="plan run not found")
    return pr


def _duration_seconds(start, end) -> float | None:
    if start is None:
        return None
    if end is None:
        end = datetime.now(timezone.utc)
    try:
        return max(0.0, (_aware(end) - _aware(start)).total_seconds())
    except TypeError:
        return None


def _aware(ts: datetime | None) -> datetime | None:
    """Normalise naive datetimes to UTC.

    SQLite (used in test mode) does not store tz info; PostgreSQL does.
    Several aggregation paths compare DB-stored values against
    ``datetime.now(timezone.utc)`` and would otherwise raise
    ``TypeError: can't compare offset-naive and offset-aware datetimes``.
    """
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


# ── Endpoint 1: GET /plan-runs/{id}/chain ────────────────────────────────

class ChainNodeOut(BaseModel):
    plan_id: int
    plan_name: Optional[str] = None
    plan_run_id: Optional[int] = None
    status: str                        # PlanRun.status 或 'pending'(尚未触发)
    chain_index: int
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    failure_threshold: float
    pass_rate: Optional[float] = None  # 来自 PlanRun.result_summary
    is_current: bool = False
    is_blocked: bool = False
    block_reason: Optional[str] = None


class PlanChainOut(BaseModel):
    plan_run_id: int
    root_plan_run_id: int
    nodes: list[ChainNodeOut]


def _chain_node_from_run(pr: PlanRun, plan_name: Optional[str], is_current: bool) -> ChainNodeOut:
    summary = pr.result_summary or {}
    pass_rate = summary.get("pass_rate") if isinstance(summary, dict) else None
    return ChainNodeOut(
        plan_id=pr.plan_id,
        plan_name=plan_name,
        plan_run_id=pr.id,
        status=pr.status,
        chain_index=pr.chain_index or 0,
        started_at=_iso(pr.started_at),
        ended_at=_iso(pr.ended_at),
        duration_seconds=_duration_seconds(pr.started_at, pr.ended_at),
        failure_threshold=pr.failure_threshold,
        pass_rate=pass_rate,
        is_current=is_current,
    )


@router.get("/plan-runs/{run_id}/chain", response_model=ApiResponse[PlanChainOut])
def get_plan_run_chain(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0020 §6: PlanRun chain 上下文 — 沿 parent_plan_run_id 回溯到 root,
    再沿 next_plan_id + next_plan_triggered 向前查找下一段。

    返回的 nodes 列表按 chain_index 升序,包含:
      - 0..N 个 parent PlanRun (已触发)
      - 1 个 current PlanRun(is_current=True)
      - 0..1 个未触发的 next Plan 节点(plan_run_id=None, status='pending')
    """
    pr = _require_plan_run(db, run_id)

    # 1) 沿 chain 收集所有已存在的 PlanRun
    root_id = pr.root_plan_run_id or pr.id
    chain_runs = db.execute(
        select(PlanRun)
        .where((PlanRun.id == root_id) | (PlanRun.root_plan_run_id == root_id))
        .order_by(PlanRun.chain_index.asc(), PlanRun.id.asc())
    ).scalars().all()

    # 2) 批量取 plan name
    plan_ids = list({r.plan_id for r in chain_runs})
    plan_names: dict[int, str] = {}
    if plan_ids:
        rows = db.execute(
            select(Plan.id, Plan.name).where(Plan.id.in_(plan_ids))
        ).all()
        plan_names = {r.id: r.name for r in rows}

    nodes = [
        _chain_node_from_run(r, plan_names.get(r.plan_id), is_current=(r.id == pr.id))
        for r in chain_runs
    ]

    # 3) 候选 next Plan(链尾):取 chain_runs 末尾的 PlanRun,看其对应 Plan.next_plan_id
    if chain_runs:
        tail = chain_runs[-1]
        tail_plan = db.get(Plan, tail.plan_id)
        if tail_plan and tail_plan.next_plan_id and not tail.next_plan_triggered:
            next_plan = db.get(Plan, tail_plan.next_plan_id)
            if next_plan is not None:
                # 推断 block 原因
                blocked = False
                reason = None
                summary = tail.result_summary or {}
                chain_fail = (
                    summary.get("chain_dispatch_failed")
                    if isinstance(summary, dict)
                    else None
                )
                if isinstance(chain_fail, dict) and chain_fail.get("error"):
                    blocked = True
                    reason = f"下游 Plan 派发失败: {chain_fail['error']}"
                elif tail.status == PlanRunStatus.RUNNING.value:
                    blocked = True
                    reason = "parent PlanRun 仍在运行,需等待终态"
                elif tail.status not in (PlanRunStatus.SUCCESS.value, PlanRunStatus.PARTIAL_SUCCESS.value):
                    blocked = True
                    pr_summary_failed = summary.get("failed", 0) if isinstance(summary, dict) else 0
                    pr_summary_total  = summary.get("total", 0)  if isinstance(summary, dict) else 0
                    if pr_summary_total:
                        rate = pr_summary_failed / pr_summary_total
                        reason = (
                            f"failure_rate {rate:.1%} > threshold "
                            f"{tail.failure_threshold:.1%}; chain 终止"
                        )
                    else:
                        reason = f"parent status={tail.status}; chain 不触发"
                elif tail.status in (PlanRunStatus.SUCCESS.value, PlanRunStatus.PARTIAL_SUCCESS.value):
                    blocked = True
                    reason = "等待下游 Plan 自动派发"

                nodes.append(ChainNodeOut(
                    plan_id=next_plan.id,
                    plan_name=next_plan.name,
                    plan_run_id=None,
                    status="pending",
                    chain_index=(tail.chain_index or 0) + 1,
                    failure_threshold=next_plan.failure_threshold,
                    is_blocked=blocked,
                    block_reason=reason,
                ))

    return ok(PlanChainOut(
        plan_run_id=pr.id,
        root_plan_run_id=root_id,
        nodes=nodes,
    ))


# ── Endpoint 2: GET /plan-runs/{id}/timeline ─────────────────────────────

class StageStepOut(BaseModel):
    step_key: str
    script_name: str
    stage: str
    sort_order: int
    device_total: int                  # = PlanRun jobs 总数
    device_succeeded: int              # event_type=COMPLETED + status=COMPLETED
    device_failed: int                 # event_type=FAILED or status=FAILED
    device_skipped: int = 0            # v3: event_type=COMPLETED + status=SKIPPED
    device_running: int                # = max(0, total - succeeded - failed - skipped)


class StageOut(BaseModel):
    stage: str                         # init / patrol / teardown
    status: str                        # pending / running / completed / failed / skipped
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    device_total: int
    device_succeeded: int = 0
    device_failed: int = 0
    device_skipped: int = 0            # v3: summed from steps
    # patrol 专属(从 JobInstance.patrol_*_cycle_count 聚合)
    patrol_cycle_index: Optional[int] = None
    patrol_active_devices: Optional[int] = None
    patrol_interval_seconds: Optional[int] = None
    steps: list[StageStepOut] = []


class PlanRunTimelineOut(BaseModel):
    plan_run_id: int
    current_stage: str                 # init / patrol / teardown / done / pending
    stages: list[StageOut]
    aborted_job_count: int = 0         # v3: ABORTED jobs 计数 (顶层 banner 用)
    triggered_at: str
    triggered_by: Optional[str] = None
    run_type: str
    plan_name: Optional[str] = None


def _stage_status_from_steps(
    stage: str,
    *,
    pr_status: str,
    job_total: int,
    succeeded: int,
    failed: int,
    has_running_jobs: bool,
) -> str:
    """从 step_trace 聚合派生 stage 整体状态。

    init 阶段:任意 step 失败 → failed;全部 step ok 且 patrol 已启动 → completed;否则 running/pending
    teardown 阶段:终态 PlanRun 时若有 step_trace → completed; 否则 pending/skipped(manual_exit)
    patrol 阶段:running 时 → running; 终态时 → completed (或 failed)
    """
    if pr_status in _TERMINAL_PR_STATUSES:
        if stage == "teardown":
            if succeeded == 0 and failed == 0:
                return "skipped"
            return "completed" if failed == 0 else "failed"
        return "completed" if failed == 0 else "failed"
    # PlanRun RUNNING
    if stage == "init":
        if failed > 0:
            return "failed"
        if succeeded >= job_total:
            return "completed"
        return "running" if has_running_jobs else "pending"
    if stage == "patrol":
        if succeeded == 0 and failed == 0 and not has_running_jobs:
            return "pending"
        return "running"
    return "pending"  # teardown 在 RUNNING 时永远是 pending


@router.get("/plan-runs/{run_id}/timeline", response_model=ApiResponse[PlanRunTimelineOut])
def get_plan_run_timeline(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0021/ADR-0022 C5a₂: 业务流时间线 — 按 stage 聚合 step_trace,
    输出三阶段的 succeeded/failed/running 计数与每个 step 的设备级进度。

    patrol 阶段额外暴露 patrol_cycle_index / active_devices(60s 内 heartbeat),
    数据源是 JobInstance.patrol_*_cycle_count(ADR-0022 心跳聚合,而非 step_trace)。
    """
    pr = _require_plan_run(db, run_id)
    plan = db.get(Plan, pr.plan_id)

    jobs = db.execute(
        select(JobInstance).where(JobInstance.plan_run_id == run_id)
    ).scalars().all()
    job_total = len(jobs)
    job_ids = [j.id for j in jobs]
    has_running = any(j.status == JobStatus.RUNNING.value for j in jobs)

    # 1) 静态 step 定义(从 plan_snapshot 或 PlanStep)
    snapshot_steps: list[dict] = []
    if isinstance(pr.plan_snapshot, dict):
        snapshot_steps = pr.plan_snapshot.get("steps") or []
    if not snapshot_steps and pr.plan_id:
        rows = db.execute(
            select(PlanStep)
            .where(PlanStep.plan_id == pr.plan_id)
            .order_by(PlanStep.stage, PlanStep.sort_order)
        ).scalars().all()
        snapshot_steps = [
            {
                "step_key": s.step_key,
                "script_name": s.script_name,
                "stage": s.stage,
                "sort_order": s.sort_order,
            }
            for s in rows
        ]

    # 2) 聚合 step_trace:按 (stage, step_id, event_type, status) 分桶
    #    v3: 基于真实 (event_type, status) 组合; 排除 STARTED 中间事件
    step_agg: dict[tuple[str, str], dict[str, int]] = {}
    if job_ids:
        agg_rows = db.execute(
            select(
                StepTrace.stage,
                StepTrace.step_id,
                StepTrace.event_type,
                StepTrace.status,
                func.count(StepTrace.id),
            )
            .where(
                StepTrace.job_id.in_(job_ids),
                StepTrace.event_type != "STARTED",
            )
            .group_by(
                StepTrace.stage, StepTrace.step_id,
                StepTrace.event_type, StepTrace.status,
            )
        ).all()
        for stage, step_id, event_type, status, cnt in agg_rows:
            key = (stage, step_id)
            bucket = step_agg.setdefault(
                key, {"succeeded": 0, "failed": 0, "skipped": 0},
            )
            if event_type == "COMPLETED" and status == "COMPLETED":
                bucket["succeeded"] += cnt
            elif event_type == "COMPLETED" and status == "SKIPPED":
                bucket["skipped"] += cnt
            elif event_type == "FAILED" or status == "FAILED":
                bucket["failed"] += cnt
            # event_type=RUN_COMPLETE + step_id=__job__: step_id 不在
            # plan_snapshot, 不会被纳入 stage 桶, 无需特殊处理

    # 3) 按 stage 组织 steps;按 plan_snapshot 顺序保留
    stages_def: dict[str, list[StageStepOut]] = {"init": [], "patrol": [], "teardown": []}
    for s in snapshot_steps:
        stage = s.get("stage")
        if stage not in stages_def:
            continue
        key = (stage, s.get("step_key", ""))
        agg = step_agg.get(key, {"succeeded": 0, "failed": 0, "skipped": 0})
        succeeded = agg["succeeded"]
        failed = agg["failed"]
        skipped = agg["skipped"]
        running = max(0, job_total - succeeded - failed - skipped) if has_running else 0
        stages_def[stage].append(StageStepOut(
            step_key=s.get("step_key", ""),
            script_name=s.get("script_name", ""),
            stage=stage,
            sort_order=int(s.get("sort_order", 0)),
            device_total=job_total,
            device_succeeded=succeeded,
            device_failed=failed,
            device_skipped=skipped,
            device_running=running,
        ))

    # 4) patrol 心跳聚合
    patrol_cycle_index = None
    patrol_active = None
    if jobs:
        cycles = [j.patrol_cycle_count or 0 for j in jobs]
        patrol_cycle_index = max(cycles) if cycles else 0
        live_threshold = datetime.now(timezone.utc) - _LIVE_PATROL_HEARTBEAT_WINDOW
        patrol_active = sum(
            1 for j in jobs
            if j.last_patrol_heartbeat_at
            and _aware(j.last_patrol_heartbeat_at) >= live_threshold
        )

    # 5) 每 stage 的 succeeded/failed/skipped 总数(求和 step 级)
    def _sum_stage(stage_name: str, accessor: str) -> int:
        return sum(getattr(s, accessor) for s in stages_def.get(stage_name, []))

    # 6) 每 stage 的 started_at / ended_at — 取该 stage 第一个/最后一个 step_trace
    stage_ts: dict[str, dict[str, Optional[datetime]]] = {
        "init": {"started_at": None, "ended_at": None},
        "patrol": {"started_at": None, "ended_at": None},
        "teardown": {"started_at": None, "ended_at": None},
    }
    if job_ids:
        ts_rows = db.execute(
            select(
                StepTrace.stage,
                func.min(StepTrace.original_ts),
                func.max(StepTrace.original_ts),
            )
            .where(StepTrace.job_id.in_(job_ids))
            .group_by(StepTrace.stage)
        ).all()
        for stage, ts_min, ts_max in ts_rows:
            if stage in stage_ts:
                stage_ts[stage]["started_at"] = ts_min
                stage_ts[stage]["ended_at"]   = ts_max

    # 7) current_stage 推导
    current_stage = "pending"
    if pr.status in _TERMINAL_PR_STATUSES:
        current_stage = "done"
    elif stage_ts["teardown"]["started_at"]:
        current_stage = "teardown"
    elif stage_ts["patrol"]["started_at"] or patrol_cycle_index:
        current_stage = "patrol"
    elif stage_ts["init"]["started_at"]:
        current_stage = "init"

    stages_out: list[StageOut] = []
    for stage_name in ("init", "patrol", "teardown"):
        steps = stages_def.get(stage_name, [])
        succeeded = _sum_stage(stage_name, "device_succeeded")
        failed    = _sum_stage(stage_name, "device_failed")
        skipped   = _sum_stage(stage_name, "device_skipped")
        st = _stage_status_from_steps(
            stage_name,
            pr_status=pr.status,
            job_total=job_total,
            succeeded=succeeded,
            failed=failed,
            has_running_jobs=has_running,
        )
        s_at = stage_ts[stage_name]["started_at"]
        e_at = stage_ts[stage_name]["ended_at"] if st in {"completed", "failed", "skipped"} else None
        stage_obj = StageOut(
            stage=stage_name,
            status=st,
            started_at=_iso(s_at),
            ended_at=_iso(e_at),
            duration_seconds=_duration_seconds(s_at, e_at),
            device_total=job_total,
            device_succeeded=succeeded,
            device_failed=failed,
            device_skipped=skipped,
            steps=steps,
        )
        if stage_name == "patrol":
            stage_obj.patrol_cycle_index = patrol_cycle_index
            stage_obj.patrol_active_devices = patrol_active
            stage_obj.patrol_interval_seconds = (
                plan.patrol_interval_seconds if plan else None
            )
        stages_out.append(stage_obj)

    aborted_job_count = sum(1 for j in jobs if j.status == JobStatus.ABORTED.value)

    return ok(PlanRunTimelineOut(
        plan_run_id=pr.id,
        current_stage=current_stage,
        stages=stages_out,
        aborted_job_count=aborted_job_count,
        triggered_at=_iso(pr.started_at) or "",
        triggered_by=pr.triggered_by,
        run_type=pr.run_type,
        plan_name=plan.name if plan else None,
    ))


# ── Endpoint 3: GET /plan-runs/{id}/events ───────────────────────────────

class EventOut(BaseModel):
    ts: str
    stage: str                         # init/patrol/teardown/system/trigger
    severity: str                      # ok/info/warn/err
    category: str                      # step / log_signal / audit / system / trigger
    title: str
    description: str = ""
    job_id: Optional[int] = None
    device_id: Optional[int] = None
    device_serial: Optional[str] = None
    ref: Optional[dict] = None         # {type, id} — 用于跳转 step_trace / log_signal


class PlanRunEventsOut(BaseModel):
    plan_run_id: int
    events: list[EventOut]
    total: int                         # 当前过滤条件下的总数(facets 后)
    facets: dict                       # {by_stage: {...}, by_severity: {...}}


def _log_signal_severity(category: str) -> str:
    cat = (category or "").upper()
    if cat in {"AEE", "VENDOR_AEE", "TOMBSTONE"}:
        return "err"
    if cat in {"ANR", "MOBILELOG"}:
        return "warn"
    return "info"


def _log_signal_title(category: str) -> str:
    cat = (category or "").upper()
    return {
        "AEE": "AEE Crash 检测",
        "VENDOR_AEE": "Vendor AEE Crash",
        "ANR": "ANR",
        "TOMBSTONE": "Tombstone",
        "MOBILELOG": "MOBILELOG",
    }.get(cat, cat or "异常事件")


def _min_aware_dt(*values: datetime | None) -> datetime | None:
    present = [_aware(v) for v in values if v is not None]
    return min(present) if present else None


def _max_aware_dt(*values: datetime | None) -> datetime | None:
    present = [_aware(v) for v in values if v is not None]
    return max(present) if present else None


def _job_has_patrol_signal(job: JobInstance) -> bool:
    return (
        (job.patrol_cycle_count or 0) > 0
        or job.last_patrol_heartbeat_at is not None
        or bool(job.current_patrol_step)
    )


def _build_synthetic_stage_events(
    pr: PlanRun,
    jobs: list[JobInstance],
    stage_meta: dict[str, dict[str, object]],
) -> list[EventOut]:
    if not jobs:
        return []

    patrol_jobs = [job for job in jobs if _job_has_patrol_signal(job)]
    patrol_cycle_index = max((job.patrol_cycle_count or 0) for job in jobs)
    latest_patrol_heartbeat = _max_aware_dt(
        *(job.last_patrol_heartbeat_at for job in jobs)
    )
    patrol_started_at = _min_aware_dt(
        stage_meta["patrol"]["started_at"],
        *(job.started_at for job in patrol_jobs),
        latest_patrol_heartbeat,
    )
    live_threshold = datetime.now(timezone.utc) - _LIVE_PATROL_HEARTBEAT_WINDOW
    patrol_active_devices = sum(
        1
        for job in jobs
        if job.last_patrol_heartbeat_at
        and _aware(job.last_patrol_heartbeat_at) >= live_threshold
    )
    events: list[EventOut] = []
    init_started_at = _aware(stage_meta["init"]["started_at"])
    init_ended_at = _aware(stage_meta["init"]["ended_at"])
    teardown_started_at = _aware(stage_meta["teardown"]["started_at"])
    teardown_ended_at = _aware(stage_meta["teardown"]["ended_at"])
    init_has_terminal_success = bool(stage_meta["init"]["has_terminal_success"])
    init_has_failure = bool(stage_meta["init"]["has_failure"])
    teardown_has_terminal_success = bool(stage_meta["teardown"]["has_terminal_success"])
    teardown_has_failure = bool(stage_meta["teardown"]["has_failure"])

    init_completed_at = _max_aware_dt(
        init_ended_at,
        init_started_at,
        patrol_started_at,
        pr.started_at,
    )
    if (
        patrol_started_at
        or teardown_started_at
        or (
            pr.status in _TERMINAL_PR_STATUSES
            and init_has_terminal_success
            and not init_has_failure
        )
    ):
        events.append(EventOut(
            ts=_iso(init_completed_at) or "",
            stage="init",
            severity="ok",
            category="system",
            title="INIT 完成",
            description="已进入后续执行阶段",
            ref={"type": "plan_run", "id": pr.id},
        ))

    if patrol_started_at:
        events.append(EventOut(
            ts=_iso(patrol_started_at) or "",
            stage="patrol",
            severity="info",
            category="system",
            title="PATROL 开始",
            description="巡检阶段已启动",
            ref={"type": "plan_run", "id": pr.id},
        ))

    patrol_progress_at = _max_aware_dt(
        latest_patrol_heartbeat,
        _aware(stage_meta["patrol"]["ended_at"]),
        _aware(stage_meta["patrol"]["started_at"]),
        *(job.started_at for job in patrol_jobs),
    )
    if pr.status not in _TERMINAL_PR_STATUSES and patrol_cycle_index > 0 and patrol_progress_at:
        heartbeat_window_minutes = int(_LIVE_PATROL_HEARTBEAT_WINDOW.total_seconds() // 60)
        patrol_desc = (
            f"最近 {heartbeat_window_minutes} 分钟内 {patrol_active_devices} 台设备上报心跳"
            if patrol_active_devices > 0
            else "已记录巡检进展"
        )
        events.append(EventOut(
            ts=_iso(patrol_progress_at) or "",
            stage="patrol",
            severity="info",
            category="system",
            title=f"PATROL 进行中 · 周期 #{patrol_cycle_index}",
            description=patrol_desc,
            ref={"type": "plan_run", "id": pr.id},
        ))

    if teardown_started_at:
        events.append(EventOut(
            ts=_iso(teardown_started_at) or "",
            stage="teardown",
            severity="info",
            category="system",
            title="TEARDOWN 开始",
            description="开始收尾清理",
            ref={"type": "plan_run", "id": pr.id},
        ))

    if teardown_ended_at and teardown_has_terminal_success and not teardown_has_failure:
        events.append(EventOut(
            ts=_iso(teardown_ended_at) or "",
            stage="teardown",
            severity="ok",
            category="system",
            title="TEARDOWN 完成",
            description="收尾阶段已结束",
            ref={"type": "plan_run", "id": pr.id},
        ))

    return [event for event in events if event.ts]


@router.get("/plan-runs/{run_id}/events", response_model=ApiResponse[PlanRunEventsOut])
def get_plan_run_events(
    run_id: int,
    stage: Optional[str] = Query(None, description="init / patrol / teardown / system / trigger / all"),
    severity: Optional[str] = Query(None, description="ok / info / warn / err / all"),
    limit: int = Query(_DEFAULT_EVENTS_LIMIT, ge=1, le=_MAX_EVENTS_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0021/ADR-0022 C5a₂: 业务流事件流 — 融合 trigger / 合成阶段进展 /
    step_trace 失败 / log_signal / audit_logs,统一封装为 EventOut。

    支持 stage / severity 维度过滤;facets 始终基于"未过滤"的全集计算
    (前端过滤标签上的总数显示原本的总量,而 events 列表是当前过滤后的页)。
    """
    pr = _require_plan_run(db, run_id)
    jobs = db.execute(
        select(JobInstance).where(JobInstance.plan_run_id == run_id)
    ).scalars().all()
    job_ids = [j.id for j in jobs]
    job_to_device = {j.id: j.device_id for j in jobs}

    devices_by_id: dict[int, str] = {}
    if jobs:
        device_ids = list({j.device_id for j in jobs})
        rows = db.execute(
            select(Device.id, Device.serial).where(Device.id.in_(device_ids))
        ).all()
        devices_by_id = {r.id: r.serial for r in rows}

    stage_meta: dict[str, dict[str, object]] = {
        "init": {
            "started_at": None,
            "ended_at": None,
            "has_terminal_success": False,
            "has_failure": False,
        },
        "patrol": {
            "started_at": None,
            "ended_at": None,
            "has_terminal_success": False,
            "has_failure": False,
        },
        "teardown": {
            "started_at": None,
            "ended_at": None,
            "has_terminal_success": False,
            "has_failure": False,
        },
    }
    if job_ids:
        trace_rows = db.execute(
            select(
                StepTrace.stage,
                StepTrace.event_type,
                StepTrace.status,
                StepTrace.original_ts,
            ).where(StepTrace.job_id.in_(job_ids))
        ).all()
        for trace_stage, event_type, status, original_ts in trace_rows:
            if trace_stage not in stage_meta:
                continue
            bucket = stage_meta[trace_stage]
            started_at = bucket["started_at"]
            ended_at = bucket["ended_at"]
            if started_at is None or original_ts < started_at:
                bucket["started_at"] = original_ts
            if ended_at is None or original_ts > ended_at:
                bucket["ended_at"] = original_ts
            if event_type == "COMPLETED" and status in {"COMPLETED", "SKIPPED"}:
                bucket["has_terminal_success"] = True
            if event_type == "FAILED" or status in {"FAILED", "ABORTED"}:
                bucket["has_failure"] = True

    events: list[EventOut] = []

    # 1) trigger 事件 — PlanRun 自身
    events.append(EventOut(
        ts=_iso(pr.started_at) or "",
        stage="trigger",
        severity="ok",
        category="trigger",
        title=f"PlanRun #{pr.id} 启动",
        description=f"触发方式 {pr.run_type}" + (f" · 用户 {pr.triggered_by}" if pr.triggered_by else ""),
        ref={"type": "plan_run", "id": pr.id},
    ))

    # 2) 有限的阶段进展合成事件,避免活跃 patrol 只剩 trigger/异常事件
    events.extend(_build_synthetic_stage_events(pr, jobs, stage_meta))

    # 3) step_trace 失败 / abort 事件
    #    v3: 收紧 WHERE — 只取 event_type=FAILED 或 RUN_COMPLETE + terminal status;
    #    区分 ABORTED (warn + "Job 已中止") 与 FAILED (err + "Job 失败")
    if job_ids:
        bad_traces = db.execute(
            select(StepTrace)
            .where(
                (StepTrace.job_id.in_(job_ids))
                & (
                    (StepTrace.event_type == "FAILED")
                    | (
                        (StepTrace.event_type == "RUN_COMPLETE")
                        & (StepTrace.status.in_(["FAILED", "ABORTED"]))
                    )
                )
            )
            .order_by(StepTrace.original_ts.desc())
            .limit(_MAX_EVENTS_LIMIT)
        ).scalars().all()
        for t in bad_traces:
            dev_id = job_to_device.get(t.job_id)
            is_aborted = (
                t.step_id == "__job__"
                and t.event_type == "RUN_COMPLETE"
                and t.status == "ABORTED"
            )
            is_job_failed = (
                t.step_id == "__job__"
                and t.event_type == "RUN_COMPLETE"
                and t.status == "FAILED"
            )
            if is_aborted:
                title = f"Job #{t.job_id} 已中止"
                evt_severity = "warn"
                evt_stage = "system"
            elif is_job_failed:
                title = f"Job #{t.job_id} 失败"
                evt_severity = "err"
                evt_stage = "system"
            else:
                title = f"{t.stage}.{t.step_id} 失败"
                evt_severity = "err"
                evt_stage = t.stage if t.stage in {"init", "patrol", "teardown"} else "system"
            events.append(EventOut(
                ts=_iso(t.original_ts) or "",
                stage=evt_stage,
                severity=evt_severity,
                category="step",
                title=title,
                description=(t.error_message or "")[:512],
                job_id=t.job_id,
                device_id=dev_id,
                device_serial=devices_by_id.get(dev_id) if dev_id else None,
                ref={"type": "step_trace", "id": t.id},
            ))

    # 4) log_signal 事件(watcher 异常)
    if job_ids:
        signal_rows = db.execute(
            select(JobLogSignal)
            .where(JobLogSignal.job_id.in_(job_ids))
            .order_by(JobLogSignal.detected_at.desc())
            .limit(_MAX_EVENTS_LIMIT)
        ).scalars().all()
        for s in signal_rows:
            dev_id = job_to_device.get(s.job_id)
            events.append(EventOut(
                ts=_iso(s.detected_at) or "",
                stage="patrol",  # watcher signals 都视为 patrol 阶段事件
                severity=_log_signal_severity(s.category),
                category="log_signal",
                title=_log_signal_title(s.category),
                description=(s.first_lines or "")[:512] or s.path_on_device,
                job_id=s.job_id,
                device_id=dev_id,
                device_serial=devices_by_id.get(dev_id) or s.device_serial,
                ref={"type": "log_signal", "id": s.id},
            ))

    # 5) audit_logs(plan_run / job_instance / dispatch_gate)
    # AuditLog.resource_id 列宽到 String(64) 后(g0b1c2d3e4f5 迁移),需将整型主键
    # 转字符串再比较;PG 严格类型不会做隐式 varchar=int 转换。
    job_id_strs = [str(j) for j in (job_ids or [-1])]
    audit_q = select(AuditLog).where(
        ((AuditLog.resource_type == "plan_run") & (AuditLog.resource_id == str(run_id)))
        | ((AuditLog.resource_type == "job_instance") & (AuditLog.resource_id.in_(job_id_strs)))
    ).order_by(AuditLog.timestamp.desc()).limit(_MAX_EVENTS_LIMIT)
    for log in db.execute(audit_q).scalars().all():
        sev = "warn" if "abort" in (log.action or "") or "fail" in (log.action or "") else "info"
        events.append(EventOut(
            ts=_iso(log.timestamp) or "",
            stage="system",
            severity=sev,
            category="audit",
            title=log.action or "audit",
            description=str(log.details or {})[:512],
            ref={"type": "audit_log", "id": log.id},
        ))

    # 6) facets — 基于全集
    facets_stage: dict[str, int] = {}
    facets_sev:   dict[str, int] = {}
    for e in events:
        facets_stage[e.stage] = facets_stage.get(e.stage, 0) + 1
        facets_sev[e.severity] = facets_sev.get(e.severity, 0) + 1
    facets_stage["all"] = len(events)
    facets_sev["all"]   = len(events)

    # 7) 过滤
    filtered = events
    if stage and stage.lower() != "all":
        filtered = [e for e in filtered if e.stage == stage.lower()]
    if severity and severity.lower() != "all":
        filtered = [e for e in filtered if e.severity == severity.lower()]

    # 8) 按 ts 倒序 + 分页
    filtered.sort(key=lambda e: e.ts, reverse=True)
    total = len(filtered)
    page = filtered[offset: offset + limit]

    return ok(PlanRunEventsOut(
        plan_run_id=pr.id,
        events=page,
        total=total,
        facets={"by_stage": facets_stage, "by_severity": facets_sev},
    ))


# ── Endpoint 4: GET /plan-runs/{id}/devices ──────────────────────────────

class DeviceMatrixItem(BaseModel):
    device_id: int
    device_serial: Optional[str] = None
    device_model: Optional[str] = None
    host_id: Optional[str] = None
    job_id: int
    job_status: str
    ui_status: str                     # completed/running/failed/risk/backoff/pending
    current_stage: str
    current_step: Optional[str] = None
    patrol_cycle_count: int = 0
    patrol_success_cycle_count: int = 0
    patrol_failed_cycle_count: int = 0
    current_failure_streak: int = 0
    next_retry_at: Optional[str] = None
    manual_action: Optional[str] = None
    log_signal_count: int = 0
    last_heartbeat_at: Optional[str] = None
    started_at: Optional[str] = None
    created_at: Optional[str] = None
    ended_at: Optional[str] = None
    status_reason: Optional[str] = None   # ADR-0021: pending_timeout / agent never claimed / etc.
    grace_remaining_seconds: Optional[int] = None
    pending_claim_remaining_seconds: Optional[int] = None
    busy_reason: Optional[str] = None
    busy_lease_job_id: Optional[int] = None


class PlanRunDevicesOut(BaseModel):
    plan_run_id: int
    total: int
    by_status: dict                    # {all/completed/running/failed/risk/backoff/pending: int}
    by_host: dict                      # {host_id: int}
    devices: list[DeviceMatrixItem]


def _ui_status_for_job(j: JobInstance, now: datetime) -> str:
    s = j.status
    if s == JobStatus.COMPLETED.value:
        return "completed"
    if s == JobStatus.UNKNOWN.value:
        return "unknown"
    if s in _FAILED_JOB_STATUSES:
        return "failed"
    if s == JobStatus.PENDING.value:
        return "pending"
    # RUNNING 分支
    if (j.manual_action or "") == "EXIT_REQUESTED":
        return "backoff"
    nrt = _aware(j.next_retry_at)
    if nrt and nrt > now:
        return "backoff"
    if (j.log_signal_count or 0) > 0:
        return "risk"
    return "running"


def _current_stage_for_job(j: JobInstance) -> str:
    s = j.status
    if s == JobStatus.COMPLETED.value:
        return "done"
    if s == JobStatus.UNKNOWN.value:
        return "unknown"
    if s in _FAILED_JOB_STATUSES:
        return "failed"
    if s == JobStatus.PENDING.value:
        return "pending"
    # RUNNING:有 patrol heartbeat 即视为 patrol;否则 init
    if (j.patrol_cycle_count or 0) > 0 or j.last_patrol_heartbeat_at:
        return "patrol"
    return "init"


def _grace_remaining_seconds(j: JobInstance, now: datetime) -> Optional[int]:
    if j.status != JobStatus.UNKNOWN.value:
        return None
    ended = _aware(j.ended_at)
    if ended is None:
        return None
    remaining = (ended + timedelta(seconds=UNKNOWN_GRACE_SECONDS) - now).total_seconds()
    return max(0, int(remaining))


def _pending_claim_remaining_seconds(j: JobInstance, now: datetime) -> Optional[int]:
    if j.status != JobStatus.PENDING.value:
        return None
    base = _aware(j.created_at) or _aware(j.started_at)
    if base is None:
        return None
    remaining = (base + timedelta(seconds=DISPATCHED_TIMEOUT_SECONDS) - now).total_seconds()
    return max(0, int(remaining))


def _adb_state_excluded(adb_state: str | None) -> bool:
    """Match claim pre-filter: offline/unknown adb_state excludes the device."""
    state = (adb_state or "device").lower()
    return state in ("offline", "unknown")


def _derive_busy_reason(
    device: Device | None,
    host_status: str | None,
    lease_job_id: int | None,
) -> tuple[Optional[str], Optional[int]]:
    if device is None:
        return None, None
    if host_status == HostStatus.OFFLINE.value:
        return "host_offline", lease_job_id
    if _adb_state_excluded(device.adb_state):
        return "adb_excluded", lease_job_id
    if not device.adb_connected or device.status == DeviceStatus.OFFLINE.value:
        return "device_offline", lease_job_id
    if lease_job_id is not None:
        return "active_lease", lease_job_id
    if device.status == DeviceStatus.BUSY.value:
        return "active_lease", None
    return None, None


@router.get("/plan-runs/{run_id}/devices", response_model=ApiResponse[PlanRunDevicesOut])
def get_plan_run_devices(
    run_id: int,
    status: Optional[str] = Query(None, description="ui_status 过滤(可选)"),
    host_id: Optional[str] = Query(None, description="host_id 过滤(可选)"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0021/ADR-0022 C5a₂: 设备执行矩阵 — 每台设备一行,
    包含 patrol 心跳聚合、退避状态、watcher 异常计数。

    by_status / by_host facet 始终基于"未过滤"的全集计算,
    便于前端筛选 chip 同时显示总数和当前数。
    """
    _require_plan_run(db, run_id)
    jobs = db.execute(
        select(JobInstance).where(JobInstance.plan_run_id == run_id)
    ).scalars().all()
    if not jobs:
        return ok(PlanRunDevicesOut(
            plan_run_id=run_id, total=0,
            by_status={"all": 0}, by_host={}, devices=[],
        ))

    # device 元数据
    device_ids = list({j.device_id for j in jobs})
    device_meta: dict[int, Device] = {}
    host_status_by_id: dict[str, str] = {}
    if device_ids:
        rows = db.execute(
            select(Device).where(Device.id.in_(device_ids))
        ).scalars().all()
        device_meta = {d.id: d for d in rows}
        host_ids = list({d.host_id for d in rows if d.host_id})
        if host_ids:
            host_rows = db.execute(
                select(Host.id, Host.status).where(Host.id.in_(host_ids))
            ).all()
            host_status_by_id = {r.id: r.status for r in host_rows}

    active_lease_by_device: dict[int, int] = {}
    if device_ids:
        lease_rows = db.execute(
            select(DeviceLease.device_id, DeviceLease.job_id).where(
                DeviceLease.device_id.in_(device_ids),
                DeviceLease.status == LeaseStatus.ACTIVE.value,
            )
        ).all()
        active_lease_by_device = {r.device_id: r.job_id for r in lease_rows if r.job_id}

    now = datetime.now(timezone.utc)
    items: list[DeviceMatrixItem] = []
    by_status: dict[str, int] = {"all": 0}
    by_host: dict[str, int] = {}

    for j in jobs:
        ui = _ui_status_for_job(j, now)
        cur_stage = _current_stage_for_job(j)
        dev = device_meta.get(j.device_id)
        serial = dev.serial if dev else None
        model = dev.model if dev else None
        host_st = host_status_by_id.get(j.host_id) if j.host_id else None
        lease_job_id = active_lease_by_device.get(j.device_id)
        busy_reason, busy_lease_job_id = _derive_busy_reason(dev, host_st, lease_job_id)
        items.append(DeviceMatrixItem(
            device_id=j.device_id,
            device_serial=serial,
            device_model=model,
            host_id=j.host_id,
            job_id=j.id,
            job_status=j.status,
            ui_status=ui,
            current_stage=cur_stage,
            current_step=j.current_patrol_step,
            patrol_cycle_count=j.patrol_cycle_count or 0,
            patrol_success_cycle_count=j.patrol_success_cycle_count or 0,
            patrol_failed_cycle_count=j.patrol_failed_cycle_count or 0,
            current_failure_streak=j.current_failure_streak or 0,
            next_retry_at=_iso(j.next_retry_at),
            manual_action=j.manual_action,
            log_signal_count=j.log_signal_count or 0,
            last_heartbeat_at=_iso(j.last_patrol_heartbeat_at),
            started_at=_iso(j.started_at),
            created_at=_iso(j.created_at),
            ended_at=_iso(j.ended_at),
            status_reason=j.status_reason,
            grace_remaining_seconds=_grace_remaining_seconds(j, now),
            pending_claim_remaining_seconds=_pending_claim_remaining_seconds(j, now),
            busy_reason=busy_reason,
            busy_lease_job_id=busy_lease_job_id,
        ))
        by_status["all"] += 1
        by_status[ui] = by_status.get(ui, 0) + 1
        if j.host_id:
            by_host[j.host_id] = by_host.get(j.host_id, 0) + 1

    # 过滤(facets 已经基于全集)
    filtered = items
    if status and status.lower() != "all":
        filtered = [d for d in filtered if d.ui_status == status.lower()]
    if host_id and host_id.lower() != "all":
        filtered = [d for d in filtered if d.host_id == host_id]

    return ok(PlanRunDevicesOut(
        plan_run_id=run_id,
        total=len(items),
        by_status=by_status,
        by_host=by_host,
        devices=filtered,
    ))


# ── Endpoint 5: GET /plan-runs/{id}/watcher-summary ──────────────────────

class WatcherCategoryOut(BaseModel):
    category: str
    count: int
    affected_device_count: int
    trend_change: int                  # 当前窗口 - 上一窗口同长度
    latest_device_serial: Optional[str] = None
    latest_detected_at: Optional[str] = None


# M0/PR #2: AEE 细分聚合(crash / vendor_crash / anr 互斥 + by_package)
# 数据来源:JobLogSignal.extra (JSONB);仅当 source='reconciler' 的 signal
# 携带完整 extra 字段(event_type/package_name/aee_ts/nfs_path/pull_source);
# 旧 inotifyd 路径 signal 没有 extra,自动落入 unknown 桶。
class PackageStatOut(BaseModel):
    package_name: str                 # 空/缺失统一归 "unknown"
    crash_count: int                  # category=AEE 且 event_type=CRASH(按 nfs_path 去重)
    vendor_crash_count: int           # category=VENDOR_AEE 同条件
    anr_count: int                    # category=ANR OR extra.event_type='ANR'
    latest_detected_at: Optional[str] = None


class AeeBreakdownOut(BaseModel):
    crash_count: int                  # COUNT(DISTINCT extra->>'nfs_path') under AEE+CRASH
    vendor_crash_count: int           # 同上,VENDOR_AEE+CRASH(与 crash_count 互斥)
    anr_count: int                    # COUNT(DISTINCT extra->>'nfs_path') under ANR
    packages: list[str]               # distinct package_name(已合并 unknown 桶)
    by_package: list[PackageStatOut]  # 按 crash + vendor_crash + anr 总数降序


class WatcherSummaryOut(BaseModel):
    plan_run_id: int
    window_minutes: int
    window_start_at: str
    window_end_at: str
    categories: list[WatcherCategoryOut]
    total: int
    affected_device_count: int
    total_devices: int
    abnormal_rate: float               # affected_device_count / total_devices
    threshold: float
    exceeded: bool
    # M0/PR #2: AEE 细分(reconciler signal 才会填充);无关联 Job 时 None
    aee_breakdown: Optional[AeeBreakdownOut] = None
    # M0/C-6 (§2.4 #5): 该 PlanRun 下 Job 的 watcher 能力快照(取最"降级"的一档)。
    #   来源:JobInstance.watcher_capability 列(由 Agent 在 complete/heartbeat 回填的
    #   JobSession.summary.watcher_capability)。无可靠来源时为 None;
    #   前端在 'unavailable' 时显示「Watcher 不可用」徽章(watcher 未正常启动,
    #   AEE reconciler 可能未运行,勿当作有 reconciler 兜底)。
    watcher_capability: Optional[str] = None
    # M1/T1-3a: 双写灰度态字段
    # legacy_patrol_in_snapshot — PlanRun.plan_snapshot.lifecycle.patrol 是否仍含
    #   scan_aee / export_mobilelogs(M2 关 patrol 后历史 PlanRun 仍能识别)
    # pull_sources — 当前窗口 log_signal.extra.pull_source 的 distinct 集合
    #   (M1 期通常仅 'reconciler';空数组 = 无 reconciler emit)
    legacy_patrol_in_snapshot: bool = False
    pull_sources: list[str] = []


@router.get(
    "/plan-runs/{run_id}/watcher-summary",
    response_model=ApiResponse[WatcherSummaryOut],
)
def get_plan_run_watcher_summary(
    run_id: int,
    window_minutes: int = Query(_DEFAULT_WATCHER_WINDOW_MIN, ge=1, le=_MAX_WATCHER_WINDOW_MIN),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0018 / ADR-0021 C5a₂: 最近 N 分钟内 watcher log_signal 按 category
    聚合,带 trend(对比上一相同长度窗口的差值)。

    abnormal_rate = 当前窗口受影响设备数 / PlanRun 总设备数;
    与 PlanRun.failure_threshold 比较给出 exceeded 标志。
    """
    pr = _require_plan_run(db, run_id)

    # M1/T1-3a: 双写灰度态 — 提前算出供早返回 / 主返回共用
    legacy_in_snapshot = _detect_legacy_patrol_in_snapshot(pr.plan_snapshot)

    job_rows = db.execute(
        select(JobInstance.id, JobInstance.device_id).where(JobInstance.plan_run_id == run_id)
    ).all()
    job_ids   = [r.id for r in job_rows]
    total_dev = len({r.device_id for r in job_rows})

    now = datetime.now(timezone.utc)
    window_delta = timedelta(minutes=window_minutes)
    cur_start  = now - window_delta
    prev_start = now - 2 * window_delta

    if not job_ids:
        return ok(WatcherSummaryOut(
            plan_run_id=pr.id,
            window_minutes=window_minutes,
            window_start_at=_iso(cur_start) or "",
            window_end_at=_iso(now) or "",
            categories=[], total=0, affected_device_count=0,
            total_devices=0, abnormal_rate=0.0,
            threshold=pr.failure_threshold, exceeded=False,
            watcher_capability=None,
            legacy_patrol_in_snapshot=legacy_in_snapshot,
            pull_sources=[],
        ))

    # 当前窗口聚合(按 category 分组)
    cur_rows = db.execute(
        select(
            JobLogSignal.category,
            func.count(JobLogSignal.id),
            func.count(func.distinct(JobLogSignal.device_serial)),
            func.max(JobLogSignal.detected_at),
        )
        .where(
            (JobLogSignal.job_id.in_(job_ids))
            & (JobLogSignal.detected_at >= cur_start)
            & (JobLogSignal.detected_at <= now)
        )
        .group_by(JobLogSignal.category)
    ).all()

    # 上一窗口仅计 count(用于 trend)
    prev_rows = db.execute(
        select(JobLogSignal.category, func.count(JobLogSignal.id))
        .where(
            (JobLogSignal.job_id.in_(job_ids))
            & (JobLogSignal.detected_at >= prev_start)
            & (JobLogSignal.detected_at < cur_start)
        )
        .group_by(JobLogSignal.category)
    ).all()
    prev_counts = {row[0]: row[1] for row in prev_rows}

    # 找当前窗口 latest_device_serial
    latest_serial_by_cat: dict[str, str] = {}
    if cur_rows:
        latest_rows = db.execute(
            select(JobLogSignal.category, JobLogSignal.device_serial, JobLogSignal.detected_at)
            .where(
                (JobLogSignal.job_id.in_(job_ids))
                & (JobLogSignal.detected_at >= cur_start)
            )
            .order_by(JobLogSignal.detected_at.desc())
        ).all()
        for cat, serial, _ts in latest_rows:
            latest_serial_by_cat.setdefault(cat, serial)

    # 受影响设备数(去重所有 category)
    affected_total = db.execute(
        select(func.count(func.distinct(JobLogSignal.device_serial))).where(
            (JobLogSignal.job_id.in_(job_ids))
            & (JobLogSignal.detected_at >= cur_start)
        )
    ).scalar() or 0

    categories_out: list[WatcherCategoryOut] = []
    total = 0
    for cat, count, affected, latest_ts in cur_rows:
        total += count
        categories_out.append(WatcherCategoryOut(
            category=cat,
            count=count,
            affected_device_count=affected,
            trend_change=count - prev_counts.get(cat, 0),
            latest_device_serial=latest_serial_by_cat.get(cat),
            latest_detected_at=_iso(latest_ts),
        ))
    categories_out.sort(key=lambda c: c.count, reverse=True)

    abnormal_rate = (affected_total / total_dev) if total_dev else 0.0

    # M0/PR #2: AEE 细分(crash / vendor_crash / anr 互斥 + by_package)
    # PG-only(JSONB):未含 extra 的 legacy signal 自动落入 unknown package +
    # NULL nfs_path,通过 path_on_device 兜底为 ANR 去重键。
    aee_breakdown = _aggregate_aee_breakdown(
        db, job_ids=job_ids, cur_start=cur_start, now=now,
    )
    pull_sources_list = _aggregate_pull_sources(
        db, job_ids=job_ids, cur_start=cur_start, now=now,
    )
    watcher_capability = _aggregate_watcher_capability(db, job_ids=job_ids)

    return ok(WatcherSummaryOut(
        plan_run_id=pr.id,
        window_minutes=window_minutes,
        window_start_at=_iso(cur_start) or "",
        window_end_at=_iso(now) or "",
        categories=categories_out,
        total=total,
        affected_device_count=affected_total,
        total_devices=total_dev,
        abnormal_rate=round(abnormal_rate, 4),
        threshold=pr.failure_threshold,
        exceeded=abnormal_rate > pr.failure_threshold,
        aee_breakdown=aee_breakdown,
        watcher_capability=watcher_capability,
        legacy_patrol_in_snapshot=legacy_in_snapshot,
        pull_sources=pull_sources_list,
    ))


# M0/C-6: watcher_capability 降级严重度排序 — 取该 PlanRun 下 Job 中"最降级"的一档,
# 使得只要有任一设备落到 reconciler 单通道(unavailable)即可在前端给出提示。
# 数值越大越降级;未知能力按 0 处理(不触发降级徽章)。
_CAPABILITY_SEVERITY: dict[str, int] = {
    "unavailable":       40,   # 探测全失败 → reconciler 单通道(目标徽章场景)
    "polling":           30,   # inotifyd 不可用,reconciler 承担拉+emit
    "inotifyd_shell":    20,
    "inotifyd_root":     10,
    "inotifyd_realtime": 10,
    "stub":               5,
    "skipped":           -10,  # watcher 未启动(非降级,前端不徽章)
}


def _aggregate_watcher_capability(db: Session, *, job_ids: list[int]) -> Optional[str]:
    """C-6 (§2.4 #5): 汇总该 PlanRun 下 Job 的 watcher 能力快照。

    取 JobInstance.watcher_capability 列中"最降级"的一档(按 _CAPABILITY_SEVERITY);
    全部为 NULL(Agent 未回填)时返回 None。该值仅用于前端提示,不参与聚合计数,
    因此跨方言(PG / SQLite)均可工作。
    """
    if not job_ids:
        return None
    rows = db.execute(
        select(JobInstance.watcher_capability)
        .where(JobInstance.id.in_(job_ids))
        .where(JobInstance.watcher_capability.isnot(None))
    ).all()
    caps = [str(r[0]) for r in rows if r[0]]
    if not caps:
        return None
    return max(caps, key=lambda c: _CAPABILITY_SEVERITY.get(c, 0))


def _detect_legacy_patrol_in_snapshot(plan_snapshot: Any) -> bool:
    """M1/T1-3a: 检测 PlanRun.plan_snapshot.lifecycle.patrol 是否仍含
    scan_aee / export_mobilelogs 步骤(双写模式标识)。

    兼容两种 patrol 形态:
      - dict(`{"interval_seconds": 60, "steps": [...]}`,ADR-0020 标准)
      - list(早期 fallback 直存 steps)
    """
    if not isinstance(plan_snapshot, dict):
        return False
    lifecycle = plan_snapshot.get("lifecycle")
    if not isinstance(lifecycle, dict):
        return False
    patrol = lifecycle.get("patrol")
    if isinstance(patrol, list):
        steps = patrol
    elif isinstance(patrol, dict):
        steps = patrol.get("steps") or []
    else:
        return False
    legacy_actions = {"script:scan_aee", "script:export_mobilelogs"}
    return any(
        isinstance(s, dict) and str(s.get("action") or "") in legacy_actions
        for s in steps
    )


def _aggregate_pull_sources(
    db: Session,
    *,
    job_ids: list[int],
    cur_start: datetime,
    now: datetime,
) -> list[str]:
    """M1/T1-3a: 收集当前窗口 log_signal.extra.pull_source 的 distinct 集合。

    PG-only(JSONB):未含 extra 或 pull_source 的旧 signal 不计入;
    返回有序去重列表,前端按其判定 reconciler-only / 双写 / patrol-only 徽章。
    """
    if not job_ids:
        return []
    rows = db.execute(
        text("""
            SELECT DISTINCT extra->>'pull_source' AS src
            FROM job_log_signal
            WHERE job_id = ANY(:job_ids)
              AND detected_at >= :cur_start
              AND detected_at <= :now
              AND extra ? 'pull_source'
        """),
        {"job_ids": job_ids, "cur_start": cur_start, "now": now},
    ).all()
    return sorted({str(r[0]) for r in rows if r[0]})


def _aggregate_aee_breakdown(
    db: Session,
    *,
    job_ids: list[int],
    cur_start: datetime,
    now: datetime,
) -> AeeBreakdownOut:
    """按 package_name 聚合 AEE/VENDOR_AEE 崩溃与 ANR;reconciler signal 携带
    extra.nfs_path 时按 nfs_path 去重(同目录视为同 crash),ANR 用 path_on_device
    兜底以兼容旧 inotifyd 路径无 extra 的 signal。

    返回零值 AeeBreakdownOut 而非 None — 调用方决定是否上抛 None(早返回路径)。
    """
    sql = text("""
        SELECT
            COALESCE(NULLIF(extra->>'package_name', ''), 'unknown') AS pkg,
            COUNT(DISTINCT extra->>'nfs_path') FILTER (
                WHERE category = 'AEE'
                  AND COALESCE(extra->>'event_type', 'CRASH') = 'CRASH'
            ) AS crash_count,
            COUNT(DISTINCT extra->>'nfs_path') FILTER (
                WHERE category = 'VENDOR_AEE'
                  AND COALESCE(extra->>'event_type', 'CRASH') = 'CRASH'
            ) AS vendor_crash_count,
            COUNT(DISTINCT path_on_device) FILTER (
                WHERE category = 'ANR'
                   OR extra->>'event_type' = 'ANR'
            ) AS anr_count,
            MAX(detected_at) AS latest_detected_at
        FROM job_log_signal
        WHERE job_id = ANY(:job_ids)
          AND detected_at >= :cur_start
          AND detected_at <= :now
          AND (
              category IN ('AEE', 'VENDOR_AEE', 'ANR')
              OR extra->>'event_type' = 'ANR'
          )
        GROUP BY pkg
        ORDER BY (
            COUNT(DISTINCT extra->>'nfs_path') FILTER (
                WHERE category = 'AEE'
                  AND COALESCE(extra->>'event_type', 'CRASH') = 'CRASH'
            )
            + COUNT(DISTINCT extra->>'nfs_path') FILTER (
                WHERE category = 'VENDOR_AEE'
                  AND COALESCE(extra->>'event_type', 'CRASH') = 'CRASH'
            )
            + COUNT(DISTINCT path_on_device) FILTER (
                WHERE category = 'ANR'
                   OR extra->>'event_type' = 'ANR'
            )
        ) DESC, pkg ASC
    """)

    rows = db.execute(
        sql, {"job_ids": list(job_ids), "cur_start": cur_start, "now": now},
    ).all()

    by_package: list[PackageStatOut] = []
    crash_total = 0
    vendor_crash_total = 0
    anr_total = 0
    for pkg, crash, vendor_crash, anr, latest_ts in rows:
        # 排除三类计数全 0 的行(理论上 WHERE 已过滤,防御性兜底)
        if not (crash or vendor_crash or anr):
            continue
        by_package.append(PackageStatOut(
            package_name=pkg,
            crash_count=int(crash or 0),
            vendor_crash_count=int(vendor_crash or 0),
            anr_count=int(anr or 0),
            latest_detected_at=_iso(latest_ts),
        ))
        crash_total += int(crash or 0)
        vendor_crash_total += int(vendor_crash or 0)
        anr_total += int(anr or 0)

    return AeeBreakdownOut(
        crash_count=crash_total,
        vendor_crash_count=vendor_crash_total,
        anr_count=anr_total,
        packages=[p.package_name for p in by_package],
        by_package=by_package,
    )


# ── Endpoint 6: GET /plan-runs/{id}/aee-reconciliation (M1 / T1-4) ─────────
# 只读灰度对账:把 reconciler / legacy_patrol / inotifyd 三路 emit 的 AEE 信号条数
# 与(可选)NFS 侧 .dbg 文件清单对齐,供 M1 双写期人工对账 reconciler 漏报率。
# 纯只读,不做任何破坏性操作。

# pull_source → 标准化桶;非 AEE 路径(inotifyd 仅服务 ANR/MOBILELOG)在此端点
# 仅作可见性参考,不参与 NFS .dbg 对账(后者只比 AEE/VENDOR_AEE)。
_RECON_PULL_SOURCES = ("reconciler", "legacy_patrol", "inotifyd")


class AeeReconBySerialOut(BaseModel):
    device_serial: Optional[str] = None
    reconciler: int = 0
    legacy_patrol: int = 0
    inotifyd: int = 0
    other: int = 0
    total: int = 0


class AeeReconciliationOut(BaseModel):
    plan_run_id: int
    # signal 侧:AEE+VENDOR_AEE 按 pull_source 分桶,按 nfs_path(去重)/path_on_device 计数
    reconciler_emitted: int
    legacy_patrol_emitted: int
    inotifyd_emitted: int
    other_emitted: int
    total_emitted: int
    by_serial: list[AeeReconBySerialOut]
    # NFS 侧:仅校验本 Run signal 的 extra.nfs_path 条目目录(非设备级 rglob)
    nfs_dbg_files: Optional[int] = None
    nfs_root_scanned: Optional[str] = None
    signal_nfs_paths_checked: int = 0
    nfs_entries_verified: int = 0
    # signal 有 nfs_path 但目录不存在或条目下无 *.dbg
    missing_on_disk: list[str] = []
    # 已校验条目中有 .dbg 但 reconciler signal 未覆盖的 basename(启发式)
    missing_in_signal: list[str] = []
    notes: list[str] = []


def _aggregate_aee_recon_signals(
    db: Session, *, job_ids: list[int],
) -> tuple[dict[str, AeeReconBySerialOut], dict[str, int], set[str]]:
    """按 (device_serial, pull_source) 聚合 AEE/VENDOR_AEE 信号条数(crash-dir 去重)。

    去重键优先 extra.nfs_path(目录级=同一 crash 事件),缺失退回 path_on_device,
    再退回 id(保证至少计数一次)。返回 (by_serial, totals, reconciler_nfs_dirs)。
    reconciler_nfs_dirs = reconciler 信号 nfs_path 的 basename 集合,供 missing 对账。
    PG-only(JSONB / ANY)。
    """
    by_serial: dict[str, AeeReconBySerialOut] = {}
    totals: dict[str, int] = {k: 0 for k in (*_RECON_PULL_SOURCES, "other", "total")}
    recon_dirs: set[str] = set()
    if not job_ids:
        return by_serial, totals, recon_dirs

    rows = db.execute(
        text("""
            SELECT
                device_serial,
                COALESCE(NULLIF(extra->>'pull_source', ''), 'unknown') AS src,
                COUNT(DISTINCT COALESCE(
                    NULLIF(extra->>'nfs_path', ''), path_on_device, CAST(id AS text)
                )) AS cnt
            FROM job_log_signal
            WHERE job_id = ANY(:job_ids)
              AND category IN ('AEE', 'VENDOR_AEE')
            GROUP BY device_serial, src
        """),
        {"job_ids": list(job_ids)},
    ).all()

    for serial, src, cnt in rows:
        key = serial or "(unknown)"
        agg = by_serial.setdefault(key, AeeReconBySerialOut(device_serial=serial))
        n = int(cnt or 0)
        bucket = src if src in _RECON_PULL_SOURCES else "other"
        setattr(agg, bucket, getattr(agg, bucket) + n)
        agg.total += n
        totals[bucket] += n
        totals["total"] += n

    # reconciler 信号的 nfs_path basename,用于 NFS .dbg 对账(缺失检测)
    dir_rows = db.execute(
        text("""
            SELECT DISTINCT extra->>'nfs_path' AS nfs_path
            FROM job_log_signal
            WHERE job_id = ANY(:job_ids)
              AND category IN ('AEE', 'VENDOR_AEE')
              AND extra->>'pull_source' = 'reconciler'
              AND extra ? 'nfs_path'
              AND NULLIF(extra->>'nfs_path', '') IS NOT NULL
        """),
        {"job_ids": list(job_ids)},
    ).all()
    for (nfs_path,) in dir_rows:
        if nfs_path:
            recon_dirs.add(Path(str(nfs_path)).name)

    return by_serial, totals, recon_dirs


def _path_within_artifact_roots(path: Path) -> bool:
    resolved = path.resolve(strict=False)
    return any(resolved.is_relative_to(root) for root in get_local_artifact_roots())


def _collect_aee_signal_nfs_paths(db: Session, *, job_ids: list[int]) -> list[str]:
    """本 PlanRun 下 AEE/VENDOR_AEE 信号携带的去重 extra.nfs_path 列表。"""
    if not job_ids:
        return []
    rows = db.execute(
        text("""
            SELECT DISTINCT extra->>'nfs_path' AS nfs_path
            FROM job_log_signal
            WHERE job_id = ANY(:job_ids)
              AND category IN ('AEE', 'VENDOR_AEE')
              AND extra ? 'nfs_path'
              AND NULLIF(extra->>'nfs_path', '') IS NOT NULL
        """),
        {"job_ids": list(job_ids)},
    ).all()
    return [str(nfs_path) for (nfs_path,) in rows if nfs_path]


def _aee_entry_dir_for_nfs_path(path: Path) -> Path:
    """AEE signal.extra.nfs_path 的契约就是 crash 条目目录本身。"""
    return path


def _count_dbg_in_entry_dir(entry_dir: Path) -> int:
    """仅统计本 crash 条目目录树内的 *.dbg(不对 {folder}/{serial} 级 rglob)。"""
    if not entry_dir.is_dir():
        return 0
    return sum(1 for f in entry_dir.rglob("*.dbg") if f.is_file())


def _verify_signal_nfs_paths(
    nfs_paths: list[str],
    recon_dirs: set[str],
) -> tuple[
    Optional[int],
    Optional[set[str]],
    list[str],
    list[str],
    int,
    int,
    list[str],
    list[str],
]:
    """按 signal nfs_path 做存在性校验与条目级 *.dbg 计数。

    返回 (nfs_dbg_files, dbg_entry_basenames, missing_in_signal, missing_on_disk,
    paths_checked, entries_verified, notes, checked_paths)。
    """
    notes: list[str] = []
    if not nfs_paths:
        notes.append(
            "NFS 对账以 signal 携带的 extra.nfs_path 为准;本 Run 无 nfs_path,"
            "无法枚举未上报条目。"
        )
        return None, None, [], [], 0, 0, notes, []

    cap = 200_000
    total_dbg = 0
    dbg_entries: set[str] = set()
    missing_on_disk: list[str] = []
    checked_paths: list[str] = []
    paths_checked = 0
    entries_verified = 0

    try:
        for raw in nfs_paths:
            entry = Path(raw.strip())
            if not _path_within_artifact_roots(entry):
                notes.append(f"跳过白名单外 nfs_path: {entry}")
                continue

            if paths_checked == 0:
                notes.append(
                    "NFS 对账仅校验本 Run signal 的 extra.nfs_path 条目目录(非设备级 rglob)。"
                )
                notes.append("无 nfs_path 的 signal 不计入 NFS 侧。")

            resolved = entry.resolve(strict=False)
            paths_checked += 1
            entry_dir = _aee_entry_dir_for_nfs_path(resolved)
            checked_paths.append(str(entry_dir))
            label = entry_dir.name or str(entry_dir)

            if not entry_dir.exists():
                missing_on_disk.append(label)
                continue

            entries_verified += 1
            dbg_count = _count_dbg_in_entry_dir(entry_dir)
            if dbg_count == 0:
                missing_on_disk.append(label)
            else:
                total_dbg += dbg_count
                dbg_entries.add(label)

            if total_dbg >= cap:
                notes.append(f"NFS *.dbg 计数达到上限 {cap},已截断。")
                break
    except OSError as exc:
        notes.append(f"NFS 校验出错: {exc}")
        return (
            None,
            None,
            [],
            missing_on_disk,
            paths_checked,
            entries_verified,
            notes,
            checked_paths,
        )

    if paths_checked == 0:
        notes.append("无法限定本 Run 的 NFS 范围(无位于 artifact 白名单内的 nfs_path),请用 signal 侧对账。")
        return None, None, [], [], 0, 0, notes, []

    missing_in_signal = sorted(dbg_entries - recon_dirs)
    if missing_in_signal:
        notes.append(
            "missing_in_signal 为已校验条目中有 .dbg 但 reconciler signal 未覆盖的 "
            "basename;无法发现 signal 未携带路径的磁盘条目。"
        )

    return (
        total_dbg,
        dbg_entries,
        missing_in_signal,
        sorted(set(missing_on_disk)),
        paths_checked,
        entries_verified,
        notes,
        checked_paths,
    )


@router.get(
    "/plan-runs/{run_id}/aee-reconciliation",
    response_model=ApiResponse[AeeReconciliationOut],
)
def get_plan_run_aee_reconciliation(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """M1/T1-4: 只读灰度对账端点。

    比对该 PlanRun 下 AEE/VENDOR_AEE log_signal 按 pull_source 分桶的条数(crash-dir
    去重),并在后端可访问 NFS 时按 signal extra.nfs_path 校验条目目录内 *.dbg 计数,
    供 M1 双写期评估 reconciler 漏报率(目标 < 5%)。纯只读;不以设备目录 rglob 为 ground truth。
    """
    pr = _require_plan_run(db, run_id)

    job_ids = [
        r.id for r in db.execute(
            select(JobInstance.id).where(JobInstance.plan_run_id == run_id)
        ).all()
    ]

    by_serial_map, totals, recon_dirs = _aggregate_aee_recon_signals(db, job_ids=job_ids)

    signal_nfs_paths = _collect_aee_signal_nfs_paths(db, job_ids=job_ids)
    (
        nfs_count,
        _nfs_dbg_entries,
        missing_in_signal,
        missing_on_disk,
        paths_checked,
        entries_verified,
        notes,
        checked_paths,
    ) = _verify_signal_nfs_paths(signal_nfs_paths, recon_dirs)

    by_serial = sorted(
        by_serial_map.values(),
        key=lambda s: (-s.total, s.device_serial or ""),
    )

    if not job_ids:
        notes.append("该 PlanRun 无关联 Job,signal 侧计数为空。")

    if checked_paths:
        scanned_summary = ", ".join(checked_paths[:5])
        if len(checked_paths) > 5:
            scanned_summary += f" …(+{len(checked_paths) - 5})"
        nfs_root_scanned = scanned_summary
    else:
        nfs_root_scanned = None

    return ok(AeeReconciliationOut(
        plan_run_id=pr.id,
        reconciler_emitted=totals["reconciler"],
        legacy_patrol_emitted=totals["legacy_patrol"],
        inotifyd_emitted=totals["inotifyd"],
        other_emitted=totals["other"],
        total_emitted=totals["total"],
        by_serial=by_serial,
        nfs_dbg_files=nfs_count,
        nfs_root_scanned=nfs_root_scanned,
        signal_nfs_paths_checked=paths_checked,
        nfs_entries_verified=entries_verified,
        missing_on_disk=missing_on_disk,
        missing_in_signal=missing_in_signal,
        notes=notes,
    ))


@router.get("/plan-runs/{run_id}/report/export")
def export_plan_run_report(
    run_id: int,
    format: str = Query("markdown"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """Export PlanRun summary + devices + timeline (bounded to avoid OOM)."""
    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="plan run not found")

    data = build_plan_run_export(db, pr)
    fmt = format.strip().lower()
    if fmt == "json":
        return JSONResponse(
            content=data,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="plan-run-{run_id}-report.json"'
                ),
            },
        )
    if fmt != "markdown":
        raise HTTPException(status_code=400, detail="format must be markdown or json")
    markdown = plan_run_export_to_markdown(data)
    return PlainTextResponse(
        markdown,
        headers={
            "Content-Disposition": (
                f'attachment; filename="plan-run-{run_id}-report.md"'
            ),
        },
    )


@router.get("/plan-runs/{run_id}/summary", response_model=ApiResponse[dict])
def get_plan_run_summary(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="plan run not found")

    jobs_result = db.execute(
        select(
            JobInstance.status,
            func.count(JobInstance.id),
        )
        .where(JobInstance.plan_run_id == run_id)
        .group_by(JobInstance.status)
    )
    status_counts = {row[0]: row[1] for row in jobs_result.all()}
    total = sum(status_counts.values())
    pass_rate = (
        status_counts.get("COMPLETED", 0) / total if total > 0 else 0.0
    )

    return ok({
        "plan_run_id": run_id,
        "status": pr.status,
        "total_jobs": total,
        "status_counts": status_counts,
        "pass_rate": round(pass_rate, 4),
        "started_at": _iso(pr.started_at),
        "ended_at": _iso(pr.ended_at),
        "result_summary": pr.result_summary,
    })


# ── Artifacts ────────────────────────────────────────────────────────────

@router.get(
    "/plan-runs/{run_id}/jobs/{job_id}/artifacts",
    response_model=ApiResponse[list],
)
def list_job_artifacts(
    run_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    job = db.get(JobInstance, job_id)
    if job is None or job.plan_run_id != run_id:
        raise HTTPException(status_code=404, detail="job not found in this plan run")

    result = db.execute(
        select(JobArtifact).where(JobArtifact.job_id == job_id)
    )
    artifacts = result.scalars().all()
    return ok([
        {
            "id": a.id,
            "job_id": a.job_id,
            "filename": a.storage_uri.rsplit("/", 1)[-1] if a.storage_uri else None,
            "artifact_type": a.artifact_type,
            "size_bytes": a.size_bytes,
            "checksum": a.checksum,
            "created_at": _iso(a.created_at),
        }
        for a in artifacts
    ])


def _artifact_download_target(storage_uri: str) -> dict[str, str]:
    parsed = urlparse(storage_uri)
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"}:
        return {"kind": "redirect", "url": storage_uri}
    try:
        local_path = resolve_local_artifact_path(storage_uri, must_exist=True)
    except ArtifactPathNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ArtifactPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"kind": "local", "path": str(local_path)}


@router.get(
    "/plan-runs/{run_id}/jobs/{job_id}/artifacts/{artifact_id}/download",
)
def download_job_artifact(
    run_id: int,
    job_id: int,
    artifact_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    job = db.get(JobInstance, job_id)
    if job is None or job.plan_run_id != run_id:
        raise HTTPException(status_code=404, detail="job not found in this plan run")

    artifact = db.get(JobArtifact, artifact_id)
    if artifact is None or artifact.job_id != job_id:
        raise HTTPException(status_code=404, detail="artifact not found for this job")

    target = _artifact_download_target(artifact.storage_uri)
    if target["kind"] == "redirect":
        return RedirectResponse(url=target["url"], status_code=307)
    local_path = Path(target["path"])
    media_type = "application/gzip" if local_path.suffixes[-2:] == [".tar", ".gz"] else None
    return FileResponse(path=str(local_path), filename=local_path.name, media_type=media_type)
