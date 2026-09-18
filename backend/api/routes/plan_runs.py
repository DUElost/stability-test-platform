"""PlanRun API — ADR-0020（#1520：路由薄壳，业务在 ``backend.services.plan_run_*``）。"""

from __future__ import annotations

import logging
import time
from typing import NoReturn, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import get_current_active_user, User
from backend.api.schemas.case_result import TestCaseResultsPayload
from backend.api.schemas.plan_run import (
    JobInstanceOut,
    JobManualActionIn,
    JobManualActionOut,
    PlanChainOut,
    PlanRunAbortIn,
    PlanRunAbortSummaryOut,
    PlanRunArchiveTriggerOut,
    PlanRunDetailOut,
    PlanRunDevicesOut,
    PlanRunDispatchRetrySummaryOut,
    PlanRunEventsOut,
    PlanRunJobArtifactOut,
    PlanRunJobsSummaryOut,
    PlanRunListPageOut,
    PlanRunLogEventsOut,
    PlanRunTimelineOut,
    WatcherSummaryOut,
)
from backend.core.database import get_db
from backend.core.metrics import record_plan_run_devices_query_duration
from backend.models.enums import PlanRunStatus
from backend.models.job import JobInstance
from backend.services.job_artifact_download import build_artifact_download_response
from backend.services.plan_precheck import (
    PlanRunDispatchRetryError,
    retry_plan_run_dispatch,
)
from backend.services.plan_run_abort import PlanRunAbortError, abort_plan_run
from backend.services.plan_run_archive import archive_plan_run_logs
from backend.services.plan_run_catalog import (
    build_plan_run_detail,
    build_plan_run_jobs,
    build_plan_run_list_page,
)
from backend.services.plan_run_chain import build_plan_run_chain
from backend.services.plan_run_devices import build_plan_run_devices
from backend.services.plan_run_event_feed import build_plan_run_events
from backend.services.plan_run_export import (
    build_plan_run_export,
    plan_run_export_to_markdown,
)
from backend.services.plan_run_job_artifacts import list_plan_run_job_artifacts
from backend.services.plan_run_manual import manual_exit_job_sync, manual_retry_job_sync
from backend.services.plan_run_read_common import iso as _iso
from backend.services.plan_run_read_common import require_plan_run as _require_plan_run
from backend.services.plan_run_result_views import (
    build_plan_run_log_events,
    build_plan_run_test_case_results,
)
from backend.services.plan_run_summary import build_plan_run_summary
from backend.services.plan_run_timeline import build_plan_run_timeline
from backend.services.plan_run_watcher_summary import (
    _MAX_WATCHER_WINDOW_MIN,
    build_plan_run_crash_details,
    build_plan_run_watcher_summary,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["plan-runs"])

_MAX_EVENTS_LIMIT = 500
_DEFAULT_EVENTS_LIMIT = 100


def _manual_action_out(
    job: JobInstance, *, run_id: int, job_id: int, action: str,
) -> JobManualActionOut:
    return JobManualActionOut(
        job_id=job_id,
        plan_run_id=run_id,
        action=action,
        status=job.status,
        manual_action=job.manual_action,
        next_retry_at=_iso(job.next_retry_at),
        current_failure_streak=job.current_failure_streak or 0,
    )


def _raise_abort_http(exc: PlanRunAbortError) -> NoReturn:
    msg = str(exc)
    code = 404 if "not found" in msg else 409
    raise HTTPException(status_code=code, detail=msg) from exc


def _raise_dispatch_retry_http(exc: PlanRunDispatchRetryError) -> NoReturn:
    msg = str(exc)
    if "not found" in msg:
        raise HTTPException(status_code=404, detail=msg) from exc
    if "queue unavailable" in msg:
        raise HTTPException(status_code=503, detail=msg) from exc
    raise HTTPException(status_code=409, detail=msg) from exc


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
    """薄壳：``plan_run_catalog.build_plan_run_list_page``。"""
    return ok(build_plan_run_list_page(
        db,
        skip=skip,
        limit=limit,
        plan_id=plan_id,
        statuses=status,
        project_key=project_key,
        q=q,
    ))


