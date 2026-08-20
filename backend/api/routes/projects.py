"""ADR-0029 P2 / P2.5a — 项目登记簿 + Fleet 事实只读聚合。

- ``GET /api/v1/projects`` — 项目列表（卡片数据：facet 列 + 设备数 / 在跑
  Run 数聚合）。facet 筛选由前端对全量列表做——6 项目规模无需服务端筛选。
- ``GET /api/v1/projects/inventory/models`` — fleet 按 ``device.model`` 聚合。
- ``GET /api/v1/projects/inventory/summary`` — 工作台顶栏计数。
- ``GET /api/v1/projects/{project_key}/models`` — 回填标签下归属设备 model 反查。
- ``GET /api/v1/projects/{project_key}`` — 项目详情聚合（计数 + 最近 Run）。

静态路径 ``/inventory/*`` 必须注册在 ``/{project_key}`` 之前，否则
``inventory`` 会被当成 project_key。

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
    InventoryModelOut,
    InventorySummaryOut,
    ProjectDetailOut,
    ProjectModelCoverageOut,
    ProjectSummaryOut,
    RecentProjectRunOut,
)
from backend.core.database import get_db
from backend.models.host import Device
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.project import TestProject

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

# 「在跑」= RUNNING / QUEUED / PRECHECK（终态除外；DEGRADED 仅历史可读，不计）
_ACTIVE_RUN_STATUSES = ("RUNNING", "QUEUED", "PRECHECK")
_LEGACY_KEY = "LEGACY"


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


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _aggregate_inventory_rows(
    rows: list[tuple[str | None, str | None, str | None]],
) -> list[InventoryModelOut]:
    """``rows`` = (model, platform, project_key)；~545 台全量聚合，不分页。"""
    buckets: dict[str | None, dict] = {}
    for raw_model, raw_platform, project_key in rows:
        model = _blank_to_none(raw_model)
        bucket = buckets.get(model)
        if bucket is None:
            bucket = {
                "device_count": 0,
                "platforms": set(),
                "backfill_project_keys": set(),
                "legacy_device_count": 0,
                "null_device_count": 0,
            }
            buckets[model] = bucket
        bucket["device_count"] += 1
        platform = _blank_to_none(raw_platform)
        if platform:
            bucket["platforms"].add(platform)
        if project_key is None:
            bucket["null_device_count"] += 1
        else:
            bucket["backfill_project_keys"].add(project_key)
            if project_key == _LEGACY_KEY:
                bucket["legacy_device_count"] += 1
    items = [
        InventoryModelOut(
            model=model,
            device_count=bucket["device_count"],
            platforms=sorted(bucket["platforms"]),
            backfill_project_keys=sorted(bucket["backfill_project_keys"]),
            mapped_project_keys=[],
            legacy_device_count=bucket["legacy_device_count"],
            null_device_count=bucket["null_device_count"],
        )
        for model, bucket in buckets.items()
    ]
    items.sort(key=lambda item: (-item.device_count, item.model or ""))
    return items


def _load_inventory(db: Session) -> list[InventoryModelOut]:
    rows = (
        db.query(Device.model, Device.platform, TestProject.project_key)
        .outerjoin(TestProject, Device.project_id == TestProject.id)
        .all()
    )
    return _aggregate_inventory_rows(list(rows))


def _inventory_summary(items: list[InventoryModelOut]) -> InventorySummaryOut:
    total = sum(item.device_count for item in items)
    legacy = sum(item.legacy_device_count for item in items)
    null_count = sum(item.null_device_count for item in items)
    unmapped_models = [
        item.model
        for item in items
        if item.legacy_device_count + item.null_device_count == item.device_count
    ]
    return InventorySummaryOut(
        total_devices=total,
        mapped_devices=total - legacy - null_count,
        legacy_devices=legacy,
        null_devices=null_count,
        distinct_models=len(items),
        unmapped_models=unmapped_models,
    )


def _get_project_or_404(db: Session, project_key: str) -> TestProject:
    project = (
        db.query(TestProject)
        .filter(TestProject.project_key == project_key)
        .first()
    )
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


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


@router.get(
    "/inventory/models",
    response_model=ApiResponse[list[InventoryModelOut]],
)
def list_inventory_models(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    return ok(_load_inventory(db))


@router.get(
    "/inventory/summary",
    response_model=ApiResponse[InventorySummaryOut],
)
def get_inventory_summary(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    return ok(_inventory_summary(_load_inventory(db)))


@router.get(
    "/{project_key}/models",
    response_model=ApiResponse[list[ProjectModelCoverageOut]],
)
def list_project_models(
    project_key: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    project = _get_project_or_404(db, project_key)
    rows = (
        db.query(Device.model, Device.platform)
        .filter(Device.project_id == project.id)
        .all()
    )
    coverage_rows = [(model, platform, project_key) for model, platform in rows]
    return ok(
        [
            ProjectModelCoverageOut(
                model=item.model,
                device_count=item.device_count,
                platforms=item.platforms,
            )
            for item in _aggregate_inventory_rows(coverage_rows)
        ]
    )


@router.get("/{project_key}", response_model=ApiResponse[ProjectDetailOut])
def get_project(
    project_key: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    project = _get_project_or_404(db, project_key)

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
