"""ADR-0029 — 项目登记簿 + Fleet 事实 + 人工映射。

- ``GET /api/v1/projects`` — 默认只返回 ``source=USER``（人工项目）。
- ``POST /api/v1/projects`` — admin 新建 USER 项目。
- ``PUT /api/v1/projects/{key}`` — admin 改 facet（逐字段审计）。
- ``POST /api/v1/projects/{key}/archive`` — admin 归档。
- ``GET /api/v1/projects/inventory/models`` — fleet 按 model 聚合；
  ``mapped_project_keys`` 只含 USER 项目。
- ``POST /api/v1/projects/{key}/map/preview|apply`` — 把型号映射到 USER 项目。

静态路径 ``/inventory/*`` 必须注册在 ``/{project_key}`` 之前。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import User, get_current_active_user, require_admin
from backend.api.schemas.project import (
    InventoryModelOut,
    InventorySummaryOut,
    ProjectCreateIn,
    ProjectDetailOut,
    ProjectMapIn,
    ProjectMapPreviewOut,
    ProjectModelCoverageOut,
    ProjectRenameIn,
    ProjectSummaryOut,
    ProjectUpdateIn,
    RecentProjectRunOut,
)
from backend.core.audit import record_audit
from backend.core.database import get_db
from backend.models.host import Device
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.project import Customer, TestProject
from backend.models.project_model import ProjectModel
from backend.realtime.socketio_server import emit_project_changed
from backend.services.project_inventory import (
    aggregate_inventory,
    inventory_summary,
    load_inventory,
    platforms_map,
    rule_values_for_project,
    summary_rows,
    summary_rows_for,
)
from backend.services.project_mapping import (
    apply_project_mapping,
    blank_to_none,
    preview_project_mapping,
    remove_project_mapping_rule,
)
from backend.services.project_registry import (
    UPDATABLE_FIELDS,
    archive_project_entry,
    create_project_entry,
    get_project_or_404,
    rename_project_entry,
    unarchive_project_entry,
    update_project_facets,
)

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

_ACTIVE_RUN_STATUSES = ("RUNNING", "QUEUED", "PRECHECK")
_USER_SOURCE = "USER"
_SEED_SOURCE = "SEED"


def _fill_summary(db: Session, project: TestProject) -> ProjectSummaryOut:
    device_count, running_run_count = summary_rows_for(
        db, [project.id]
    ).get(project.id, (0, 0))
    out = ProjectSummaryOut.model_validate(project)
    out.match_models = rule_values_for_project(db, project.id)
    out.platforms = platforms_map(db, [project.id]).get(project.id, [])
    out.device_count = device_count
    out.running_run_count = running_run_count
    return out


@router.get("", response_model=ApiResponse[list[ProjectSummaryOut]])
def list_projects(
    source: str = Query("user", pattern="^(user|seed|all)$"),
    status: Optional[str] = Query(None, pattern="^(ACTIVE|ARCHIVED)$",
                                  description="ADR-0029 P0: filter by lifecycle status"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    query = db.query(TestProject).order_by(TestProject.id)
    if source == "user":
        query = query.filter(TestProject.source == _USER_SOURCE)
        # 注意：不按 SEED_PROJECT_KEYS 剔除 key——promote 转正的行 key 不变、
        # 但 source=USER，必须显示；未转正的 SEED 行已被 source 过滤挡住
    elif source == "seed":
        query = query.filter(TestProject.source == _SEED_SOURCE)
        # 待转正队列默认只列 ACTIVE——归档 = 显式放弃（#644 P2-7 退场路径），
        # 放弃的标签不再占队列名额；显式 ?status=ARCHIVED 仍可列出复查
        if status is None:
            query = query.filter(TestProject.status == "ACTIVE")
    if status:
        query = query.filter(TestProject.status == status)
    projects = query.all()
    aggregates = summary_rows(db)
    platforms_by_project = platforms_map(db, [p.id for p in projects])
    items = []
    for project in projects:
        device_count, running_run_count = aggregates.get(project.id, (0, 0))
        out = ProjectSummaryOut.model_validate(project)
        out.match_models = rule_values_for_project(db, project.id)
        out.platforms = platforms_by_project.get(project.id, [])
        out.device_count = device_count
        out.running_run_count = running_run_count
        items.append(out)
    return ok(items)


@router.get("/customers", response_model=ApiResponse[list[dict]])
def list_customers(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0029 D12 customer 字典——项目编辑下拉的数据源。

    静态种子数据（key 即客户名，seed 从 test_project 去重回填），无写端点——
    变更走迁移（同 list_specialties 口径）。customer 列不动（自由文本保留），
    字典表只承担输入建议。
    """
    rows = db.query(Customer).order_by(Customer.sort_order, Customer.id).all()
    return ok([{"key": r.key, "display_name": r.display_name,
                "sort_order": r.sort_order} for r in rows])


