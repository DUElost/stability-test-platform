"""ADR-0029 P2 — 项目登记簿 API。

- ``GET /api/v1/projects`` — 项目列表（卡片数据：facet 列 + 设备数 / 在跑
  Run 数聚合）。facet 筛选由前端对全量列表做——6 项目规模无需服务端筛选。
- ``GET /api/v1/projects/{project_key}`` — 项目详情聚合（计数 + 最近 Run）；
  设备 / Plan / Run 明细由前端带 ``project_key`` 调既有列表接口。

口径（F2 / D9 挂起）：对外一律 ``project_key``（URL / 日志 / 审计可读），
数字 id 只留 DB 外键。``project_key`` 一经对外使用即不可变（D2）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import User, get_current_active_user
from backend.api.schemas.project import (
    ProjectDetailOut,
    ProjectSummaryOut,
    RecentProjectRunOut,
)
from backend.core.database import get_db
from backend.models.host import Device
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.test_project import TestProject

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

# 「在跑」= RUNNING / QUEUED / PRECHECK（终态除外；DEGRADED 仅历史可读，不计）
_ACTIVE_RUN_STATUSES = ("RUNNING", "QUEUED", "PRECHECK")


def _summary_rows(db: Session) -> dict[int, tuple[int, int]]:
    """所有项目的 (device_count, running_run_count) 聚合（避免逐项目 N 次查询）。"""
    projects = db.query(TestProject.id).all()
    pids = [pid for (pid,) in projects]
    if not pids:
        return {}
    device_counts = dict(
        db.query(Device.project_id, func.count(Device.id))
        .filter(Device.project_id.in_(pids))
        .group_by(Device.project_id)
        .all()
    )
    run_counts = dict(
        db.query(PlanRun.project_id, func.count(PlanRun.id))
        .filter(PlanRun.project_id.in_(pids), PlanRun.status.in_(_ACTIVE_RUN_STATUSES))
        .group_by(PlanRun.project_id)
        .all()
    )
    return {
        pid: (device_counts.get(pid, 0), run_counts.get(pid, 0))
        for pid in pids
    }


@router.get("", response_model=ApiResponse[list[ProjectSummaryOut]])
def list_projects(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    projects = db.query(TestProject).order_by(TestProject.id).all()
    aggregates = _summary_rows(db)
    items = []
    for p in projects:
        device_count, running_run_count = aggregates.get(p.id, (0, 0))
        out = ProjectSummaryOut.model_validate(p)
        out.device_count = device_count
        out.running_run_count = running_run_count
        items.append(out)
    return ok(items)


@router.get("/{project_key}", response_model=ApiResponse[ProjectDetailOut])
def get_project(
    project_key: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    project = (
        db.query(TestProject)
        .filter(TestProject.project_key == project_key)
        .first()
    )
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    device_count, running_run_count = _summary_rows(db).get(project.id, (0, 0))
    detail = ProjectDetailOut.model_validate(project)
    detail.device_count = device_count
    detail.running_run_count = running_run_count
    detail.plan_count = (
        db.query(func.count(Plan.id))
        .filter(Plan.project_id == project.id)
        .scalar() or 0
    )
    detail.total_run_count = (
        db.query(func.count(PlanRun.id))
        .filter(PlanRun.project_id == project.id)
        .scalar() or 0
    )
    recent = (
        db.query(PlanRun)
        .filter(PlanRun.project_id == project.id)
        .order_by(PlanRun.started_at.desc())
        .limit(5)
        .all()
    )
    detail.recent_runs = [RecentProjectRunOut.model_validate(r) for r in recent]
    return ok(detail)
