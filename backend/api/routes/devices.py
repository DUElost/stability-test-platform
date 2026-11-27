from datetime import datetime, timezone
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional, Union

from backend.core.database import get_db
from backend.services.audit_writer import record_audit
from backend.core.job_timeout_config import HOST_HEARTBEAT_TIMEOUT_SECONDS  # noqa: F401
from backend.models.host import Host, Device
from backend.models.project import TestProject
from backend.models.project_model import ProjectModel
from backend.api.schemas import DeviceCreate, DeviceOut, PaginatedResponse
from backend.api.response import ApiResponse, ok
from backend.api.schemas.device import (
    BulkProjectAssignIn,
    BulkSkipOobeIn,
    BulkSkipOobeOut,
    BulkSwipeTrailIn,
    BulkSwipeTrailOut,
    DeviceFacetsOut,
    DeviceLeaseReleaseIn,
    DeviceLeaseReleaseOut,
    DeviceListItemOut,
    DeviceRetireBatchIn,
    DeviceRetireBatchOut,
    DeviceRetireBatchResult,
    DeviceRetireIn,
    DeviceSelectionPreviewIn,
    DeviceSelectionPreviewOut,
    DeviceUnretireIn,
    DeviceRebindIn,
    DeviceReturnDebtOut,
    DeviceReturnOverrideIn,
    DeviceReturnOverrideOut,
)
from backend.api.routes.auth import get_current_active_user, require_admin, User
from backend.services.device_inventory_query import (
    DeviceQueryError,
    DeviceQueryFilters,
    build_device_facets,
    build_device_selection_preview,
    enforce_bulk_write_limit,
    fetch_active_lease_fields,
    query_filtered_devices_page,
    serialize_device_full_readonly,
    serialize_device_list_item,
    validate_projection,
    validate_sort_params,
)
from backend.services.device_lease_emergency_release import release_terminal_job_lease
from backend.services.device_return import apply_override_sync, open_debts_for_device_sync
from backend.core.metrics import record_device_return_override
from backend.services.vbus_usb_presence import usb_presence_for
from backend.services.device_retirement import (
    retire_device,
    retire_devices_batch,
    unretire_device,
)
from backend.services.device_rebind import rebind_device
from backend.services.device_skip_oobe import bulk_skip_device_oobe
from backend.services.device_swipe_trail import bulk_set_swipe_trail

# `GET /devices` 的**单次响应** limit 护栏。它是体积护栏，**不是 fleet 总量**：
# 48 host × 25 台 = 1200 已把本值压满，而 ADR-0026 的规模目标是 60+ host / 1000+
# device（= 1500 台），即本值已落在承诺包线之内（#3131）。客户端要全量必须按 skip
# 翻页（前端 `fetchAllDevicePages`）；把本值当成「一次能装下整个 fleet」的写法在扩容
# 后只会静默少设备。调大本值等于把墙往后挪，且单次响应体积随之线性上涨
# （1200 台实测 473 KB / 220 ms），正解是消费方翻页而不是抬护栏。
_DEVICE_LIST_MAX_LIMIT = 1200

logger = logging.getLogger(__name__)


def _enforce_bulk_limit_http(device_ids: List[int], *, action: str) -> None:
    try:
        enforce_bulk_write_limit(device_ids, action=action)
    except DeviceQueryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


def _serialize_device_readonly(
    device: Device,
    model_to_project: dict,
    *,
    now: datetime,
    active_lease=None,
) -> DeviceOut:
    """只读构造 DeviceOut：以请求内统一时间基准派生有效状态、陈旧度与在位信息，不改写 ORM。"""
    return serialize_device_full_readonly(
        device,
        model_to_project,
        now=now,
        active_lease=active_lease,
    )


router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


