from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from ...core.database import get_db
from ...models.schemas import Host
from ..schemas import HostCreate, HostOut

router = APIRouter(prefix="/api/v1/hosts", tags=["hosts"])


@router.post("", response_model=HostOut)
def create_host(payload: HostCreate, db: Session = Depends(get_db)):
    host = Host(
        name=payload.name,
        ip=payload.ip,
        ssh_port=payload.ssh_port,
        ssh_user=payload.ssh_user,
        ssh_auth_type=payload.ssh_auth_type,
        ssh_key_path=payload.ssh_key_path,
    )
    db.add(host)
    db.commit()
    db.refresh(host)
    return host


@router.get("", response_model=List[HostOut])
def list_hosts(db: Session = Depends(get_db)):
    return db.query(Host).order_by(Host.id).all()


@router.get("/{host_id}", response_model=HostOut)
def get_host(host_id: int, db: Session = Depends(get_db)):
    host = db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="host not found")
    return host
