from datetime import datetime, timedelta, timezone
import logging
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from typing import Any, List

from backend.core.database import get_db
from backend.core.audit import record_audit
from backend.models.host import Host
from backend.api.schemas import HostCreate, HostOut, PaginatedResponse
from backend.api.routes.auth import get_current_active_user, User

logger = logging.getLogger(__name__)

# Host heartbeat timeout config (default 5 minutes)
HOST_HEARTBEAT_TIMEOUT_SECONDS = int(os.getenv("HOST_HEARTBEAT_TIMEOUT_SECONDS", "300"))


def _ensure_host_status_up_to_date(host: Host) -> bool:
    """Update host status to OFFLINE if heartbeat has expired.
    Returns True if status was changed, False otherwise.
    """
    if host.status != "ONLINE":
        return False

    now = datetime.now(timezone.utc)
    offline_deadline = now - timedelta(seconds=HOST_HEARTBEAT_TIMEOUT_SECONDS)
    last_heartbeat = host.last_heartbeat
    if last_heartbeat and last_heartbeat.tzinfo is None:
        # 兼容历史/测试数据中的 naive 时间
        last_heartbeat = last_heartbeat.replace(tzinfo=timezone.utc)

    if last_heartbeat is None or last_heartbeat < offline_deadline:
        host.status = "OFFLINE"
        logger.info(
            "host_status_marked_offline",
            extra={
                "host_id": host.id,
                "host_name": host.name,
                "last_heartbeat": last_heartbeat.isoformat() if last_heartbeat else None,
            },
        )
        return True
    return False

def _host_to_out(h: Host) -> HostOut:
    """从 ORM 对象构造 HostOut，从 host.extra 中提取 capacity/health。

    不能仅靠 HostOut.model_validate(h) —— Pydantic 不会自动从 JSON 列
    的嵌套 key 映射到顶层字段。此 helper 在 validate 后补充。
    """
    out = HostOut.model_validate(h) if hasattr(HostOut, "model_validate") else HostOut.from_orm(h)
    extra = h.extra or {}
    out.capacity = extra.get("capacity")
    out.health = extra.get("health")
    out.max_concurrent_jobs = h.max_concurrent_jobs
    return out


router = APIRouter(prefix="/api/v1/hosts", tags=["hosts"])


@router.post("", response_model=HostOut)
def create_host(payload: HostCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user), request: Request = None):
    host_id = str(uuid.uuid4())
    host = Host(
        id=host_id,
        hostname=payload.name,
        name=payload.name,
        ip=payload.ip,
        ip_address=payload.ip,
        ssh_port=payload.ssh_port,
        ssh_user=payload.ssh_user,
        ssh_auth_type=payload.ssh_auth_type,
        ssh_key_path=payload.ssh_key_path,
    )
    db.add(host)
    db.flush()
    record_audit(
        db,
        action="create",
        resource_type="host",
        resource_id=host.id,
        details={"name": host.name, "ip": host.ip,
                 "ssh_port": host.ssh_port, "ssh_auth_type": host.ssh_auth_type},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(host)
    return _host_to_out(host)


@router.get("", response_model=Any)
def list_hosts(
    request: Request,
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
    items = [_host_to_out(h) for h in hosts]
    # 兼容旧接口：未显式传分页参数时返回数组
    if "skip" not in request.query_params and "limit" not in request.query_params:
        return items
    return PaginatedResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/{host_id}", response_model=HostOut)
def get_host(host_id: str, db: Session = Depends(get_db)):
    host = db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="host not found")
    if _ensure_host_status_up_to_date(host):
        db.commit()
    return _host_to_out(host)


@router.put("/{host_id}", response_model=HostOut)
def update_host(
    host_id: str,
    payload: HostCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    """更新主机信息"""
    host = db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="host not found")

    host.name = payload.name
    host.hostname = payload.name
    host.ip = payload.ip
    host.ip_address = payload.ip
    host.ssh_port = payload.ssh_port
    host.ssh_user = payload.ssh_user
    host.ssh_auth_type = payload.ssh_auth_type
    host.ssh_key_path = payload.ssh_key_path

    record_audit(
        db,
        action="update",
        resource_type="host",
        resource_id=host.id,
        details={"name": host.name, "ip": host.ip},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(host)
    return _host_to_out(host)
