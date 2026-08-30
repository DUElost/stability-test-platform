from datetime import datetime, timedelta, timezone
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import cast
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional, Union

from backend.core.database import get_db
from backend.core.audit import record_audit
from backend.models.host import Host, Device
from backend.models.project import TestProject
from backend.services.project_attribution import resolve_project_id
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
        .options(joinedload(Device.project))
        .all()
    )
    if len(devices) != len(set(payload.device_ids)):
        raise HTTPException(status_code=404, detail="one or more devices not found")

    for device in devices:
        if device.project_id == project.id and device.project_pinned:
            continue
        from_key = device.project.project_key if device.project else None
        device.project_id = project.id
        # ADR-0029 P1：批量归入 = 人工钉住（命令式例外路径，规则不覆盖）
        device.project_pinned = True
        record_audit(
            db,
            action="assign_project",
            resource_type="device",
            resource_id=device.id,  # id 已存在，无需 flush 取号
            details={
                "project_key": project.project_key,
                "from_project_key": from_key,
            },
            user_id=current_user.id,
            username=current_user.username,
            request=request,
        )
    # 全部 UPDATE + audit 累积后单次 flush+commit（545 台批量归属不逐台往返）
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
            db, project, device.model, pinned=device.project_pinned,
        )
        items.append(out)
    return ok(items)


def _attribution_source(
    db: Session, project, model: Optional[str], *, pinned: bool = False,
) -> str:
    """ADR-0029 归属来源派生（pinned / rule / manual / unassigned）。

    无项目 → unassigned；project_pinned（人工钉住，规则不覆盖）→ pinned；
    型号命中项目活跃规则（project_model，精确匹配）→ rule；其余
    （人工批量归入、SEED 回填、型号不在规则）→ manual。
    """
    if project is None:
        return "unassigned"
    if pinned:
        return "pinned"
    if model and resolve_project_id(db, model) == project.id:
        return "rule"
    return "manual"


def _fill_project_key(db: Session, device: Device, out) -> None:
    """ADR-0029：DeviceOut.project_key（F2 口径，不暴露数字 project_id）+ 归属来源。"""
    project = device.project
    out.project_key = project.project_key if project else None
    out.attribution_source = _attribution_source(
        db, project, device.model, pinned=device.project_pinned,
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
        .options(joinedload(Device.project))
        .order_by(Device.last_seen.desc().nullslast(), Device.id.asc())
    )

    # ADR-0029 P0：未归属筛选——与 project_key 互斥（「某项目里未归属」无意义）；
    # 参数组合错误优先于 key 存在性校验
    if unassigned and project_key:
        raise HTTPException(
            status_code=400,
            detail="unassigned and project_key are mutually exclusive",
        )

    # ADR-0029：项目归属筛选（project_id NULL 的设备不命中——筛选 = 只显示该项目）
    if project_key:
        # 未知 key 一律 404（与 projects 路由同语义；防「拼错 key 静默空列表」）
        if db.query(TestProject).filter(TestProject.project_key == project_key).first() is None:
            raise HTTPException(status_code=404, detail="project not found")
        query = query.join(TestProject, Device.project_id == TestProject.id) \
                     .filter(TestProject.project_key == project_key)

    if unassigned:
        query = query.filter(Device.project_id.is_(None))

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
    items = []
    for d in devices:
        out = DeviceOut.model_validate(d)
        _fill_project_key(db, d, out)
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
    _fill_project_key(db, device, out)
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
