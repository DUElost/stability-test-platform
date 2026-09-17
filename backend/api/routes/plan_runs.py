"""PlanRun API — ADR-0020.

Provides PlanRun list/detail/jobs/summary endpoints.
"""

from __future__ import annotations

import logging
import time
from copy import deepcopy
from typing import Optional

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session, joinedload

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import get_current_active_user, User
from backend.api.schemas.case_result import (
    TestCaseResultOut,
    TestCaseResultSummary,
    TestCaseResultsPayload,
)
from backend.api.schemas.plan_run import (
    JobInstanceOut,
    JobManualActionIn,
    JobManualActionOut,
    PlanChainOut,
    PlanRunAbortIn,
    PlanRunDevicesOut,
    PlanRunEventsOut,
    PlanRunLogEventOut,
    PlanRunLogEventsOut,
    PlanRunDetailOut,
    PlanRunListPageOut,
    PlanRunListStatsOut,
    PlanRunTimelineOut,
    StepTraceOut,
    WatcherSummaryOut,
)
from backend.core.database import get_db
from backend.core.metrics import (
    record_plan_run_devices_query_duration,
)
from backend.models.enums import PlanRunStatus
from backend.models.host import Device
from backend.core.job_timeout_config import (
    PRECHECK_ACTIVE_STALE_SECONDS,
    PRECHECK_QUEUE_STALE_SECONDS,
)
from backend.models.job import JobArtifact, JobInstance, StepTrace
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.project import TestProject
from backend.services.plan_run_timeline import build_plan_run_timeline
from backend.services.plan_run_event_feed import build_plan_run_events
from backend.services.plan_run_chain import build_plan_run_chain
from backend.services.plan_run_chain import (  # noqa: F401
    MAX_CHAIN_DEPTH,
    _chain_node_from_run,
    chain_node_from_run,
)
from backend.services.plan_run_devices import (
    build_plan_run_devices,
)
# 既有测试可能从路由导入 UI 状态派生符号。
from backend.services.plan_run_devices import (  # noqa: F401
    _COORDINATOR_HEARTBEAT_TIMEOUT_SECONDS,
    _WAITING_EXECUTION_STATES,
    _adb_state_excluded,
    _current_stage_for_job,
    _derive_busy_reason,
    _grace_remaining_seconds,
    _job_exec_status_for_job,
    _not_reported_liveness_anchor,
    _pending_claim_deadline,
    _pending_claim_remaining_seconds,
    _running_heartbeat_deadline,
    _ui_status_for_job,
)
from backend.services.plan_run_manual import (
    manual_exit_job_sync,
    manual_retry_job_sync,
)
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
from backend.services.device_log_event import (
    list_plan_run_device_log_event_platforms,
    list_plan_run_device_log_events,
)
from backend.services.case_result_ingest import list_plan_run_test_case_results
from backend.services.job_artifact_download import build_artifact_download_response
from backend.services.plan_run_watcher_summary import (
    build_plan_run_crash_details,
    build_plan_run_watcher_summary,
)
# 既有测试与调用方可能从路由导入这些 watcher/AEE 聚合符号（#2285 去重键契约等）。
from backend.services.plan_run_watcher_summary import (  # noqa: F401
    _CAPABILITY_SEVERITY,
    _DEFAULT_WATCHER_WINDOW_MIN,
    _MAX_WATCHER_WINDOW_MIN,
    _SUBTYPE_FIXED_ORDER,
    _WATCHER_TIME_SCOPE_TO_MINUTES,
    _aee_event_dedup_key,
    _aggregate_aee_breakdown,
    _aggregate_aee_dashboard_sections,
    _aggregate_run_log_archive,
    _aggregate_watcher_capability,
    _aggregate_watcher_platform_buckets,
    _build_dashboard_section,
    _empty_dashboard_section,
    _infer_dashboard_event_group_and_subtype,
    _infer_dashboard_package_name,
    _load_deduped_aee_events,
    _normalize_dashboard_subtype,
    _normalize_entry_origin,
    _prefer_deduped_event,
    _resolve_dashboard_local_aee_dir,
    _resolve_watcher_summary_window,
    _subtype_order_index,
    _uniview_dedup_key,
    _window_minutes_to_scope_label,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["plan-runs"])
