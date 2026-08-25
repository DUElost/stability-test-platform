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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm.attributes import flag_modified

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
    ProjectSummaryOut,
    ProjectUpdateIn,
    RecentProjectRunOut,
)
from backend.core.audit import record_audit
from backend.core.database import get_db
from backend.models.host import Device
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.project import SEED_PROJECT_KEYS, TestProject
from backend.realtime.socketio_server import emit_project_changed

_UPDATABLE_FIELDS = (
    "display_name",
    "customer",
    "platform",
    "form_factor",
    "product_line",
    "jira_project_key",
)

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

_ACTIVE_RUN_STATUSES = ("RUNNING", "QUEUED", "PRECHECK")
_USER_SOURCE = "USER"
_SEED_SOURCE = "SEED"


def _summary_rows(db: Session) -> dict[int, tuple[int, int]]:
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
    rows: list[tuple[str | None, str | None, str | None, str | None]],
    *,
    rule_models: dict[str | None, set[str]],
) -> list[InventoryModelOut]:
    buckets: dict[str | None, dict] = {}
    for raw_model, raw_platform, project_key, source in rows:
        model = _blank_to_none(raw_model)
        bucket = buckets.get(model)
        if bucket is None:
            bucket = {
                "device_count": 0,
                "platforms": set(),
                "mapped_project_keys": set(),
                "unassigned_device_count": 0,
            }
            buckets[model] = bucket
        bucket["device_count"] += 1
        platform = _blank_to_none(raw_platform)
        if platform:
            bucket["platforms"].add(platform)
        if (
            source == _USER_SOURCE
            and project_key
            and project_key not in SEED_PROJECT_KEYS
        ):
            bucket["mapped_project_keys"].add(project_key)
        else:
            bucket["unassigned_device_count"] += 1
    for model, keys in rule_models.items():
        bucket = buckets.get(model)
        if bucket is None:
            continue
        bucket["mapped_project_keys"].update(keys)
    items = [
        InventoryModelOut(
            model=model,
            device_count=bucket["device_count"],
            platforms=sorted(bucket["platforms"]),
            mapped_project_keys=sorted(bucket["mapped_project_keys"]),
            unassigned_device_count=bucket["unassigned_device_count"],
        )
        for model, bucket in buckets.items()
    ]
    items.sort(key=lambda item: (-item.device_count, item.model or ""))
    return items


def _rule_models_by_model(db: Session) -> dict[str | None, set[str]]:
    mapping: dict[str | None, set[str]] = {}
    for project in db.query(TestProject).filter(TestProject.source == _USER_SOURCE).all():
        if project.project_key in SEED_PROJECT_KEYS:
            continue
        for raw in project.match_models or []:
            model = _blank_to_none(raw)
            mapping.setdefault(model, set()).add(project.project_key)
    return mapping


def _load_inventory(db: Session) -> list[InventoryModelOut]:
    rows = (
        db.query(
            Device.model,
            Device.platform,
            TestProject.project_key,
            TestProject.source,
        )
        .outerjoin(TestProject, Device.project_id == TestProject.id)
        .all()
    )
    return _aggregate_inventory(list(rows), rule_models=_rule_models_by_model(db))


def _inventory_summary(items: list[InventoryModelOut]) -> InventorySummaryOut:
    total = sum(item.device_count for item in items)
    user_mapped = sum(item.device_count - item.unassigned_device_count for item in items)
    unmapped_models = [
        item.model for item in items if not item.mapped_project_keys
    ]
    return InventorySummaryOut(
        total_devices=total,
        user_mapped_devices=user_mapped,
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


def _require_user_project(project: TestProject) -> None:
    if project.source != _USER_SOURCE:
        raise HTTPException(
            status_code=422,
            detail="seed backfill labels cannot be mapped; create a user project",
        )


def _fill_summary(db: Session, project: TestProject) -> ProjectSummaryOut:
    device_count, running_run_count = _summary_rows(db).get(project.id, (0, 0))
    out = ProjectSummaryOut.model_validate(project)
    out.match_models = list(project.match_models or [])
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
        .options(joinedload(Device.project))
        .filter(Device.model.in_(names))
        .all()
    )
    present = {_blank_to_none(d.model) for d in devices}
    unknown = [name for name in names if name not in present]
    will: list[Device] = []
    already = 0
    conflicts: list[ProjectMapConflictOut] = []
    for device in devices:
        current = device.project
        if current is not None and current.id == project.id:
            already += 1
            continue
        is_user = current is not None and current.source == _USER_SOURCE
        if is_user and not reassign_conflicts:
            conflicts.append(
                ProjectMapConflictOut(
                    device_id=device.id,
                    serial=device.serial,
                    model=_blank_to_none(device.model),
                    from_project_key=current.project_key,
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
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    query = db.query(TestProject).order_by(TestProject.id)
    if source == "user":
        query = query.filter(TestProject.source == _USER_SOURCE)
        query = query.filter(~TestProject.project_key.in_(SEED_PROJECT_KEYS))
    elif source == "seed":
        query = query.filter(TestProject.source == _SEED_SOURCE)
    projects = query.all()
    aggregates = _summary_rows(db)
    items = []
    for project in projects:
        device_count, running_run_count = aggregates.get(project.id, (0, 0))
        out = ProjectSummaryOut.model_validate(project)
        out.match_models = list(project.match_models or [])
        out.device_count = device_count
        out.running_run_count = running_run_count
        items.append(out)
    return ok(items)


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
        platform=payload.platform,
        form_factor=payload.form_factor,
        product_line=payload.product_line,
        jira_project_key=payload.jira_project_key,
        source=_USER_SOURCE,
        match_models=[],
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
    return ok(_inventory_summary(_load_inventory(db)))


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
    merged = list(project.match_models or [])
    for model in preview.models:
        if model not in merged:
            merged.append(model)
    project.match_models = merged
    flag_modified(project, "match_models")
    for device in to_assign:
        from_key = device.project.project_key if device.project else None
        device.project_id = project.id
        record_audit(
            db,
            action="assign_project",
            resource_type="device",
            resource_id=device.id,
            details={
                "project_key": project.project_key,
                "from_project_key": from_key,
            },
            user_id=current_user.id,
            username=current_user.username,
            request=request,
        )
    record_audit(
        db,
        action="apply_project_device_rule",
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
        db.query(Device.model, Device.platform, TestProject.project_key, TestProject.source)
        .join(TestProject, Device.project_id == TestProject.id)
        .filter(Device.project_id == project.id)
        .all()
    )
    return ok(
        [
            ProjectModelCoverageOut(
                model=item.model,
                device_count=item.device_count,
                platforms=item.platforms,
            )
            for item in _aggregate_inventory(list(rows), rule_models={})
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
    detail.match_models = list(project.match_models or [])
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
