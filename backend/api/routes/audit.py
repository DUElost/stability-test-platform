# -*- coding: utf-8 -*-
"""
Audit Log API — admin-only read access to audit trail.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.api.routes.auth import require_admin, User
from backend.api.schemas import AuditLogOut, PaginatedResponse
from backend.core.database import get_db
from backend.models.schemas import AuditLog

router = APIRouter(prefix="/api/v1/audit-logs", tags=["audit"])


@router.get("", response_model=PaginatedResponse)
def list_audit_logs(
    resource_type: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    start_time: Optional[datetime] = Query(None),
    end_time: Optional[datetime] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """List audit log entries (admin-only, paginated)."""
    query = db.query(AuditLog)
    if resource_type:
        query = query.filter(AuditLog.resource_type == resource_type)
    if action:
        query = query.filter(AuditLog.action == action)
    if user_id is not None:
        query = query.filter(AuditLog.user_id == user_id)
    if start_time:
        query = query.filter(AuditLog.timestamp >= start_time)
    if end_time:
        query = query.filter(AuditLog.timestamp <= end_time)

    total = query.count()
    rows = query.order_by(AuditLog.timestamp.desc()).offset(skip).limit(limit).all()
    items = [
        AuditLogOut.model_validate(r) if hasattr(AuditLogOut, "model_validate") else AuditLogOut.from_orm(r)
        for r in rows
    ]
    return PaginatedResponse(items=items, total=total, skip=skip, limit=limit)
