"""PlanRun 设备双视图分离与单 Job 详情路由（#83 / #4117 阶段 B2-3）。

从 ``plan_runs.py`` 独立成同级薄路由模块，避免突破 ``plan_runs.py`` 的文件行数上限；
所有业务逻辑位于 ``backend.services.plan_run_devices`` 与 ``backend.services.plan_run_job_detail``。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import User, get_current_active_user
from backend.api.schemas.plan_run import (
    PlanRunDevicesMinimapOut,
    PlanRunJobDetailOut,
    PlanRunJobTimelineOut,
    PlanRunJobWatcherSignalsOut,
)
from backend.core.database import get_db
from backend.services.errors import NotFound
from backend.services.plan_run_devices import build_plan_run_devices_minimap
from backend.services.plan_run_job_detail import (
    build_plan_run_job_detail,
    build_plan_run_job_timeline,
    build_plan_run_job_watcher_signals,
)
from backend.services.plan_run_read_common import require_plan_run

router = APIRouter(prefix="/api/v1", tags=["plan-runs"])


@router.get(
    "/plan-runs/{run_id}/devices/minimap",
    response_model=ApiResponse[PlanRunDevicesMinimapOut],
)
def get_plan_run_devices_minimap(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """#83 / #4117 B2-3：独立轻量全集 Minimap 端点（严格 7 字段 DTO + 全局 facets/hosts/stuck）。"""
    require_plan_run(db, run_id)
    return ok(build_plan_run_devices_minimap(db, run_id))


@router.get(
    "/plan-runs/{run_id}/jobs/{job_id}",
    response_model=ApiResponse[PlanRunJobDetailOut],
)
def get_plan_run_job_detail(
    run_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """#83 / #4117 B2-3：单 Job 完整详情（严格校验 (run_id, job_id) 归属，错配返回 404）。"""
    try:
        return ok(build_plan_run_job_detail(db, run_id, job_id))
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.get(
    "/plan-runs/{run_id}/jobs/{job_id}/timeline",
    response_model=ApiResponse[PlanRunJobTimelineOut],
)
def get_plan_run_job_timeline(
    run_id: int,
    job_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """#83 / #4117 B2-3：按 Job 延迟加载有上限阶段时间线（不含完整 output 日志）。"""
    try:
        return ok(build_plan_run_job_timeline(db, run_id, job_id, skip=skip, limit=limit))
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.get(
    "/plan-runs/{run_id}/jobs/{job_id}/watcher-signals",
    response_model=ApiResponse[PlanRunJobWatcherSignalsOut],
)
def get_plan_run_job_watcher_signals(
    run_id: int,
    job_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    category: Optional[str] = Query(None, max_length=32),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """#83 / #4117 B2-3：按 Job 延迟加载有上限 Watcher 异常明细。"""
    try:
        return ok(
            build_plan_run_job_watcher_signals(
                db, run_id, job_id, skip=skip, limit=limit, category=category,
            )
        )
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