# ── Schemas ──────────────────────────────────────────────────────────────


# ── Helpers ──────────────────────────────────────────────────────────────

def _iso(v) -> str | None:
    if v is None:
        return None
    return v.isoformat()


def _project_run_context(pr: PlanRun) -> Optional[dict]:
    if not isinstance(pr.run_context, dict):
        return pr.run_context
    context = deepcopy(pr.run_context)
    state = context.get("dispatch_state")
    if not isinstance(state, dict):
        return context

    status = str(state.get("status") or "")
    anchor_raw = (
        state.get("started_at")
        if status == "running"
        else state.get("enqueued_at")
    )
    timeout_seconds = (
        PRECHECK_ACTIVE_STALE_SECONDS
        if status == "running"
        else PRECHECK_QUEUE_STALE_SECONDS
    )
    if status in {"queued", "running"} and anchor_raw:
        try:
            anchor = datetime.fromisoformat(
                str(anchor_raw).replace("Z", "+00:00")
            )
            deadline = _aware(anchor) + timedelta(seconds=timeout_seconds)
            state["deadline_at"] = deadline.isoformat()
            state["stale"] = datetime.now(timezone.utc) >= deadline
        except (TypeError, ValueError):
            state["deadline_at"] = None
            state["stale"] = False
    else:
        state["stale"] = False
    summary = pr.result_summary if isinstance(pr.result_summary, dict) else {}
    state["retryable"] = (
        pr.status == PlanRunStatus.FAILED.value
        and bool(summary.get("precheck_failed") or summary.get("dispatch_failed"))
    )
    return context


def _plan_run_capabilities(pr: PlanRun) -> dict:
    summary = pr.result_summary if isinstance(pr.result_summary, dict) else {}
    terminal = pr.status in {
        PlanRunStatus.SUCCESS.value,
        PlanRunStatus.PARTIAL_SUCCESS.value,
        PlanRunStatus.FAILED.value,
    }
    return {
        "abort": pr.status in (
            PlanRunStatus.RUNNING.value,
            # ADR-0026: QUEUED/PRECHECK abort → straight FAILED (no jobs)
            PlanRunStatus.QUEUED.value,
            PlanRunStatus.PRECHECK.value,
        ),
        "retry_dispatch": (
            pr.status == PlanRunStatus.FAILED.value
            and bool(
                summary.get("precheck_failed")
                or summary.get("dispatch_failed")
            )
        ),
        "final_archive": terminal,
    }


def _plan_run_out(
    pr: PlanRun,
    jobs: list[JobInstanceOut] | None = None,
    plan_name: str | None = None,
    device_count: int | None = None,
) -> PlanRunDetailOut:
    return PlanRunDetailOut(
        id=pr.id,
        plan_id=pr.plan_id,
        status=pr.status,
        failure_threshold=pr.failure_threshold,
        run_type=pr.run_type,
        triggered_by=pr.triggered_by,
        started_at=_iso(pr.started_at) or "",
        ended_at=_iso(pr.ended_at),
        result_summary=pr.result_summary,
        run_context=_project_run_context(pr),
        plan_snapshot=pr.plan_snapshot,
        parent_plan_run_id=pr.parent_plan_run_id,
        root_plan_run_id=pr.root_plan_run_id,
        chain_index=pr.chain_index or 0,
        next_plan_triggered=bool(pr.next_plan_triggered),
        plan_name=plan_name,
        project_key=pr.project.project_key if pr.project else None,
        capabilities=_plan_run_capabilities(pr),
        jobs=jobs or [],
        # Distinct devices, not JobInstance row count (#747). List and detail
        # share this fallback so multi-job-per-device runs do not inflate.
        device_count=(
            device_count
            if device_count is not None
            else len({j.device_id for j in (jobs or [])})
        ),
        queue_reason=pr.queue_reason,
        enqueued_at=_iso(pr.enqueued_at),
        next_admission_at=_iso(pr.next_admission_at),
        priority=pr.priority or 0,
    )


