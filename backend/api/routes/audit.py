# -*- coding: utf-8 -*-
"""
Audit Log API — admin-only read access to audit trail.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.routes.auth import require_admin, User
from backend.api.schemas import AuditLogOut, PaginatedResponse
from backend.core.database import get_async_db
from backend.models.audit import AuditLog

router = APIRouter(prefix="/api/v1/audit-logs", tags=["audit"])


def _apply_audit_filters(
    stmt,
    *,
    resource_type: Optional[str],
    action: Optional[str],
    user_id: Optional[int],
    start_time: Optional[datetime],
    end_time: Optional[datetime],
):
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if start_time:
        stmt = stmt.where(AuditLog.timestamp >= start_time)
    if end_time:
        stmt = stmt.where(AuditLog.timestamp <= end_time)
    return stmt


@router.get("", response_model=PaginatedResponse)
async def list_audit_logs(
    resource_type: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    start_time: Optional[datetime] = Query(None),
    end_time: Optional[datetime] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_admin),
):
    """List audit log entries (admin-only, paginated)."""
    base = _apply_audit_filters(
        select(AuditLog),
        resource_type=resource_type,
        action=action,
        user_id=user_id,
        start_time=start_time,
        end_time=end_time,
    )

    try:
        total = int(
            (
                await db.execute(
                    select(func.count()).select_from(base.subquery())
                )
            ).scalar_one()
        )
        rows = (
            await db.execute(
                base.order_by(AuditLog.timestamp.desc()).offset(skip).limit(limit)
            )
        ).scalars().all()
    except ProgrammingError as exc:
        # 兼容尚未创建 audit_logs 的环境：返回空结果而不是 500
        message = str(exc)
        if "audit_logs" not in message or (
            "does not exist" not in message.lower()
            and "undefinedtable" not in message.lower()
            and "不存在" not in message
        ):
            raise
        await db.rollback()
        return PaginatedResponse(items=[], total=0, skip=skip, limit=limit)

    items = [AuditLogOut.model_validate(r) for r in rows]
    return PaginatedResponse(items=items, total=total, skip=skip, limit=limit)
