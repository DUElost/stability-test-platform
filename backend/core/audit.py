# -*- coding: utf-8 -*-
"""Audit logging helper for tracking mutation operations."""

import logging
from typing import Any, Dict, Optional

from fastapi import Request
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session
from sqlalchemy.orm.session import object_session

from backend.models.audit import AuditLog

logger = logging.getLogger(__name__)


def record_audit(
    db: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: Optional[int] = None,
    details: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    request: Optional[Request] = None,
) -> Optional[AuditLog]:
    """Record an audit log entry for a mutation operation."""
    ip_address = None
    if request:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip_address = forwarded.split(",")[0].strip()
        elif request.client:
            ip_address = request.client.host

    entry = AuditLog(
        user_id=user_id,
        username=username,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
        ip_address=ip_address,
    )
    try:
        # 使用 savepoint 包裹审计写入，避免审计失败污染主业务事务
        with db.begin_nested():
            db.add(entry)
            db.flush()
    except (ProgrammingError, OperationalError) as exc:
        message = str(exc)
        is_missing_audit_table = (
            "audit_logs" in message
            and (
                "does not exist" in message.lower()
                or "undefinedtable" in message.lower()
                or "不存在" in message
            )
        )
        if not is_missing_audit_table:
            raise

        # 缺少 audit_logs 表时降级：仅记录告警，不阻塞主流程
        if object_session(entry) is db:
            db.expunge(entry)
        logger.warning(
            "audit_logs_missing_skip: %s %s/%s by %s",
            action,
            resource_type,
            resource_id,
            username or user_id or "anonymous",
        )
        return None

    logger.info(
        "audit: %s %s/%s by %s",
        action,
        resource_type,
        resource_id,
        username or user_id or "anonymous",
    )
    return entry
