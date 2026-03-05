from datetime import datetime, timedelta, timezone
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import cast
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.orm import Session
from typing import Any, List, Optional

from backend.core.database import get_db
from backend.models.host import Host, Device
from backend.api.schemas import DeviceCreate, DeviceOut, PaginatedResponse
from backend.api.routes.auth import get_current_active_user, User

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
def create_device(payload: DeviceCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
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
    db.commit()
    db.refresh(device)
    return device


@router.get("", response_model=Any)
def list_devices(
    request: Request,
    tags: Optional[str] = Query(None, description="Comma-separated tag filter"),
    status: Optional[str] = Query(None, description="Filter by device status (ONLINE, OFFLINE, BUSY)"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(Device).order_by(Device.id)

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
    items = [
        DeviceOut.model_validate(d) if hasattr(DeviceOut, "model_validate") else DeviceOut.from_orm(d)
        for d in devices
    ]
    # 兼容旧接口：未显式传分页参数时返回数组
    if "skip" not in request.query_params and "limit" not in request.query_params:
        return items
    return PaginatedResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(device_id: int, db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device not found")
    if _ensure_host_online_for_device(device):
        db.commit()
    return device


@router.put("/{device_id}/tags", response_model=DeviceOut)
def update_device_tags(
    device_id: int,
    tags: List[str],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Update device tags."""
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device not found")
    device.tags = tags
    db.commit()
    db.refresh(device)
    return device