def _step_out(t: StepTrace) -> StepTraceOut:
    return StepTraceOut(
        id=t.id, job_id=t.job_id, step_id=t.step_id, stage=t.stage,
        event_type=t.event_type, status=t.status, output=t.output,
        error_message=t.error_message, exit_code=t.exit_code,
        metadata=t.step_metadata,
        original_ts=_iso(t.original_ts) or "",
        created_at=_iso(t.created_at) or "",
    )


def _job_out(job: JobInstance, traces: list, device_serial: str | None = None) -> JobInstanceOut:
    return JobInstanceOut(
        id=job.id, plan_run_id=job.plan_run_id, plan_id=job.plan_id,
        device_id=job.device_id, device_serial=device_serial,
        host_id=job.host_id, status=job.status,
        status_reason=job.status_reason,
        execution_state=job.execution_state,
        last_execution_heartbeat_at=_iso(job.last_execution_heartbeat_at),
        last_progress_at=_iso(job.last_progress_at),
        started_at=_iso(job.started_at),
        ended_at=_iso(job.ended_at),
        created_at=_iso(job.created_at),
        step_traces=[_step_out(t) for t in traces],
    )


# ── Endpoints ────────────────────────────────────────────────────────────

def _apply_plan_run_list_filters(
    stmt,
    *,
    plan_id: Optional[int],
    statuses: Optional[list[PlanRunStatus]],
    project_key: Optional[str],
    q: Optional[str],
    db: Session,
    join_plan_for_search: bool = False,
):
    """共享 list / count / stats 的过滤条件。project_key 未知 → 404。"""
    if plan_id is not None:
        stmt = stmt.where(PlanRun.plan_id == plan_id)
    if statuses:
        values = [s.value for s in statuses]
        if len(values) == 1:
            stmt = stmt.where(PlanRun.status == values[0])
        else:
            stmt = stmt.where(PlanRun.status.in_(values))
    if project_key is not None:
        if db.query(TestProject).filter(TestProject.project_key == project_key).first() is None:
            raise HTTPException(status_code=404, detail="project not found")
        stmt = stmt.join(TestProject, PlanRun.project_id == TestProject.id).where(
            TestProject.project_key == project_key
        )
    needle = (q or "").strip()
    if needle:
        pattern = f"%{needle}%"
        clauses = [
            PlanRun.triggered_by.ilike(pattern),
            cast(PlanRun.id, String).ilike(pattern),
        ]
        if join_plan_for_search:
            stmt = stmt.join(Plan, PlanRun.plan_id == Plan.id)
        clauses.append(Plan.name.ilike(pattern))
        stmt = stmt.where(or_(*clauses))
    return stmt


