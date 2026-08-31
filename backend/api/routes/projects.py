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

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import User, get_current_active_user, require_admin
from backend.api.schemas.project import (
    InventoryModelOut,
    InventorySummaryOut,
    ProjectCreateIn,
    ProjectDetailOut,
    ProjectMapConflictOut,
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
from backend.models.project import SEED_PROJECT_KEYS, Customer, TestProject
from backend.models.project_model import ProjectModel
from backend.realtime.socketio_server import emit_project_changed

_UPDATABLE_FIELDS = (
    "display_name",
    "customer",
    "jira_project_key",
)

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

_ACTIVE_RUN_STATUSES = ("RUNNING", "QUEUED", "PRECHECK")
_USER_SOURCE = "USER"
_SEED_SOURCE = "SEED"


def _platforms_map(db: Session, project_ids: list[int]) -> dict[int, list[str]]:
    """ADR-0029 P1-B：项目平台从设备派生（distinct(device.platform)）。

    事实层在 device；UNKNOWN（探测失败哨兵）不展示。列表场景一次聚合，
    避免逐项目 N+1。
    """
    if not project_ids:
        return {}
    # v2.5 D10：平台按成员型号派生（device.model ⋈ project_model）
    rows = db.execute(
        select(ProjectModel.project_id, Device.platform)
        .join(Device, Device.model == ProjectModel.match_value)
        .where(
            ProjectModel.project_id.in_(project_ids),
            ProjectModel.is_active.is_(True),
            Device.platform.is_not(None),
        )
        .distinct()
    ).all()
    buckets: dict[int, set[str]] = {}
    for pid, platform in rows:
        if platform and platform != "UNKNOWN":
            buckets.setdefault(pid, set()).add(platform)
    return {pid: sorted(values) for pid, values in buckets.items()}


def _summary_rows(db: Session) -> dict[int, tuple[int, int]]:
    projects = db.query(TestProject.id).all()
    pids = [pid for (pid,) in projects]
    if not pids:
        return {}
    # v2.5 D10：设备数按成员型号派生（device.model ⋈ project_model）
    device_counts = dict(
        db.query(ProjectModel.project_id, func.count(Device.id))
        .join(Device, Device.model == ProjectModel.match_value)
        .filter(
            ProjectModel.project_id.in_(pids),
                        ProjectModel.is_active.is_(True),
        )
        .group_by(ProjectModel.project_id)
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


def _normalize_models(models: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in models:
        model = _blank_to_none(raw)
        if model is None or model in seen:
            continue
        seen.add(model)
        out.append(model)
    return out


def _aggregate_inventory(
    rows: list[tuple[str | None, str | None]],
    *,
    model_to_projects: dict[str | None, set[str]],
) -> list[InventoryModelOut]:
    """型号级聚合（ADR-0029 v2.5 D10 派生）。

    归属是型号级函数（device.model ⋈ project_model 活跃成员行）：
    mapped 型号全部设备归属、unmapped 全部未归属——unassigned_device_count
    不再逐设备累计（无例外，pinned 已废）。
    """
    buckets: dict[str | None, dict] = {}
    for raw_model, raw_platform in rows:
        model = _blank_to_none(raw_model)
        bucket = buckets.get(model)
        if bucket is None:
            bucket = {"device_count": 0, "platforms": set()}
            buckets[model] = bucket
        bucket["device_count"] += 1
        platform = _blank_to_none(raw_platform)
        if platform:
            bucket["platforms"].add(platform)
    items = [
        InventoryModelOut(
            model=model,
            device_count=bucket["device_count"],
            platforms=sorted(bucket["platforms"]),
            mapped_project_keys=sorted(model_to_projects.get(model, set())),
            unassigned_device_count=(
                bucket["device_count"]
                if not model_to_projects.get(model)
                else 0
            ),
        )
        for model, bucket in buckets.items()
    ]
    items.sort(key=lambda item: (-item.device_count, item.model or ""))
    return items


def _rule_values_for_project(db: Session, project_id: int) -> list[str]:
    """项目活跃规则 → match_models 兼容列表（对外 API 契约）。

    match_models 列已 drop（P1 收尾），此处是唯一读侧派生来源。
    """
    rows = db.execute(
        select(ProjectModel.match_value)
        .where(
            ProjectModel.project_id == project_id,
                        ProjectModel.is_active.is_(True),
        )
        .order_by(ProjectModel.match_value)
    ).scalars().all()
    return list(rows)


def _model_to_projects(db: Session) -> dict[str | None, set[str]]:
    """活跃成员行全量：model → {project_key}（v2.5 派生读预取）。"""
    mapping: dict[str | None, set[str]] = {}
    rows = db.execute(
        select(ProjectModel.match_value, TestProject.project_key)
        .join(TestProject, TestProject.id == ProjectModel.project_id)
        .where(
            ProjectModel.is_active.is_(True),
            TestProject.source == _USER_SOURCE,
        )
    ).all()
    for raw, key in rows:
        model = _blank_to_none(raw)
        if model is not None:
            mapping.setdefault(model, set()).add(key)
    return mapping


def _load_inventory(db: Session) -> list[InventoryModelOut]:
    rows = (
        db.query(Device.model, Device.platform).all()
    )
    return _aggregate_inventory(
        list(rows), model_to_projects=_model_to_projects(db),
    )


def _inventory_summary(db: Session, items: list[InventoryModelOut]) -> InventorySummaryOut:
    total = sum(item.device_count for item in items)
    user_mapped = sum(item.device_count - item.unassigned_device_count for item in items)
    unmapped_models = [
        item.model for item in items if not item.mapped_project_keys
    ]
    # ADR-0029 v2.5：严格未映射口径——型号无 **USER** 成员行的设备数
    # （SEED 成员不算映射，与 _model_to_projects 同口径；设备页
    # ?unassigned=true 一致；型号级归属函数，无逐设备例外）。
    mapped_models = (
        select(ProjectModel.match_value)
        .join(TestProject, TestProject.id == ProjectModel.project_id)
        .where(
            ProjectModel.is_active.is_(True),
            TestProject.source == _USER_SOURCE,
        )
    )
    unassigned_devices = db.query(func.count(Device.id)).filter(
        or_(Device.model.is_(None), ~Device.model.in_(mapped_models))
    ).scalar() or 0
    return InventorySummaryOut(
        total_devices=total,
        user_mapped_devices=user_mapped,
        distinct_models=len(items),
        unmapped_models=unmapped_models,
        unassigned_devices=unassigned_devices,
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


def _require_user_project(project: TestProject) -> None:
    if project.source != _USER_SOURCE:
        raise HTTPException(
            status_code=422,
            detail="seed backfill labels cannot be mapped; create a user project",
        )


def _fill_summary(db: Session, project: TestProject) -> ProjectSummaryOut:
    device_count, running_run_count = _summary_rows(db).get(project.id, (0, 0))
    out = ProjectSummaryOut.model_validate(project)
    out.match_models = _rule_values_for_project(db, project.id)
    out.platforms = _platforms_map(db, [project.id]).get(project.id, [])
    out.device_count = device_count
    out.running_run_count = running_run_count
    return out


def _map_preview(
    db: Session, project: TestProject, models: list[str], reassign_conflicts: bool
) -> tuple[ProjectMapPreviewOut, list[Device]]:
    names = _normalize_models(models)
    if not names:
        raise HTTPException(status_code=422, detail="models must not be empty")
    devices = (
        db.query(Device)
        .filter(Device.model.in_(names))
        .all()
    )
    present = {_blank_to_none(d.model) for d in devices}
    unknown = [name for name in names if name not in present]
    # 型号级归属（v2.5 D10 派生）：当前项目 = 型号的活跃成员行
    model_to_project = {
        match_value: (pid, key, source)
        for pid, match_value, key, source in (
            db.query(
                ProjectModel.project_id, ProjectModel.match_value,
                TestProject.project_key, TestProject.source,
            )
            .join(TestProject, TestProject.id == ProjectModel.project_id)
            .filter(ProjectModel.is_active.is_(True))
            .all()
        )
    }
    will: list[Device] = []
    already = 0
    conflicts: list[ProjectMapConflictOut] = []
    for device in devices:
        entry = model_to_project.get(_blank_to_none(device.model))
        if entry is not None and entry[0] == project.id:
            already += 1
            continue
        is_user = entry is not None and entry[2] == _USER_SOURCE
        if is_user and not reassign_conflicts:
            conflicts.append(
                ProjectMapConflictOut(
                    device_id=device.id,
                    serial=device.serial,
                    model=_blank_to_none(device.model),
                    from_project_key=entry[2],
                )
            )
            continue
        will.append(device)
    preview = ProjectMapPreviewOut(
        target_project_key=project.project_key,
        models=names,
        will_assign=len(will),
        already_in_target=already,
        conflicts=conflicts,
        unknown_models=unknown,
    )
    return preview, will


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
    if status:
        query = query.filter(TestProject.status == status)
    projects = query.all()
    aggregates = _summary_rows(db)
    platforms_map = _platforms_map(db, [p.id for p in projects])
    items = []
    for project in projects:
        device_count, running_run_count = aggregates.get(project.id, (0, 0))
        out = ProjectSummaryOut.model_validate(project)
        out.match_models = _rule_values_for_project(db, project.id)
        out.platforms = platforms_map.get(project.id, [])
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
    key = payload.project_key
    if key.upper() in SEED_PROJECT_KEYS:
        raise HTTPException(status_code=422, detail="reserved seed project_key")
    existing = (
        db.query(TestProject)
        .filter(func.lower(TestProject.project_key) == key.lower())
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="project_key already exists")
    project = TestProject(
        project_key=key,
        display_name=payload.display_name.strip(),
        customer=payload.customer,
        jira_project_key=payload.jira_project_key,
        source=_USER_SOURCE,
        status="ACTIVE",
    )
    db.add(project)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="project_key already exists") from None
    record_audit(
        db,
        action="create_project",
        resource_type="test_project",
        resource_id=project.id,
        details={"project_key": key},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(project)
    emit_project_changed(project.id, "created")
    return ok(_fill_summary(db, project))


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
    return ok(_inventory_summary(db, _load_inventory(db)))


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
            _blank_to_none(m)
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
            if _blank_to_none(m)
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
    from backend.realtime.socketio_server import emit_project_changed

    emit_project_changed(seed.id, "promoted")

    device_count, running_run_count = _summary_rows(db).get(seed.id, (0, 0))
    out = ProjectSummaryOut.model_validate(seed)
    out.match_models = _rule_values_for_project(db, seed.id)
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
    """ADR-0029 D2 / #406 — facet 修改；逐字段 ``record_audit``。"""
    project = _get_project_or_404(db, project_key)
    _require_user_project(project)
    if project.status == "ARCHIVED":
        raise HTTPException(status_code=409, detail="archived project cannot be updated")

    fields_set = getattr(payload, "model_fields_set", set())
    if not fields_set:
        raise HTTPException(status_code=422, detail="no fields to update")

    changed: list[tuple[str, object, object]] = []
    for field in _UPDATABLE_FIELDS:
        if field not in fields_set:
            continue
        new_value = getattr(payload, field)
        old_value = getattr(project, field)
        if old_value == new_value:
            continue
        setattr(project, field, new_value)
        changed.append((field, old_value, new_value))

    if not changed:
        return ok(_fill_summary(db, project))

    project.updated_at = datetime.now(timezone.utc)
    for field, old_value, new_value in changed:
        record_audit(
            db,
            action="update_project",
            resource_type="test_project",
            resource_id=project.id,
            details={
                "project_key": project.project_key,
                "field": field,
                "old": old_value,
                "new": new_value,
            },
            user_id=current_user.id,
            username=current_user.username,
            request=request,
        )
    db.commit()
    db.refresh(project)
    emit_project_changed(project.id, "updated")
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
    """ADR-0029 D2 / #406 — 归档（单向；SEED 回填标签不可归档）。"""
    project = _get_project_or_404(db, project_key)
    _require_user_project(project)
    if project.status == "ARCHIVED":
        raise HTTPException(status_code=409, detail="project already archived")

    project.status = "ARCHIVED"
    project.updated_at = datetime.now(timezone.utc)
    record_audit(
        db,
        action="archive_project",
        resource_type="test_project",
        resource_id=project.id,
        details={
            "project_key": project.project_key,
            "from_status": "ACTIVE",
            "to_status": "ARCHIVED",
        },
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(project)
    emit_project_changed(project.id, "archived")
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
    project = _get_project_or_404(db, project_key)
    _require_user_project(project)
    new_key = payload.new_key
    if new_key.upper() in SEED_PROJECT_KEYS:
        raise HTTPException(status_code=422, detail="reserved seed project_key")
    existing = (
        db.query(TestProject)
        .filter(func.lower(TestProject.project_key) == new_key.lower())
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="project_key already exists")

    old_key = project.project_key
    project.project_key = new_key
    project.updated_at = datetime.now(timezone.utc)
    record_audit(
        db,
        action="rename_project",
        resource_type="test_project",
        resource_id=project.id,
        details={
            "from_project_key": old_key,
            "to_project_key": new_key,
        },
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(project)
    emit_project_changed(project.id, "renamed")
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
    project = _get_project_or_404(db, project_key)
    _require_user_project(project)
    preview, _devices = _map_preview(
        db, project, payload.models, payload.reassign_conflicts
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
    project = _get_project_or_404(db, project_key)
    _require_user_project(project)
    preview, to_assign = _map_preview(
        db, project, payload.models, payload.reassign_conflicts
    )
    if preview.conflicts:
        raise HTTPException(
            status_code=409,
            detail="models already mapped to another user project",
        )
    # ADR-0029 P1：规则写入 project_model（活跃唯一索引兜底——同型号
    # 双归属 INSERT 即 IntegrityError，preview 设备级冲突之外的双保险）。
    # v2.5 语义与 preview 对齐（M3 修正）：SEED 项目占用不算冲突（让位），
    # 仅 USER 项目占用需 reassign_conflicts。
    for model in preview.models:
        existing = db.execute(
            select(ProjectModel)
            .where(
                ProjectModel.match_value == model,
                ProjectModel.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if existing is not None and existing.project_id != project.id:
            owner = db.get(TestProject, existing.project_id)
            if owner is not None and owner.source == _USER_SOURCE and not payload.reassign_conflicts:
                raise HTTPException(
                    status_code=409,
                    detail=f"model {model} already ruled to another project",
                )
            # SEED 占用或 reassign：旧成员行让位（uq 只约束活跃行）
            existing.is_active = False
        if existing is None or existing.project_id != project.id:
            db.add(ProjectModel(
                project_id=project.id,
                match_value=model,
                created_by=current_user.id,
            ))
    # v2.5 D10 M3：归属派生——apply 只写成员行，不写设备列（无副本可写）
    record_audit(
        db,
        action="apply_project_model",
        resource_type="test_project",
        resource_id=project.id,
        details={
            "project_key": project.project_key,
            "models": preview.models,
            "assigned_count": preview.will_assign,
        },
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    # 归属变更必须广播——否则 B 端陈旧缓存可一路放行到派发（ADR-0029 D8）。
    emit_project_changed(project.id, "assigned")
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
    """ADR-0029 复盘：删除项目的一条活跃型号规则（admin，记审计）。

    规则表此前只增不减（map/apply 对已归属别的项目的型号 409），错误规则
    （如生产 A57→MLD_LX2 残留）无法通过平台修正。删除 = 撤回成员声明：
    型号脱离项目后，该型号设备在派生读路径下立即回归「未映射」（v2.5 D10
    归属派生化——无副本可写，无需任何收敛机制）。

    路由顺序：/{project_key}/rules/{model} 三段静态，与 /{project_key}
    单段、/inventory/* 静态段互不冲突。
    """
    project = _get_project_or_404(db, project_key)
    _require_user_project(project)
    rule = db.execute(
        select(ProjectModel)
        .where(
            ProjectModel.project_id == project.id,
                        ProjectModel.match_value == model,
            ProjectModel.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if rule is None:
        raise HTTPException(
            status_code=404,
            detail=f"no active rule for model {model}",
        )
    db.delete(rule)
    record_audit(
        db,
        action="remove_project_model",
        resource_type="test_project",
        resource_id=project.id,
        details={
            "project_key": project.project_key,
            "model": model,
        },
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    emit_project_changed(project.id, "rule_removed")
    return ok({"project_key": project.project_key, "model": model})



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
            for item in _aggregate_inventory(list(rows), model_to_projects={})
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
    detail.match_models = _rule_values_for_project(db, project.id)
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
