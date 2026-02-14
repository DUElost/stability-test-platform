from datetime import datetime, timedelta
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from backend.core.database import get_db
from backend.models.schemas import Device, DeviceStatus, Host, HostStatus
from backend.api.schemas import DeviceCreate, DeviceOut
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
    if host.status != HostStatus.ONLINE:
        if device.status != DeviceStatus.OFFLINE:
            device.status = DeviceStatus.OFFLINE
            logger.info(
                "device_offline_by_host_status",
                extra={
                    "device_id": device.id,
                    "device_serial": device.serial,
                    "host_id": host.id,
                    "host_status": host.status.value,
                },
            )
            return True
        return False

    # Check host heartbeat timeout
    now = datetime.utcnow()
    offline_deadline = now - timedelta(seconds=HOST_HEARTBEAT_TIMEOUT_SECONDS)

    if host.last_heartbeat is None or host.last_heartbeat < offline_deadline:
        if device.status != DeviceStatus.OFFLINE:
            device.status = DeviceStatus.OFFLINE
            host.status = HostStatus.OFFLINE
            logger.info(
                "device_offline_by_host_heartbeat_timeout",
                extra={
                    "device_id": device.id,
                    "device_serial": device.serial,
                    "host_id": host.id,
                    "host_last_heartbeat": host.last_heartbeat.isoformat() if host.last_heartbeat else None,
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


@router.get("", response_model=List[DeviceOut])
def list_devices(db: Session = Depends(get_db)):
    devices = db.query(Device).order_by(Device.id).all()
    # Update device status based on host status
    needs_commit = False
    for device in devices:
        if _ensure_host_online_for_device(device):
            needs_commit = True
    if needs_commit:
        db.commit()
    return devices


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(device_id: int, db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device not found")
    if _ensure_host_online_for_device(device):
        db.commit()
    return device