@router.get("/plan-runs", response_model=ApiResponse[PlanRunListPageOut])
def list_plan_runs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    plan_id: Optional[int] = None,
    status: Optional[list[PlanRunStatus]] = Query(
        default=None,
        description="可重复：?status=QUEUED&status=PRECHECK",
    ),
    project_key: Optional[str] = Query(None, description="ADR-0029: filter by project key"),
    q: Optional[str] = Query(None, description="搜索 Run ID / Plan 名 / 触发者"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    needle = (q or "").strip()
    need_plan_join = bool(needle)

    base = select(PlanRun).options(joinedload(PlanRun.project))
    base = _apply_plan_run_list_filters(
        base,
        plan_id=plan_id,
        statuses=status,
        project_key=project_key,
        q=q,
        db=db,
        join_plan_for_search=need_plan_join,
    )
    base = base.order_by(PlanRun.started_at.desc())

    count_stmt = select(func.count()).select_from(PlanRun)
    count_stmt = _apply_plan_run_list_filters(
        count_stmt,
        plan_id=plan_id,
        statuses=status,
        project_key=project_key,
        q=q,
        db=db,
        join_plan_for_search=need_plan_join,
    )
    total = int(db.execute(count_stmt).scalar_one())

    stats_stmt = select(PlanRun.status, func.count()).group_by(PlanRun.status)
    stats_stmt = _apply_plan_run_list_filters(
        stats_stmt,
        plan_id=plan_id,
        statuses=None,
        project_key=project_key,
        q=None,
        db=db,
        join_plan_for_search=False,
    )
    by_status = {row[0]: int(row[1]) for row in db.execute(stats_stmt).all()}
    stats = PlanRunListStatsOut(
        total=sum(by_status.values()),
        running=by_status.get(PlanRunStatus.RUNNING.value, 0),
        failed=by_status.get(PlanRunStatus.FAILED.value, 0),
    )

    runs = db.execute(base.offset(skip).limit(limit)).scalars().unique().all()
    plan_ids = {r.plan_id for r in runs}
    plan_names: dict[int, str] = {}
    if plan_ids:
        plan_rows = db.execute(
            select(Plan.id, Plan.name).where(Plan.id.in_(plan_ids))
        ).all()
        plan_names = {row.id: row.name for row in plan_rows}
    run_ids = [r.id for r in runs]
    device_counts: dict[int, int] = {}
    if run_ids:
        # Align with watcher-summary / UI「设备」列: distinct device_id (#747).
        count_rows = db.execute(
            select(
                JobInstance.plan_run_id,
                func.count(func.distinct(JobInstance.device_id)),
            )
            .where(JobInstance.plan_run_id.in_(run_ids))
            .group_by(JobInstance.plan_run_id)
        ).all()
        device_counts = {int(rid): int(cnt) for rid, cnt in count_rows}
    items = [
        _plan_run_out(
            r,
            plan_name=plan_names.get(r.plan_id),
            device_count=device_counts.get(r.id, 0),
        )
        for r in runs
    ]
    return ok(
        PlanRunListPageOut(
            items=items,
            total=total,
            skip=skip,
            limit=limit,
            stats=stats,
        )
    )


@router.get("/plan-runs/{run_id}", response_model=ApiResponse[PlanRunDetailOut])
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
    plan_name: str | None = None
    if pr.plan_id is not None:
        plan_row = db.execute(
            select(Plan.name).where(Plan.id == pr.plan_id)
        ).scalar_one_or_none()
        plan_name = plan_row
    return ok(_plan_run_out(pr, jobs=[_job_out(j, []) for j in jobs], plan_name=plan_name))


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

    Returns 409 if the PlanRun is already terminal. PENDING jobs become
    ABORTED immediately; RUNNING jobs retain their lease until the Agent kills
    the process tree and ACKs ABORTED through the completion endpoint.
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
            raise HTTPException(status_code=404, detail=msg) from exc
        raise HTTPException(status_code=409, detail=msg) from exc
    return ok(summary)


# ── ADR-0025 S2: 手动归档(立即触发已终态 Job 的运行日志归档) ─────────────────

@router.post(
    "/plan-runs/{run_id}/archive",
    response_model=ApiResponse[dict],
)
async def archive_plan_run_logs_endpoint(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """ADR-0025 S2: 手动触发该 PlanRun 涉及 host 的运行日志立即归档 + scan。

    经 SocketIO control 向各 ONLINE host 的 Agent 下发:
      - archive_now: Agent ``scan_once(grace_seconds=0)``；实际仍受 ``MIN_GRACE_SECONDS``
        （300s）下限约束，并跳过 active Job，防止刚启动 Job 被误 prune。
      - scan_now: 向本 PlanRun 涉及的全部 host 下发同一份设备 serial 列表
        （可跨主机），Agent 只扫本地 HDD 上命中的 `{folder}/{serial}/`。
    归档和 scan 均为异步——返回「已触发」，前端应轮询/refetch。

    ADR-0038 D5：回收类允许触达退役主机，但**仅显式 admin 触发**（admin 会话
    allow_retired=True）+ 审计 + `skipped_retired` 如实报告（不虚报完整）。
    """
    from backend.core.audit import record_audit
    from backend.realtime.socketio_server import emit_agent_control
    from backend.services.plan_run_scan_scope import (
        build_scan_now_payload,
        classify_recycle_targets,
        iter_plan_run_scan_hosts,
    )

    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="plan run not found")

    has_jobs = (
        db.query(JobInstance.id)
        .filter(JobInstance.plan_run_id == run_id)
        .first()
    )
    if not has_jobs:
        raise HTTPException(status_code=400, detail="no jobs found for this plan run")

    allow_retired = current_user.role == "admin"
    host_rows = iter_plan_run_scan_hosts(db, run_id)
    if not host_rows:
        raise HTTPException(status_code=400, detail="no jobs found for this plan run")

    targets, skipped_offline, skipped_retired = classify_recycle_targets(
        host_rows, allow_retired=allow_retired,
    )

    triggered: list[str] = []
    for host_id in targets:
        await emit_agent_control(
            host_id, "archive_now",
            payload={"plan_run_id": run_id},
        )
        await emit_agent_control(
            host_id, "scan_now",
            payload=build_scan_now_payload(db, run_id, host_id, is_final=False),
        )
        triggered.append(host_id)

    record_audit(
        db,
        action="plan_run_archive_scan_trigger",
        resource_type="plan_run",
        resource_id=str(run_id),
        details={
            "triggered_hosts": triggered,
            "skipped_offline": skipped_offline,
            "skipped_retired": skipped_retired,
            "allow_retired": allow_retired,
        },
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()

    return ok({
        "plan_run_id": run_id,
        "archived_now": True,
        "triggered_hosts": triggered,
        "skipped_offline": skipped_offline,
        "skipped_retired": skipped_retired,
    })


@router.post(
    "/plan-runs/{run_id}/retry-dispatch",
    response_model=ApiResponse[dict],
)
def retry_plan_run_dispatch_endpoint(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return a failed precheck / dispatch run to the admission queue."""
    try:
        summary = retry_plan_run_dispatch(
            run_id,
            db=db,
            triggered_by=current_user.username if current_user else "api",
            audit_user_id=current_user.id if current_user else None,
        )
    except PlanRunDispatchRetryError as exc:
        msg = str(exc)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg) from exc
        if "queue unavailable" in msg:
            raise HTTPException(status_code=503, detail=msg) from exc
        raise HTTPException(status_code=409, detail=msg) from exc
    return ok(summary)


# ── ADR-0022: Manual retry / exit for patrol-backoff jobs ───────────────────


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

    #1520 切片：业务逻辑（校验/迁移/审计/emit/提交）在
    ``services/plan_run_manual.py``；本端点只做解析 → 调服务 → 序列化。
    """
    job = manual_retry_job_sync(
        db, run_id, job_id,
        reason=(payload.reason if payload else None),
        actor_id=current_user.id if current_user else None,
        actor_username=current_user.username if current_user else None,
    )
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

    #1520 切片：业务逻辑在 ``services/plan_run_manual.py``；本端点只做
    解析 → 调服务 → 序列化。
    """
    job = manual_exit_job_sync(
        db, run_id, job_id,
        reason=(payload.reason if payload else None),
        actor_id=current_user.id if current_user else None,
        actor_username=current_user.username if current_user else None,
    )
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
#       FAILED                              → failed
#       ABORTED                             → aborted
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

_MAX_EVENTS_LIMIT             = 500
_DEFAULT_EVENTS_LIMIT         = 100



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


@router.get("/plan-runs/{run_id}/chain", response_model=ApiResponse[PlanChainOut])
def get_plan_run_chain(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0020 §6: PlanRun chain 上下文 — 沿 parent_plan_run_id 回溯到 root,
    再沿 next_plan_id 展开到链尾（2026-09-01 起展示完整未触发链）。

    返回的 nodes 列表按 chain_index 升序,包含:
      - 0..N 个 parent PlanRun (已触发)
      - 1 个 current PlanRun(is_current=True)
      - 0..N 个未触发的 next Plan 节点(plan_run_id=None, status='pending')——
        沿 next_plan_id 每个未触发链节一个占位节点;仅链首节点承载
        is_blocked/block_reason,后续节点固定 block_reason="等待前序 Plan 触发"
    """
    pr = _require_plan_run(db, run_id)
    return ok(build_plan_run_chain(db, pr))


# ── Endpoint 2: GET /plan-runs/{id}/timeline ─────────────────────────────

@router.get("/plan-runs/{run_id}/timeline", response_model=ApiResponse[PlanRunTimelineOut])
def get_plan_run_timeline(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0021/ADR-0022 C5a₂: 业务流时间线 — 按 stage 聚合 step_trace。

    业务聚合见 ``backend.services.plan_run_timeline.build_plan_run_timeline``。
    """
    pr = _require_plan_run(db, run_id)
    return ok(build_plan_run_timeline(db, pr))


# ── Endpoint 3: GET /plan-runs/{id}/events ───────────────────────────────


@router.get("/plan-runs/{run_id}/events", response_model=ApiResponse[PlanRunEventsOut])
def get_plan_run_events(
    run_id: int,
    stage: Optional[str] = Query(None, description="init / patrol / teardown / system / trigger / all"),
    severity: Optional[str] = Query(None, description="ok / info / warn / err / all"),
    search: Optional[str] = Query(None, description="关键字,大小写不敏感,匹配 title / description / device_serial"),
    limit: int = Query(_DEFAULT_EVENTS_LIMIT, ge=1, le=_MAX_EVENTS_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0021/ADR-0022 C5a₂: 业务流事件流。

    聚合见 ``backend.services.plan_run_event_feed.build_plan_run_events``。
    """
    pr = _require_plan_run(db, run_id)
    return ok(build_plan_run_events(
        db,
        pr,
        stage=stage,
        severity=severity,
        search=search,
        limit=limit,
        offset=offset,
    ))


# ── Endpoint 4: GET /plan-runs/{id}/devices ──────────────────────────────


@router.get("/plan-runs/{run_id}/devices", response_model=ApiResponse[PlanRunDevicesOut])
def get_plan_run_devices(
    run_id: int,
    status: Optional[str] = Query(None, description="job_exec_status 过滤(可选)"),
    link_status: Optional[str] = Query(None, description="device_link_status 过滤(可选)"),
    host_id: Optional[str] = Query(None, description="host_id 过滤(可选)"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0021/ADR-0022 C5a₂: 设备执行矩阵 — 每台设备一行,
    包含 patrol 心跳聚合、退避状态、watcher 异常计数。

    「连接」与「执行」是两个正交维度,各有一组 facet:
    `by_status` 基于 `job_exec_status`(纯 Job 投影,不掺设备连接),
    `by_link_status` 基于 `device_link_status`(ADB / Host 可达性)。
    两组 facet 与 `by_host` 一样始终基于"未过滤"全集计算,
    便于前端筛选 chip 同时显示总数和当前数。
    """
    t0 = time.perf_counter()
    try:
        _require_plan_run(db, run_id)
        return ok(build_plan_run_devices(
            db,
            run_id,
            status=status,
            link_status=link_status,
            host_id=host_id,
        ))
    finally:
        record_plan_run_devices_query_duration(time.perf_counter() - t0)


# ── Endpoint 5: GET /plan-runs/{id}/watcher-summary ──────────────────────


@router.get(
    "/plan-runs/{run_id}/watcher-summary",
    response_model=ApiResponse[WatcherSummaryOut],
)
def get_plan_run_watcher_summary(
    run_id: int,
    window_minutes: Optional[int] = Query(None, ge=1, le=_MAX_WATCHER_WINDOW_MIN),
    time_scope: Optional[str] = Query(None, pattern="^(all|15m|1h|6h|24h)$"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """#1520 薄壳：聚合逻辑在 `services/plan_run_watcher_summary`。"""
    pr = _require_plan_run(db, run_id)
    return ok(build_plan_run_watcher_summary(
        db, pr, window_minutes=window_minutes, time_scope=time_scope,
    ))


@router.get(
    "/plan-runs/{run_id}/log-events",
    response_model=ApiResponse[PlanRunLogEventsOut],
)
def get_plan_run_log_events(
    run_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    state: Optional[str] = Query(None, description="Filter by DLE state"),
    platform: Optional[str] = Query(None, description="Filter by device platform"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0028 / #529: PlanRun device log events — archive authority.

    Paths and upload state come from ``device_log_event`` (not watcher-summary
  signals). RUNNING PlanRuns may return a partial list; terminal runs are the
    primary consumer.
    """
    _require_plan_run(db, run_id)
    rows, total = list_plan_run_device_log_events(
        db,
        run_id,
        skip=skip,
        limit=limit,
        state=state,
        platform=platform,
    )
    items = [
        PlanRunLogEventOut(
            id=str(row.id),
            serial=row.serial,
            platform=row.platform,
            event_type=row.event_type,
            event_subtype=row.event_subtype,
            state=row.state,
            local_path=row.local_path,
            remote_path=row.remote_path,
            detected_at=_iso(row.detected_at) or "",
            device_timestamp=_iso(row.device_timestamp),
            job_id=row.job_id,
            host_id=row.host_id,
            signal_seq_no=row.signal_seq_no,
        )
        for row in rows
    ]
    return ok(PlanRunLogEventsOut(
        plan_run_id=run_id,
        total=total,
        items=items,
        # #2288：平台全集单独取——不受本次 `platform`/`limit` 影响，前端筛选选项据此渲染。
        platforms=list_plan_run_device_log_event_platforms(db, run_id, state=state),
    ))


@router.get(
    "/plan-runs/{run_id}/test-case-results",
    response_model=ApiResponse[TestCaseResultsPayload],
)
def get_plan_run_test_case_results(
    run_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    status: Optional[str] = Query(None, description="PASS / FAILURE / ERROR"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0030 P2: PlanRun 逐条用例结果（test_case_result 表）。"""
    _require_plan_run(db, run_id)
    rows, total, summary_counts = list_plan_run_test_case_results(
        db, run_id, status=status, skip=skip, limit=limit,
    )
    job_ids = {row.job_id for row in rows}
    jobs_by_id: dict[int, JobInstance] = {}
    if job_ids:
        jobs_by_id = {
            j.id: j
            for j in db.query(JobInstance).filter(JobInstance.id.in_(job_ids)).all()
        }
    items = []
    for row in rows:
        job = jobs_by_id.get(row.job_id)
        items.append(TestCaseResultOut(
            id=row.id,
            plan_run_id=row.plan_run_id,
            job_id=row.job_id,
            suite_id=row.suite_id,
            case_id=row.case_id,
            case_name=row.case_name,
            status=row.status,
            detail=row.detail,
            artifact_uri=row.artifact_uri,
            run_dir=row.run_dir,
            created_at=row.created_at,
            device_id=job.device_id if job else None,
            host_id=str(job.host_id) if job and job.host_id is not None else None,
        ))
    return ok(TestCaseResultsPayload(
        items=items,
        total=total,
        summary=TestCaseResultSummary(**summary_counts),
    ))


# M0/C-6: watcher_capability 降级严重度排序 — 取该 PlanRun 下 Job 中"最降级"的一档,
# 使得只要有任一设备落到 reconciler 单通道(unavailable)即可在前端给出提示。
# 数值越大越降级;未知能力按 0 处理(不触发降级徽章)。
# ── ADR-0025 Sprint 3: crash 详情端点（按事件目录组织）──────────────────────

@router.get(
    "/plan-runs/{run_id}/crash-details",
    response_model=ApiResponse[list],
)
def get_plan_run_crash_details(
    run_id: int,
    package_name: Optional[str] = Query(None, description="按包名过滤"),
    time_scope: Optional[str] = Query(None, pattern="^(all|15m|1h|6h|24h)$"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """#1520 薄壳：见 `services/plan_run_watcher_summary.build_plan_run_crash_details`。"""
    pr = _require_plan_run(db, run_id)
    return ok(build_plan_run_crash_details(
        db, pr, package_name=package_name, time_scope=time_scope,
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
    """产物下载（**UI 权威入口**）：唯一带 run↔job 配对校验的一条。

    #2420 第 4 项：实现与 job 域脚本入口
    ``GET /runs/{job_id}/artifacts/{artifact_id}/download`` 共用
    ``backend/services/job_artifact_download.py``，守卫与判据两侧同形。
    """
    return build_artifact_download_response(
        db, job_id=job_id, artifact_id=artifact_id, plan_run_id=run_id,
    )
