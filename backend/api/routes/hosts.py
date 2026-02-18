from datetime import datetime, timedelta
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List

from backend.core.database import get_db
from backend.models.schemas import Host, HostStatus
from backend.api.schemas import HostCreate, HostOut, PaginatedResponse
from backend.api.routes.auth import get_current_active_user, User

logger = logging.getLogger(__name__)

# Host heartbeat timeout config (default 5 minutes)
HOST_HEARTBEAT_TIMEOUT_SECONDS = int(os.getenv("HOST_HEARTBEAT_TIMEOUT_SECONDS", "300"))


def _ensure_host_status_up_to_date(host: Host) -> bool:
    """Update host status to OFFLINE if heartbeat has expired.
    Returns True if status was changed, False otherwise.
    """
    if host.status != HostStatus.ONLINE:
        return False

    now = datetime.utcnow()
    offline_deadline = now - timedelta(seconds=HOST_HEARTBEAT_TIMEOUT_SECONDS)

    if host.last_heartbeat is None or host.last_heartbeat < offline_deadline:
        host.status = HostStatus.OFFLINE
        logger.info(
            "host_status_marked_offline",
            extra={
                "host_id": host.id,
                "host_name": host.name,
                "last_heartbeat": host.last_heartbeat.isoformat() if host.last_heartbeat else None,
            },
        )
        return True
    return False

router = APIRouter(prefix="/api/v1/hosts", tags=["hosts"])


@router.post("", response_model=HostOut)
def create_host(payload: HostCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
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


@router.get("", response_model=PaginatedResponse)
def list_hosts(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(Host).order_by(Host.id)
    total = query.count()
    hosts = query.offset(skip).limit(limit).all()
    # Update status for hosts with expired heartbeat
    needs_commit = False
    for host in hosts:
        if _ensure_host_status_up_to_date(host):
            needs_commit = True
    if needs_commit:
        db.commit()
    items = [
        HostOut.model_validate(h) if hasattr(HostOut, "model_validate") else HostOut.from_orm(h)
        for h in hosts
    ]
    return PaginatedResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/{host_id}", response_model=HostOut)
def get_host(host_id: int, db: Session = Depends(get_db)):
    host = db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="host not found")
    if _ensure_host_status_up_to_date(host):
        db.commit()
    return host


@router.put("/{host_id}", response_model=HostOut)
def update_host(
    host_id: int,
    payload: HostCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新主机信息"""
    host = db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="host not found")

    host.name = payload.name
    host.ip = payload.ip
    host.ssh_port = payload.ssh_port
    host.ssh_user = payload.ssh_user
    host.ssh_auth_type = payload.ssh_auth_type
    host.ssh_key_path = payload.ssh_key_path

    db.commit()
    db.refresh(host)
    return host
