"""ADR-0029 — 项目登记簿 + Fleet 事实 + 人工映射。

- ``GET /api/v1/projects`` — 默认只返回 ``source=USER``（人工项目）。
- ``POST /api/v1/projects`` — admin 新建 USER 项目。
- ``PUT /api/v1/projects/{key}`` — admin 改 facet（逐字段审计）。
- ``POST /api/v1/projects/{key}/archive`` — admin 归档。
- ``POST /api/v1/projects/seed/{key}/promote`` — SEED→USER 转正（服务层）。
- ``GET /api/v1/projects/inventory/models`` — fleet 按 model 聚合；
  ``mapped_project_keys`` 只含 USER 项目。
- ``POST /api/v1/projects/{key}/map/preview|apply`` — 把型号映射到 USER 项目。

静态路径 ``/inventory/*`` 必须注册在 ``/{project_key}`` 之前。

#1520：读侧 list/detail/models/customers/summary 装配在
``services/project_catalog``；本文件只留 Depends + ``ok(...)``。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
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
)
from backend.core.database import get_db
from backend.services.project_catalog import (
    build_project_detail,
    fill_project_summary,
    fill_promoted_summary,
    list_customer_entries,
    list_project_model_coverage,
    list_project_summaries,
)
from backend.services.project_inventory import (
    inventory_summary,
    load_inventory,
)
from backend.services.project_mapping import (
    apply_project_mapping,
    preview_project_mapping,
    remove_project_mapping_rule,
)
from backend.services.project_registry import (
    UPDATABLE_FIELDS,
    archive_project_entry,
    create_project_entry,
    promote_seed_project_entry,
    rename_project_entry,
    unarchive_project_entry,
    update_project_facets,
)

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


@router.get("", response_model=ApiResponse[list[ProjectSummaryOut]])
def list_projects(
    source: str = Query("user", pattern="^(user|seed|all)$"),
    status: Optional[str] = Query(None, pattern="^(ACTIVE|ARCHIVED)$",
                                  description="ADR-0029 P0: filter by lifecycle status"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """#1520 薄壳：列表装配在 ``project_catalog.list_project_summaries``。"""
    return ok(list_project_summaries(db, source=source, status=status))


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
    return ok(list_customer_entries(db))


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
    return ok(fill_project_summary(db, project))


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

    业务规则见 ``promote_seed_project_entry``；本路由只做响应装配。
    """
    seed = promote_seed_project_entry(
        db,
        project_key=project_key,
        actor_id=current_user.id,
        actor_username=current_user.username,
        request=request,
    )
    return ok(fill_promoted_summary(db, seed))


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
    return ok(fill_project_summary(db, project))


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
    return ok(fill_project_summary(db, project))


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
    return ok(fill_project_summary(db, project))


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
    return ok(fill_project_summary(db, project))


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
    """#1520 薄壳：型号覆盖在 ``project_catalog.list_project_model_coverage``。"""
    return ok(list_project_model_coverage(db, project_key))


@router.get("/{project_key}", response_model=ApiResponse[ProjectDetailOut])
def get_project(
    project_key: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """#1520 薄壳：详情装配在 ``project_catalog.build_project_detail``。"""
    return ok(build_project_detail(db, project_key))