@router.post("", response_model=DeviceOut)
def create_device(
    payload: DeviceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    # 检查序列号是否已存在
    existing = db.query(Device).filter(Device.serial == payload.serial).first()
    if existing:
        raise HTTPException(status_code=400, detail="Device with this serial already exists")
    if payload.host_id is not None and db.get(Host, payload.host_id) is None:
        raise HTTPException(status_code=400, detail="host not found")

    device = Device(
        serial=payload.serial,
        model=payload.model,
        host_id=payload.host_id,
        tags=payload.tags,
    )
    db.add(device)
    db.flush()
    record_audit(
        db,
        action="create",
        resource_type="device",
        resource_id=device.id,
        details={"serial": device.serial, "model": device.model, "host_id": device.host_id},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(device)
    return device


@router.post("/bulk-project", response_model=ApiResponse[List[DeviceOut]])
def bulk_assign_project(
    payload: BulkProjectAssignIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    """ADR-0029 P2 — 设备批量归入项目（admin 动作）。

    - project_key 不存在 → 404；device_ids 含不存在 id → 404（整体事务，
      防部分成功）；
    - 每台实际变更记录一条 audit（action=assign_project，details 含
      from/to project_key——F2：审计可读）；
    - 幂等：已是目标项目的设备跳过（不记 audit）；
    - 「移出项目」= 归入 LEGACY（V2 后无 NULL 公共池，NULL 仅迁移瞬态）。
    """
    project = (
        db.query(TestProject)
        .filter(TestProject.project_key == payload.project_key)
        .first()
    )
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    # #752：归档守卫（与 projects.py `_require_active_project` 同口径）——
    # 归档 = 冻结；批量归入会写新 ACTIVE 成员行，等价于用 SEED 标签复活归档。
    if project.status == "ARCHIVED":
        raise HTTPException(
            status_code=409,
            detail="archived project is read-only; unarchive to modify",
        )
    if not payload.device_ids:
        raise HTTPException(status_code=422, detail="device_ids must not be empty")
    _enforce_bulk_limit_http(payload.device_ids, action="assign_project")

    devices = (
        db.query(Device)
        .filter(Device.id.in_(payload.device_ids))
        .all()
    )
    if len(devices) != len(set(payload.device_ids)):
        raise HTTPException(status_code=404, detail="one or more devices not found")

    # ADR-0057 D4 第 8 面：退役设备不接受归属改写（整批 fail-closed，不做部分成功）。
    retired = [d for d in devices if d.retired_at is not None]
    if retired:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "retired devices cannot be re-assigned; unretire them first",
                "device_ids": [d.id for d in retired],
                "serial_numbers": [d.serial for d in retired],
            },
        )

    no_model = [d for d in devices if _blank_to_none(d.model) is None]
    if no_model:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "devices without model cannot be bulk-assigned",
                "device_ids": [d.id for d in no_model],
                "serial_numbers": [d.serial for d in no_model],
            },
        )

    # ADR-0029 v2.5 D10 M3：批量归入 = 为选中设备的型号添加成员行
    # （归属唯一事实源；同型号全部设备随之归入，无逐设备钉住）。
    models = sorted({_blank_to_none(d.model) for d in devices if d.model})
    added = []
    for model in models:
        # #752：与 apply_project_map 对齐——按 lower() 归一匹配（lower 唯一索引
        # 口径），混大小写旧行必须命中，否则 INSERT 撞 uq_project_model_active
        # 未捕获 IntegrityError → 500。
        existing = db.execute(
            select(ProjectModel)
            .where(
                func.lower(ProjectModel.match_value) == func.lower(model),
                ProjectModel.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if existing is not None and existing.project_id != project.id:
            raise HTTPException(
                status_code=409,
                detail=f"model {model} already member of another project",
            )
        if existing is None:
            try:
                db.add(ProjectModel(
                    project_id=project.id,
                    match_value=model,
                    created_by=current_user.id,
                ))
                db.flush()  # 立即触发 lower() 唯一索引检查（IntegrityError 在此抛）
            except IntegrityError:
                # 并发双写最后一道：existing 检查与 INSERT 之间被另一请求抢占
                # 同一归一型号 → 409 而非 500（用户可重试）
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail=f"model {model} concurrently claimed by another project",
                ) from None
            added.append(model)
    if added:
        record_audit(
            db,
            action="bulk_assign_project_models",
            resource_type="test_project",
            resource_id=project.id,
            details={
                "project_key": project.project_key,
                "models": added,
                "device_count": len(devices),
            },
            user_id=current_user.id,
            username=current_user.username,
            request=request,
        )
    db.commit()

    from backend.realtime.socketio_server import emit_project_changed

    emit_project_changed(project.id, "assigned")

    items = []
    for device in devices:
        out = DeviceOut.model_validate(device)
        # 整批归入同一目标；joinedload 缓存的关系在赋值后不自动失效，
        # device 无 project 关系（M3 删列），派生 key 直接写目标
        out.project_key = project.project_key
        out.attribution_source = _attribution_source(
            device.model, True,
        )
        items.append(out)
    return ok(items)


@router.post("/bulk-swipe-trail", response_model=ApiResponse[BulkSwipeTrailOut])
async def bulk_swipe_trail(
    payload: BulkSwipeTrailIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    """对选中设备批量开关滑动留痕（show_touches + pointer_location）。

    Agent 白名单 control；无 host / host 离线计入 skipped；Agent 未连或
    单台 adb 失败计入 failed。
    """
    if not payload.device_ids:
        raise HTTPException(status_code=422, detail="device_ids must not be empty")
    _enforce_bulk_limit_http(payload.device_ids, action="swipe_trail")

    out = await bulk_set_swipe_trail(
        db,
        device_ids=payload.device_ids,
        enabled=payload.enabled,
    )
    record_audit(
        db,
        action="bulk_swipe_trail",
        resource_type="device",
        resource_id=None,
        details={
            "enabled": payload.enabled,
            "device_ids": list(payload.device_ids),
            "ok": out.ok,
            "failed": out.failed,
            "skipped": out.skipped,
        },
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    return ok(out)


@router.post("/bulk-skip-oobe", response_model=ApiResponse[BulkSkipOobeOut])
async def bulk_skip_oobe(
    payload: BulkSkipOobeIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    """对选中设备批量快捷跳过 OOBE（复用脚本库 oobe_skip）。

    控制面选取最新已启用的 oobe_skip 脚本包，按 host_id 并发下发白名单
    Agent control ``skip_device_oobe``；无 host / host 离线 / 设备忙碌计入
    skipped；Agent 未连 / RPC 或脚本执行失败计入 failed。
    """
    if not payload.device_ids:
        raise HTTPException(status_code=422, detail="device_ids must not be empty")
    _enforce_bulk_limit_http(payload.device_ids, action="skip_oobe")

    out = await bulk_skip_device_oobe(
        db,
        device_ids=payload.device_ids,
    )
    record_audit(
        db,
        action="bulk_skip_oobe",
        resource_type="device",
        resource_id=None,
        details={
            "script_version": out.script_version,
            "device_ids": list(payload.device_ids),
            "ok": out.ok,
            "failed": out.failed,
            "skipped": out.skipped,
        },
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    return ok(out)


def _blank_to_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _model_to_project_map(db: Session) -> dict[str, tuple[int, str]]:
    """活跃成员行一次查询：model → (project_id, project_key)（批量预取）。"""
    rows = db.execute(
        select(ProjectModel.project_id, ProjectModel.match_value,
               TestProject.project_key)
        .join(TestProject, TestProject.id == ProjectModel.project_id)
        .where(
            ProjectModel.is_active.is_(True),
        )
    ).all()
    return {match_value: (project_id, project_key)
            for project_id, match_value, project_key in rows}


def _attribution_source(model: Optional[str], mapped: bool) -> str:
    """ADR-0029 v2.5 D10：归属来源两态（mapped / unmapped）。

    派生自 project_model（型号有活跃成员行 = mapped）；无型号设备 /
    型号未映射 = unmapped。归属唯一来源是成员行，无 pinned 例外。
    """
    if not model or not mapped:
        return "unmapped"
    return "mapped"


def _assert_device_writable(device: Device) -> None:
    """ADR-0057 D4 第 8 面：退役设备的管理写路径（标签/项目归属）拒绝写入。

    与主机侧同惯例（`hosts.py` 对退役主机的写路径 409）：退役是账面上的
    「不再使用」，继续改写标签/归属会让退役事实与派生口径漂移；要改先 unretire。
    """
    if device.retired_at is not None:
        raise HTTPException(
            status_code=409,
            detail="device is retired; unretire it before modifying",
        )


def _fill_project_key(device: Device, out, model_to_project: dict[str, int]) -> None:
    """ADR-0029 v2.5：DeviceOut.project_key 派生（F2 口径）+ 归属来源两态。

    model_to_project 由调用方批量预取（活跃成员行一次查询），避免逐设备
    N+1。
    """
    entry = model_to_project.get(device.model) if device.model else None
    out.project_key = entry[1] if entry else None
    out.attribution_source = _attribution_source(
        device.model, entry is not None,
    )


@router.get("", response_model=Union[List[DeviceOut], List[DeviceListItemOut], PaginatedResponse])
def list_devices(
    request: Request,
    q: Optional[str] = Query(
        None,
        max_length=256,
        description="不区分大小写的字面子串搜索（覆盖串号、展示型号、展示 Product、build_display_id、主机展示名、任一标签）",
    ),
    tags: Optional[str] = Query(None, description="Comma-separated tag filter (AND)"),
    has_tags: Optional[bool] = Query(
        None,
        description="标签存在性三态筛选：省略不限、true 非空数组、false 无标签",
    ),
    status: Optional[str] = Query(
        None,
        description="Filter by effective status (ONLINE/BUSY/OFFLINE/ERROR, lowercase aliases, or need_attention)",
    ),
    host_id: Optional[str] = Query(
        None,
        max_length=64,
        description="Exact host_id filter; use 'unassigned' for devices without host (host_id IS NULL)",
    ),
    model: Optional[str] = Query(None, max_length=128, description="Exact device model filter"),
    product: Optional[str] = Query(None, min_length=1, max_length=128, description="Exact ADB product filter"),
    version: Optional[str] = Query(None, max_length=256, description="Exact build_display_id filter"),
    project_key: Optional[str] = Query(None, description="ADR-0029: filter by project key"),
    unassigned: bool = Query(False, description="ADR-0029 P0: only devices with no project (project_id IS NULL)"),
    include_retired: bool = Query(
        False,
        description=(
            "ADR-0038 D5 / ADR-0057 D4：默认隐藏退役主机上的设备**与已退役设备**，"
            "显式置 true 才显示"
        ),
    ),
    include_stale: bool = Query(
        False,
        description=(
            "#2962 A：默认隐藏陈旧设备（OFFLINE 且 last_seen 早于 7 天或为空），"
            "显式置 true 才显示"
        ),
    ),
    sort_by: Optional[str] = Query(
        None,
        description="排序列白名单：serial | host_id（缺省为 host_id NULLS LAST, id）",
    ),
    sort_dir: Optional[str] = Query(
        None,
        description="排序方向：asc | desc（缺省 asc）",
    ),
    projection: Optional[str] = Query(
        None,
        description="响应投影：full（默认兼容 DeviceOut）或 list（有界 DeviceListItemOut，不含无界 extra）",
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=_DEVICE_LIST_MAX_LIMIT),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    # 稳定次序（#3123 / #496）：排序白名单仅允许恒定身份列 {serial, host_id}，
    # 固定 NULLS LAST + Device.id ASC 尾键，严禁引入心跳遥测列避免跨页漂移。
    want_paginated = "skip" in request.query_params or "limit" in request.query_params
    try:
        norm_sort_by, norm_sort_dir = validate_sort_params(sort_by, sort_dir)
        norm_projection = validate_projection(projection)
        now = datetime.now(timezone.utc)
        filters = DeviceQueryFilters(
            q=q,
            status=status,
            host_id=host_id,
            model=model,
            product=product,
            version=version,
            tags=tags,
            has_tags=has_tags,
            project_key=project_key,
            unassigned=unassigned,
            include_stale=include_stale,
            include_retired=include_retired,
        )
        devices, total = query_filtered_devices_page(
            db,
            filters,
            now=now,
            skip=skip,
            limit=limit,
            sort_by=norm_sort_by,
            sort_dir=norm_sort_dir,
            projection=norm_projection,
            include_total=want_paginated,
        )
    except DeviceQueryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    model_to_project = _model_to_project_map(db)
    # #3904：ACTIVE 租约 type/reason 单次批量投影，供首屏徽标分流（零 N+1）。
    lease_map = fetch_active_lease_fields(db, [d.id for d in devices])
    if norm_projection == "list":
        items = [
            serialize_device_list_item(
                d,
                model_to_project,
                now=now,
                active_lease=lease_map.get(d.id),
            )
            for d in devices
        ]
    else:
        items = [
            _serialize_device_readonly(
                d,
                model_to_project,
                now=now,
                active_lease=lease_map.get(d.id),
            )
            for d in devices
        ]
    # 兼容旧接口：未显式传分页参数时返回数组
    if not want_paginated:
        return items
    return PaginatedResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/facets", response_model=ApiResponse[DeviceFacetsOut])
def get_device_facets(
    q: Optional[str] = Query(None, max_length=256),
    tags: Optional[str] = Query(None),
    has_tags: Optional[bool] = Query(None),
    status: Optional[str] = Query(None),
    host_id: Optional[str] = Query(None, max_length=64),
    model: Optional[str] = Query(None, max_length=128),
    product: Optional[str] = Query(None, min_length=1, max_length=128),
    version: Optional[str] = Query(None, max_length=256),
    project_key: Optional[str] = Query(None),
    unassigned: bool = Query(False),
    include_retired: bool = Query(False),
    include_stale: bool = Query(False),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """#496 / #4117 B2-1：物理设备三级作用域 Facets（只读，不含分页与排序参数）。"""
    filters = DeviceQueryFilters(
        q=q,
        status=status,
        host_id=host_id,
        model=model,
        product=product,
        version=version,
        tags=tags,
        has_tags=has_tags,
        project_key=project_key,
        unassigned=unassigned,
        include_stale=include_stale,
        include_retired=include_retired,
    )
    try:
        return ok(build_device_facets(db, filters, now=datetime.now(timezone.utc)))
    except DeviceQueryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.post("/selection-preview", response_model=ApiResponse[DeviceSelectionPreviewOut])
def preview_device_selection(
    payload: DeviceSelectionPreviewIn,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """#496 / #4117 B2-1：跨页批量选择预览（只读，冻结匹配 ID 与影响摘要）。"""
    filters = DeviceQueryFilters(
        q=payload.q,
        status=payload.status,
        host_id=payload.host_id,
        model=payload.model,
        product=payload.product,
        version=payload.version,
        tags=payload.tags,
        has_tags=payload.has_tags,
        project_key=payload.project_key,
        unassigned=payload.unassigned,
        include_stale=payload.include_stale,
        include_retired=payload.include_retired,
    )
    try:
        return ok(
            build_device_selection_preview(
                db,
                filters,
                action=payload.action,
                now=datetime.now(timezone.utc),
            )
        )
    except DeviceQueryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(
    device_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    now = datetime.now(timezone.utc)
    device = (
        db.query(Device)
        .options(joinedload(Device.host))
        .filter(Device.id == device_id)
        .first()
    )
    if not device:
        raise HTTPException(status_code=404, detail="device not found")
    lease_map = fetch_active_lease_fields(db, [device.id])
    return _serialize_device_readonly(
        device,
        _model_to_project_map(db),
        now=now,
        active_lease=lease_map.get(device.id),
    )


@router.get("/{device_id}/return-debts", response_model=ApiResponse[List[DeviceReturnDebtOut]])
def list_device_return_debts_endpoint(
    device_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    # ADR-0061 D5.9 / #3723 U6: open debt rows, oldest first, no evidence.
    if db.get(Device, device_id) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "DEVICE_NOT_FOUND", "message": "设备不存在"},
        )
    rows = open_debts_for_device_sync(db, device_id)
    return ok([
        DeviceReturnDebtOut(
            id=row.id,
            family=row.family,
            source_kind=row.source_kind,
            source_job_id=row.source_job_id,
            cause=row.cause,
            created_at=row.created_at,
        )
        for row in rows
    ])


@router.put("/{device_id}/tags", response_model=DeviceOut)
def update_device_tags(
    device_id: int,
    tags: List[str],
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    """Update device tags."""
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device not found")
    _assert_device_writable(device)
    device.tags = tags
    record_audit(
        db,
        action="update_tags",
        resource_type="device",
        resource_id=device.id,
        details={"serial": device.serial, "tags": tags},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(device)
    return device


def _device_out(device: Device, db: Session) -> DeviceOut:
    out = DeviceOut.model_validate(device)
    _fill_project_key(device, out, _model_to_project_map(db))
    out.usb_presence = usb_presence_for(device.host, device.serial)
    return out


@router.post(
    "/{device_id}/leases/{lease_id}/release",
    response_model=ApiResponse[DeviceLeaseReleaseOut],
)
def release_device_lease_endpoint(
    device_id: int,
    lease_id: int,
    payload: DeviceLeaseReleaseIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """#3646：释放一条终态 job 的 ACTIVE JOB 租约（admin）。不改 job / PlanRun。"""
    released = release_terminal_job_lease(
        db,
        device_id=device_id,
        lease_id=lease_id,
        reason=payload.reason,
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    return ok(DeviceLeaseReleaseOut(
        lease_id=released.lease_id,
        device_id=released.device_id,
        job_id=released.job_id,
        status=released.status,
    ))




@router.post(
    "/{device_id}/return-override",
    response_model=ApiResponse[DeviceReturnOverrideOut],
)
def return_override_endpoint(
    device_id: int,
    payload: DeviceReturnOverrideIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """ADR-0061 D5.7 / #3723 U3：人工放行未关闭欠账（admin）。记 unverified/override。"""
    result = apply_override_sync(
        db,
        device_id=device_id,
        families=payload.families,
        reason_category=payload.reason_category,
        evidence_ref=payload.evidence_ref,
    )
    if not result.get("ok"):
        raise HTTPException(
            status_code=404,
            detail={"code": result.get("code", "DEVICE_NOT_FOUND"), "message": "设备不存在"},
        )
    record_audit(
        db,
        action="device_return_override",
        resource_type="device",
        resource_id=device_id,
        details={
            "families": result["families"],
            "reason_category": payload.reason_category,
            "evidence_ref": payload.evidence_ref,
            "closed_count": result["closed_count"],
            "debt_lease_released": result["debt_lease_released"],
        },
        user_id=current_user.id,
        username=current_user.username,
        request=request,
        strict=True,
    )
    record_device_return_override()
    db.commit()
    return ok(DeviceReturnOverrideOut(
        device_id=device_id,
        families=result["families"],
        closed_count=result["closed_count"],
        debt_lease_released=result["debt_lease_released"],
        open_debt_count=result["open_debt_count"],
    ))

@router.post("/{device_id}/retire", response_model=DeviceOut)
def retire_device_endpoint(
    device_id: int,
    payload: DeviceRetireIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """ADR-0057 D2：设备退役（admin + 审计；幂等）。

    前置（E2）：设备**无活跃 Job、无 ACTIVE 租约**，否则 409——退役是账面动作，
    不顺带中止在跑的测试。退役不改写 `status`（心跳所有）、不删历史行；要恢复
    走 `POST /devices/{id}/unretire`。重复调用返回当前退役态（不重复审计）。
    """
    device = retire_device(
        db,
        device_id=device_id,
        reason=payload.retire_reason,
        actor_id=current_user.id,
        actor_username=current_user.username,
        request=request,
    )
    return _device_out(device, db)


@router.post("/{device_id}/unretire", response_model=DeviceOut)
def unretire_device_endpoint(
    device_id: int,
    payload: DeviceUnretireIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """ADR-0057 D2：解除退役（admin + 审计；无前置；幂等）。

    写回语义：清 `retired_at`（八面收口随之恢复）；`retired_by`/`retire_reason`
    保留为最近一次退役痕迹。重复调用返回当前在役态。
    """
    device = unretire_device(
        db,
        device_id=device_id,
        reason=payload.retire_reason,
        actor_id=current_user.id,
        actor_username=current_user.username,
        request=request,
    )
    return _device_out(device, db)


@router.post("/{device_id}/rebind", response_model=ApiResponse[DeviceOut])
def rebind_device_endpoint(
    device_id: int,
    payload: DeviceRebindIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """#3892：设备改绑（admin + 审计；幂等）。

    把设备账面 `host_id` 改到目标主机——补齐 powered_off 设备无法靠心跳改绑的
    人工路径（vbus 断电刷机「先改绑再刷」的前置）。前置：设备在役、目标主机
    存在且在役、设备无活跃 Job / 无 ACTIVE 租约，否则 409/404。只改 `host_id`，
    不改写 `status`（心跳所有）。目标即当前归属时原样返回（不重复审计）。
    """
    device = rebind_device(
        db,
        device_id=device_id,
        target_host_id=payload.host_id,
        reason=payload.reason,
        actor_id=current_user.id,
        actor_username=current_user.username,
        request=request,
    )
    return ok(_device_out(device, db))


@router.post("/retire", response_model=ApiResponse[DeviceRetireBatchOut])
def retire_devices_endpoint(
    payload: DeviceRetireBatchIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """ADR-0057 D2/E3：批量退役（admin + 逐台审计）。

    逐台独立事务，**任一台失败不影响其他台**（E2 前置不满足的那台记 `conflict`）；
    返回逐台结果与计数。E3：清单由陈旧度视图导出，人工确认后走本入口。
    """
    if not payload.device_ids:
        raise HTTPException(status_code=422, detail="device_ids must not be empty")
    _enforce_bulk_limit_http(payload.device_ids, action="retire")
    results = retire_devices_batch(
        db,
        device_ids=payload.device_ids,
        reason=payload.retire_reason,
        actor_id=current_user.id,
        actor_username=current_user.username,
        request=request,
    )
    counts = {"retired": 0, "already_retired": 0, "conflict": 0, "not_found": 0, "failed": 0}
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return ok(DeviceRetireBatchOut(
        results=[DeviceRetireBatchResult(**item) for item in results],
        **counts,
    ))
