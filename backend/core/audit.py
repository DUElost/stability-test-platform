# -*- coding: utf-8 -*-
"""Audit logging helper for tracking mutation operations."""

import logging
from typing import Any, Dict, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from backend.models.schemas import AuditLog

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
) -> AuditLog:
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
    db.add(entry)
    # Flush so the caller's commit will include the audit row
    db.flush()
    logger.info(
        "audit: %s %s/%s by %s",
        action,
        resource_type,
        resource_id,
        username or user_id or "anonymous",
    )
    return entry
