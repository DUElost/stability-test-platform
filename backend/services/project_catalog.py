"""Project 读侧 catalog / 响应装配（#1520）。

``_fill_summary`` / list / detail / models / customers 原先散落在 ``projects``
路由；本模块收成单一真源，路由退化为 Depends + ``ok(build_*)``。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.api.schemas.project import (
    ProjectDetailOut,
    ProjectModelCoverageOut,
    ProjectSummaryOut,
    RecentProjectRunOut,
)
from backend.models.host import Device
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.project import Customer, TestProject
from backend.models.project_model import ProjectModel
from backend.services.project_inventory import (
    aggregate_inventory,
    platforms_map,
    rule_values_for_project,
    summary_rows,
    summary_rows_for,
)
from backend.services.project_registry import (
    SEED_SOURCE,
    USER_SOURCE,
    get_project_or_404,
)


def fill_project_summary(db: Session, project: TestProject) -> ProjectSummaryOut:
    """单项目 summary 装配（写路径成功后复用）。"""
    device_count, running_run_count = summary_rows_for(
        db, [project.id]
    ).get(project.id, (0, 0))
    out = ProjectSummaryOut.model_validate(project)
    out.match_models = rule_values_for_project(db, project.id)
    out.platforms = platforms_map(db, [project.id]).get(project.id, [])
    out.device_count = device_count
    out.running_run_count = running_run_count
    return out


def fill_promoted_summary(db: Session, project: TestProject) -> ProjectSummaryOut:
    """SEED→USER promote 成功后的 summary（历史口径：不填 platforms）。"""
    device_count, running_run_count = summary_rows(db).get(project.id, (0, 0))
    out = ProjectSummaryOut.model_validate(project)
    out.match_models = rule_values_for_project(db, project.id)
    out.device_count = device_count
    out.running_run_count = running_run_count
    return out


def list_project_summaries(
    db: Session,
    *,
    source: str = "user",
    status: Optional[str] = None,
) -> list[ProjectSummaryOut]:
    """GET /projects 列表装配。"""
    query = db.query(TestProject).order_by(TestProject.id)
    if source == "user":
        query = query.filter(TestProject.source == USER_SOURCE)
        # 注意：不按 SEED_PROJECT_KEYS 剔除 key——promote 转正的行 key 不变、
        # 但 source=USER，必须显示；未转正的 SEED 行已被 source 过滤挡住
    elif source == "seed":
        query = query.filter(TestProject.source == SEED_SOURCE)
        # 待转正队列默认只列 ACTIVE——归档 = 显式放弃（#644 P2-7 退场路径），
        # 放弃的标签不再占队列名额；显式 ?status=ARCHIVED 仍可列出复查
        if status is None:
            query = query.filter(TestProject.status == "ACTIVE")
    if status:
        query = query.filter(TestProject.status == status)
    projects = query.all()
    aggregates = summary_rows(db)
    platforms_by_project = platforms_map(db, [p.id for p in projects])
    items: list[ProjectSummaryOut] = []
    for project in projects:
        device_count, running_run_count = aggregates.get(project.id, (0, 0))
        out = ProjectSummaryOut.model_validate(project)
        out.match_models = rule_values_for_project(db, project.id)
        out.platforms = platforms_by_project.get(project.id, [])
        out.device_count = device_count
        out.running_run_count = running_run_count
        items.append(out)
    return items


def list_customer_entries(db: Session) -> list[dict]:
    """ADR-0029 D12 customer 字典。"""
    rows = db.query(Customer).order_by(Customer.sort_order, Customer.id).all()
    return [
        {"key": r.key, "display_name": r.display_name, "sort_order": r.sort_order}
        for r in rows
    ]


def list_project_model_coverage(
    db: Session, project_key: str
) -> list[ProjectModelCoverageOut]:
    """GET /{project_key}/models — 成员型号覆盖。"""
    project = get_project_or_404(db, project_key)
    # v2.5：详情页型号覆盖 = 该型号成员行下的设备（派生口径）——直接按
    # 成员行 + 设备型号过滤，不读 device.project_id 列
    rows = (
        db.query(Device.model, Device.platform)
        .join(ProjectModel, Device.model == ProjectModel.match_value)
        .filter(
            ProjectModel.project_id == project.id,
            ProjectModel.is_active.is_(True),
        )
        .all()
    )
    return [
        ProjectModelCoverageOut(
            model=item.model,
            device_count=item.device_count,
            platforms=item.platforms,
        )
        for item in aggregate_inventory(list(rows), model_to_projects={})
    ]


def build_project_detail(db: Session, project_key: str) -> ProjectDetailOut:
    """GET /{project_key} 详情装配。"""
    project = get_project_or_404(db, project_key)
    device_count, running_run_count = summary_rows(db).get(project.id, (0, 0))
    detail = ProjectDetailOut.model_validate(project)
    detail.match_models = rule_values_for_project(db, project.id)
    # #957: 派生 platforms 与列表 fill_project_summary 同一构造——详情不再落回 []。
    detail.platforms = platforms_map(db, [project.id]).get(project.id, [])
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
    return detail