@router.get("/plan-runs/{run_id}", response_model=ApiResponse[PlanRunDetailOut])
def get_plan_run(
    run_id: int,
    include_jobs: bool = True,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """薄壳：``plan_run_catalog.build_plan_run_detail``。

    #2623：`include_jobs=false` 时不查也不序列化内嵌 jobs（实测占响应 98.9%）；
    默认 True 保持既有契约不变（仓外调用方不受影响）。
    """
    return ok(build_plan_run_detail(db, run_id, include_jobs=include_jobs))


@router.get("/plan-runs/{run_id}/jobs", response_model=ApiResponse[list[JobInstanceOut]])
def list_plan_run_jobs(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """薄壳：``plan_run_catalog.build_plan_run_jobs``。"""
    return ok(build_plan_run_jobs(db, run_id))


@router.post(
    "/plan-runs/{run_id}/abort",
    response_model=ApiResponse[PlanRunAbortSummaryOut],
)
def abort_plan_run_endpoint(
    run_id: int,
    payload: Optional[PlanRunAbortIn] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """ADR-0021 D7 — abort；终态 409，未知 404。"""
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
        _raise_abort_http(exc)
    return ok(summary)


@router.post(
    "/plan-runs/{run_id}/archive",
    response_model=ApiResponse[PlanRunArchiveTriggerOut],
)
async def archive_plan_run_logs_endpoint(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """ADR-0025 S2 / ADR-0038 D5：手动归档+scan；admin 可触达退役机。"""
    return ok(await archive_plan_run_logs(
        db,
        run_id,
        allow_retired=current_user.role == "admin",
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    ))


@router.post(
    "/plan-runs/{run_id}/retry-dispatch",
    response_model=ApiResponse[PlanRunDispatchRetrySummaryOut],
)
def retry_plan_run_dispatch_endpoint(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """失败 precheck/dispatch 重回准入队列。"""
    try:
        summary = retry_plan_run_dispatch(
            run_id,
            db=db,
            triggered_by=current_user.username if current_user else "api",
            audit_user_id=current_user.id if current_user else None,
        )
    except PlanRunDispatchRetryError as exc:
        _raise_dispatch_retry_http(exc)
    return ok(summary)


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
    """ADR-0022 D7：清 backoff，逼下一轮 patrol 立刻跑。"""
    job = manual_retry_job_sync(
        db, run_id, job_id,
        reason=(payload.reason if payload else None),
        actor_id=current_user.id if current_user else None,
        actor_username=current_user.username if current_user else None,
    )
    return ok(_manual_action_out(job, run_id=run_id, job_id=job_id, action="manual_retry"))


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
    """ADR-0022 D7：请求 Agent 跳过剩余 patrol 并 abort。"""
    job = manual_exit_job_sync(
        db, run_id, job_id,
        reason=(payload.reason if payload else None),
        actor_id=current_user.id if current_user else None,
        actor_username=current_user.username if current_user else None,
    )
    return ok(_manual_action_out(job, run_id=run_id, job_id=job_id, action="manual_exit"))


@router.get("/plan-runs/{run_id}/chain", response_model=ApiResponse[PlanChainOut])
def get_plan_run_chain(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0020 §6：PlanRun 链上下文（``plan_run_chain``）。"""
    return ok(build_plan_run_chain(db, _require_plan_run(db, run_id)))


@router.get("/plan-runs/{run_id}/timeline", response_model=ApiResponse[PlanRunTimelineOut])
def get_plan_run_timeline(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """C5a₂ 时间线（``plan_run_timeline``）。"""
    return ok(build_plan_run_timeline(db, _require_plan_run(db, run_id)))


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
    """C5a₂ 事件流（``plan_run_event_feed``）。"""
    return ok(build_plan_run_events(
        db,
        _require_plan_run(db, run_id),
        stage=stage,
        severity=severity,
        search=search,
        limit=limit,
        offset=offset,
    ))


@router.get("/plan-runs/{run_id}/devices", response_model=ApiResponse[PlanRunDevicesOut])
def get_plan_run_devices(
    run_id: int,
    status: Optional[str] = Query(None, description="job_exec_status 过滤(可选)"),
    link_status: Optional[str] = Query(None, description="device_link_status 过滤(可选)"),
    host_id: Optional[str] = Query(None, description="host_id 过滤(可选)"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """C5a₂ 设备矩阵（``plan_run_devices``）；facet 基于未过滤全集。"""
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
    """薄壳：``plan_run_watcher_summary``。"""
    return ok(build_plan_run_watcher_summary(
        db,
        _require_plan_run(db, run_id),
        window_minutes=window_minutes,
        time_scope=time_scope,
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
    """ADR-0028 / #529：DLE 归档权威列表。"""
    _require_plan_run(db, run_id)
    return ok(build_plan_run_log_events(
        db, run_id, skip=skip, limit=limit, state=state, platform=platform,
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
    """ADR-0030 P2：逐条用例结果。"""
    _require_plan_run(db, run_id)
    return ok(build_plan_run_test_case_results(
        db, run_id, skip=skip, limit=limit, status=status,
    ))


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
    """薄壳：``build_plan_run_crash_details``。"""
    return ok(build_plan_run_crash_details(
        db,
        _require_plan_run(db, run_id),
        package_name=package_name,
        time_scope=time_scope,
    ))


@router.get("/plan-runs/{run_id}/report/export")
def export_plan_run_report(
    run_id: int,
    format: str = Query("markdown"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """导出 summary+devices+timeline（有界，防 OOM）。"""
    data = build_plan_run_export(db, _require_plan_run(db, run_id))
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
    return PlainTextResponse(
        plan_run_export_to_markdown(data),
        headers={
            "Content-Disposition": (
                f'attachment; filename="plan-run-{run_id}-report.md"'
            ),
        },
    )


@router.get(
    "/plan-runs/{run_id}/summary",
    response_model=ApiResponse[PlanRunJobsSummaryOut],
)
def get_plan_run_summary(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    return ok(build_plan_run_summary(db, run_id))


@router.get(
    "/plan-runs/{run_id}/jobs/{job_id}/artifacts",
    response_model=ApiResponse[list[PlanRunJobArtifactOut]],
)
def list_job_artifacts(
    run_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    return ok(list_plan_run_job_artifacts(db, run_id, job_id))


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
    """UI 权威下载入口（#2420）：与 job 域共用 ``job_artifact_download``。"""
    return build_artifact_download_response(
        db, job_id=job_id, artifact_id=artifact_id, plan_run_id=run_id,
    )
