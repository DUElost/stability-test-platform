from datetime import datetime, timedelta, timezone
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import cast, or_, select
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.orm import Session
from typing import List, Optional, Union

from backend.core.database import get_db
from backend.core.audit import record_audit
from backend.models.host import Host, Device
from backend.models.project import TestProject
from backend.models.project_model import ProjectModel
from backend.api.schemas import DeviceCreate, DeviceOut, PaginatedResponse
from backend.api.response import ApiResponse, ok
from backend.api.schemas.device import BulkProjectAssignIn
from backend.api.routes.auth import get_current_active_user, require_admin, User

logger = logging.getLogger(__name__)

# Host heartbeat timeout config (default 5 minutes)
HOST_HEARTBEAT_TIMEOUT_SECONDS = int(os.getenv("HOST_HEARTBEAT_TIMEOUT_SECONDS", "300"))


def _ensure_host_online_for_device(device: Device) -> bool:
    """Mark device as OFFLINE if its host is OFFLINE or heartbeat has expired.
    Returns True if device status was changed, False otherwise.
    """
    host = device.host
    if not host:
        return False

    # Check if host is offline
    if host.status != "ONLINE":
        if device.status != "OFFLINE":
            device.status = "OFFLINE"
            logger.info(
                "device_offline_by_host_status",
                extra={
                    "device_id": device.id,
                    "device_serial": device.serial,
                    "host_id": host.id,
                    "host_status": host.status,
                },
            )
            return True
        return False

    # Check host heartbeat timeout
    now = datetime.now(timezone.utc)
    offline_deadline = now - timedelta(seconds=HOST_HEARTBEAT_TIMEOUT_SECONDS)
    last_heartbeat = host.last_heartbeat
    if last_heartbeat and last_heartbeat.tzinfo is None:
        # 兼容历史/测试数据中的 naive 时间
        last_heartbeat = last_heartbeat.replace(tzinfo=timezone.utc)

    if last_heartbeat is None or last_heartbeat < offline_deadline:
        if device.status != "OFFLINE":
            device.status = "OFFLINE"
            host.status = "OFFLINE"
            logger.info(
                "device_offline_by_host_heartbeat_timeout",
                extra={
                    "device_id": device.id,
                    "device_serial": device.serial,
                    "host_id": host.id,
                    "host_last_heartbeat": last_heartbeat.isoformat() if last_heartbeat else None,
                },
            )
            return True
        return False

    return False


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
    if not payload.device_ids:
        raise HTTPException(status_code=422, detail="device_ids must not be empty")

    devices = (
        db.query(Device)
        .filter(Device.id.in_(payload.device_ids))
        .all()
    )
    if len(devices) != len(set(payload.device_ids)):
        raise HTTPException(status_code=404, detail="one or more devices not found")

    # ADR-0029 v2.5 D10 M3：批量归入 = 为选中设备的型号添加成员行
    # （归属唯一事实源；同型号全部设备随之归入，无逐设备钉住）。
    from backend.models.project_model import ProjectModel

    models = sorted({_blank_to_none(d.model) for d in devices if d.model})
    added = []
    for model in models:
        existing = db.execute(
            select(ProjectModel)
            .where(
                                ProjectModel.match_value == model,
                ProjectModel.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if existing is not None and existing.project_id != project.id:
            raise HTTPException(
                status_code=409,
                detail=f"model {model} already member of another project",
            )
        if existing is None:
            db.add(ProjectModel(
                project_id=project.id,
                match_value=model,
                created_by=current_user.id,
            ))
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
        # 直接写目标 key（不读 device.project）
        out.project_key = project.project_key
        out.attribution_source = _attribution_source(
            device.model, True,
        )
        items.append(out)
    return ok(items)


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
    型号未映射 = unmapped。pinned 随 device.project_id 副本删除（M3）。
    """
    if not model or not mapped:
        return "unmapped"
    return "mapped"


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


@router.get("", response_model=Union[List[DeviceOut], PaginatedResponse])
def list_devices(
    request: Request,
    tags: Optional[str] = Query(None, description="Comma-separated tag filter"),
    status: Optional[str] = Query(None, description="Filter by device status (ONLINE, OFFLINE, BUSY)"),
    project_key: Optional[str] = Query(None, description="ADR-0029: filter by project key"),
    unassigned: bool = Query(False, description="ADR-0029 P0: only devices with no project (project_id IS NULL)"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=1200),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    # 稳定次序：last_seen 相同（尤其是全 NULL 的新设备）时 PG 不保证顺序，
    # 追加 Device.id 作 tie-breaker，保证分页与前端列表顺序可复现（#537）
    query = (
        db.query(Device)
        .order_by(Device.last_seen.desc().nullslast(), Device.id.asc())
    )

    # ADR-0029 P0：未归属筛选——与 project_key 互斥（「某项目里未归属」无意义）；
    # 参数组合错误优先于 key 存在性校验
    if unassigned and project_key:
        raise HTTPException(
            status_code=400,
            detail="unassigned and project_key are mutually exclusive",
        )

    # ADR-0029 v2.5：归属筛选派生（device.model ⋈ project_model 活跃成员行）
    if project_key:
        # 未知 key 一律 404（与 projects 路由同语义；防「拼错 key 静默空列表」）
        project = db.query(TestProject).filter(TestProject.project_key == project_key).first()
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        query = query.join(ProjectModel, Device.model == ProjectModel.match_value) \
                     .filter(
                         ProjectModel.project_id == project.id,
                                                  ProjectModel.is_active.is_(True),
                     )

    if unassigned:
        mapped_models = select(ProjectModel.match_value).where(
                        ProjectModel.is_active.is_(True),
        )
        query = query.filter(
            or_(Device.model.is_(None), ~Device.model.in_(mapped_models))
        )

    # Filter by status if provided
    if status:
        query = query.filter(Device.status == status)

    # Filter by tags using JSONB @> operator
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        for tag in tag_list:
            query = query.filter(Device.tags.op('@>')(cast(json.dumps([tag]), PG_JSONB)))

    total = query.count()
    devices = query.offset(skip).limit(limit).all()
    # Update device status based on host status
    needs_commit = False
    for device in devices:
        if _ensure_host_online_for_device(device):
            needs_commit = True
    if needs_commit:
        db.commit()
    model_to_project = _model_to_project_map(db)
    items = []
    for d in devices:
        out = DeviceOut.model_validate(d)
        _fill_project_key(d, out, model_to_project)
        items.append(out)
    # 兼容旧接口：未显式传分页参数时返回数组
    if "skip" not in request.query_params and "limit" not in request.query_params:
        return items
    return PaginatedResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(
    device_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device not found")
    if _ensure_host_online_for_device(device):
        db.commit()
    out = DeviceOut.model_validate(device)
    _fill_project_key(device, out, _model_to_project_map(db))
    return out


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
