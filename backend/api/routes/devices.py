from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from ...core.database import get_db
from ...models.schemas import Device
from ..schemas import DeviceCreate, DeviceOut

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


@router.post("", response_model=DeviceOut)
def create_device(payload: DeviceCreate, db: Session = Depends(get_db)):
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
    return db.query(Device).order_by(Device.id).all()


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(device_id: int, db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device not found")
    return device