@router.post("", response_model=ApiResponse[ProjectSummaryOut], status_code=201)
def create_project(
    payload: ProjectCreateIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """ADR-0029 D2 / #406 — 新建 USER 项目（校验/审计/事件在 project_registry）。"""
    project = create_project_entry(
        db,
        project_key=payload.project_key,
        display_name=payload.display_name,
        customer=payload.customer,
        jira_project_key=payload.jira_project_key,
        actor_id=current_user.id,
        actor_username=current_user.username,
        request=request,
    )
    return ok(_fill_summary(db, project))


@router.get(
    "/inventory/models",
    response_model=ApiResponse[list[InventoryModelOut]],
)
def list_inventory_models(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    return ok(load_inventory(db))


@router.get(
    "/inventory/summary",
    response_model=ApiResponse[InventorySummaryOut],
)
def getinventory_summary(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    return ok(inventory_summary(db, load_inventory(db)))


@router.post(
    "/seed/{project_key}/promote",
    response_model=ApiResponse[ProjectSummaryOut],
)
def promote_seed_project(
    project_key: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """ADR-0029 P0：SEED 回填标签转正为人工项目（admin 动作，就地转换）。

    把 P1 脚本灌入的标签（HONOR-ELA 等）变成有终点的待办队列：SEED 行
    source SEED → USER、match_models 预填其持有设备的型号、设备归属不动
    （project_id 不变，行身份即归属身份）。解决「设备行显示归属它、筛选
    下拉里却没有它」的半隐身状态。

    project_key 全局唯一（uq_test_project_key 不分 source），不能新建同 key
    USER 行——就地转换是唯一不违反约束的路径。

    LEGACY 是「无型号设备」兜底标签，不是待转正对象——拒绝。
    幂等：source=SEED 且 ACTIVE 才能转正，重复调用 → 404（已非 SEED）。
    """
    seed = (
        db.query(TestProject)
        .filter(TestProject.project_key == project_key)
        .first()
    )
    if seed is None or seed.source != _SEED_SOURCE:
        raise HTTPException(status_code=404, detail="seed project not found")
    if project_key == "LEGACY":
        raise HTTPException(status_code=422, detail="LEGACY is the fallback bucket, not promotable")
    if seed.status == "ARCHIVED":
        raise HTTPException(status_code=409, detail="seed project archived, not promotable")

    models = sorted(
        {
            blank_to_none(m)
            for (m,) in (
                db.query(Device.model)
                .join(ProjectModel, Device.model == ProjectModel.match_value)
                .filter(
                    ProjectModel.project_id == seed.id,
                    ProjectModel.is_active.is_(True),
                    Device.model.is_not(None),
                )
                .all()
            )
            if blank_to_none(m)
        }
    )
    seed.source = _USER_SOURCE
    existing_members = {
        m for (m,) in db.query(ProjectModel.match_value).filter(
            ProjectModel.project_id == seed.id,
            ProjectModel.is_active.is_(True),
        ).all()
    }
    for model in models:
        if model in existing_members:
            continue   # 幂等：成员行已存在（如带外预建）不重复插入
        db.add(ProjectModel(
            project_id=seed.id,
            match_value=model,
            created_by=current_user.id,
        ))
    record_audit(
        db,
        action="promote_seed_project",
        resource_type="test_project",
        resource_id=seed.id,
        details={
            "project_key": project_key,
            "match_models": models,
        },
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()

    emit_project_changed(seed.id, "promoted")

    device_count, running_run_count = summary_rows(db).get(seed.id, (0, 0))
    out = ProjectSummaryOut.model_validate(seed)
    out.match_models = rule_values_for_project(db, seed.id)
    out.device_count = device_count
    out.running_run_count = running_run_count
    return ok(out)


@router.put("/{project_key}", response_model=ApiResponse[ProjectSummaryOut])
def update_project(
    project_key: str,
    payload: ProjectUpdateIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """ADR-0029 D2 / #406 — facet 修改；逐字段 ``record_audit``（服务层）。"""
    fields_set = getattr(payload, "model_fields_set", set())
    if not fields_set:
        raise HTTPException(status_code=422, detail="no fields to update")
    provided = {
        field: getattr(payload, field)
        for field in UPDATABLE_FIELDS
        if field in fields_set
    }
    project = update_project_facets(
        db,
        project_key=project_key,
        provided=provided,
        actor_id=current_user.id,
        actor_username=current_user.username,
        request=request,
    )
    return ok(_fill_summary(db, project))


@router.post(
    "/{project_key}/archive",
    response_model=ApiResponse[ProjectSummaryOut],
)
def archive_project(
    project_key: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """ADR-0029 D2 / #406 — 归档（SEED 回填标签也可归档 = 显式放弃）。"""
    project = archive_project_entry(
        db,
        project_key=project_key,
        actor_id=current_user.id,
        actor_username=current_user.username,
        request=request,
    )
    return ok(_fill_summary(db, project))


@router.post(
    "/{project_key}/unarchive",
    response_model=ApiResponse[ProjectSummaryOut],
)
def unarchive_project(
    project_key: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """#644 P1-4 — 解档：ARCHIVED 项目恢复 ACTIVE（admin，记审计）。

    归档守卫补齐后的必要另一半——没有解档端点，「归档 = 冻结」就是单行道，
    误归档无法撤销。SEED 行从未被归档（v2.5 前不可归档；本版起允许），
    对 ACTIVE 行调用幂等地 409（与 archive 对称）。
    """
    project = unarchive_project_entry(
        db,
        project_key=project_key,
        actor_id=current_user.id,
        actor_username=current_user.username,
        request=request,
    )
    return ok(_fill_summary(db, project))


@router.put(
    "/{project_key}/rename",
    response_model=ApiResponse[ProjectSummaryOut],
)
def rename_project(
    project_key: str,
    payload: ProjectRenameIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """ADR-0029 D2 复核：项目重命名（admin，记审计）。

    key 是用户指定标识（创建时手填），外键全用数字 project_id——改名
    不影响 device/plan/plan_run 归属。影响面：旧 URL 404（新 URL 生效）、
    历史审计显示旧 key（留痕）。SEED 保留名仍不可作新 key。
    """
    project = rename_project_entry(
        db,
        project_key=project_key,
        new_key=payload.new_key,
        actor_id=current_user.id,
        actor_username=current_user.username,
        request=request,
    )
    return ok(_fill_summary(db, project))


@router.post(
    "/{project_key}/map/preview",
    response_model=ApiResponse[ProjectMapPreviewOut],
)
def preview_project_map(
    project_key: str,
    payload: ProjectMapIn,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_admin),
):
    preview = preview_project_mapping(
        db,
        project_key=project_key,
        models=payload.models,
        reassign_conflicts=payload.reassign_conflicts,
    )
    return ok(preview)


@router.post(
    "/{project_key}/map/apply",
    response_model=ApiResponse[ProjectMapPreviewOut],
)
def apply_project_map(
    project_key: str,
    payload: ProjectMapIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    preview = apply_project_mapping(
        db,
        project_key=project_key,
        models=payload.models,
        reassign_conflicts=payload.reassign_conflicts,
        actor_id=current_user.id,
        actor_username=current_user.username,
        request=request,
    )
    return ok(preview)


@router.delete(
    "/{project_key}/rules/{model}",
    response_model=ApiResponse[dict],
)
def remove_project_rule(
    project_key: str,
    model: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """删除项目的一条活跃型号成员行（admin，记审计）。

    成员声明可撤回（map/apply 对已归属别的项目的型号 409，错误映射
    如生产 A57→MLD_LX2 残留曾无法通过平台修正）：型号脱离项目后，
    该型号设备在派生读路径下立即回归「未映射」（v2.5 D10 归属派生化——
    无副本可写，无需任何收敛机制）。

    路由顺序：/{project_key}/rules/{model} 三段静态，与 /{project_key}
    单段、/inventory/* 静态段互不冲突。
    """
    removed = remove_project_mapping_rule(
        db,
        project_key=project_key,
        model=model,
        actor_id=current_user.id,
        actor_username=current_user.username,
        request=request,
    )
    return ok(removed)



@router.get(
    "/{project_key}/models",
    response_model=ApiResponse[list[ProjectModelCoverageOut]],
)
def list_project_models(
    project_key: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
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
    return ok(
        [
            ProjectModelCoverageOut(
                model=item.model,
                device_count=item.device_count,
                platforms=item.platforms,
            )
            for item in aggregate_inventory(list(rows), model_to_projects={})
        ]
    )


@router.get("/{project_key}", response_model=ApiResponse[ProjectDetailOut])
def get_project(
    project_key: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    project = get_project_or_404(db, project_key)
    device_count, running_run_count = summary_rows(db).get(project.id, (0, 0))
    detail = ProjectDetailOut.model_validate(project)
    detail.match_models = rule_values_for_project(db, project.id)
    # #957: 派生 platforms 与列表 _fill_summary 同一构造——详情不再落回 []。
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
    return ok(detail)
