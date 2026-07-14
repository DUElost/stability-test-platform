# -*- coding: utf-8 -*-
"""
Notifications API — CRUD for channels and alert rules.
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.api.schemas import (
    AlertRuleCreate,
    AlertRuleOut,
    AlertRuleUpdate,
    NotificationChannelCreate,
    NotificationChannelOut,
    NotificationChannelUpdate,
    PaginatedResponse,
)
from backend.core.database import get_db
from backend.core.audit import record_audit
from backend.models.notification import AlertRule, ChannelType, EventType, NotificationChannel, NotificationLog, NotificationSource
from backend.api.routes.auth import require_admin, User

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------

@router.get("/channels", response_model=PaginatedResponse)
def list_channels(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    query = db.query(NotificationChannel).order_by(NotificationChannel.id)
    total = query.count()
    rows = query.offset(skip).limit(limit).all()
    items = [NotificationChannelOut.model_validate(r) for r in rows]
    return PaginatedResponse(items=items, total=total, skip=skip, limit=limit)


@router.post("/channels", response_model=NotificationChannelOut)
def create_channel(
    body: NotificationChannelCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    channel = NotificationChannel(
        name=body.name,
        type=ChannelType(body.type),
        config=body.config,
        enabled=body.enabled,
    )
    db.add(channel)
    db.flush()
    record_audit(
        db,
        action="create",
        resource_type="notification_channel",
        resource_id=channel.id,
        details={"name": channel.name, "type": channel.type, "enabled": channel.enabled},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(channel)
    return channel


@router.put("/channels/{channel_id}", response_model=NotificationChannelOut)
def update_channel(
    channel_id: int,
    body: NotificationChannelUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    channel = db.get(NotificationChannel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    if body.name is not None:
        channel.name = body.name
    if body.type is not None:
        channel.type = ChannelType(body.type)
    if body.config is not None:
        channel.config = body.config
    if body.enabled is not None:
        channel.enabled = body.enabled
    record_audit(
        db,
        action="update",
        resource_type="notification_channel",
        resource_id=channel.id,
        details={"name": channel.name, "type": channel.type, "enabled": channel.enabled},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(channel)
    return channel


@router.delete("/channels/{channel_id}")
def delete_channel(
    channel_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    channel = db.get(NotificationChannel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    ch_name = channel.name
    ch_type = channel.type
    rules_count = db.query(AlertRule).filter(AlertRule.channel_id == channel_id).count()
    # Cascade: delete associated rules first
    db.query(AlertRule).filter(AlertRule.channel_id == channel_id).delete()
    db.delete(channel)
    record_audit(
        db,
        action="delete",
        resource_type="notification_channel",
        resource_id=channel_id,
        details={"name": ch_name, "type": ch_type, "rules_deleted_count": rules_count},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    return {"ok": True}


@router.post("/channels/{channel_id}/test")
def test_channel(
    channel_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Send a test notification through the channel."""
    channel = db.get(NotificationChannel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    from backend.services.notification_service import send_to_channel

    try:
        send_to_channel(channel, "This is a test notification from Stability Test Platform.")
        return {"ok": True, "message": "Test notification sent"}
    except Exception as exc:
        logger.warning("test_channel_failed: channel_id=%s err=%s", channel_id, exc)
        raise HTTPException(status_code=502, detail=f"Send failed: {exc}")


# ---------------------------------------------------------------------------
# Alert Rules
# ---------------------------------------------------------------------------

@router.get("/rules", response_model=PaginatedResponse)
def list_rules(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    base = db.query(AlertRule).order_by(AlertRule.id)
    total = base.count()
    rules = base.offset(skip).limit(limit).all()
    result = []
    for rule in rules:
        out = AlertRuleOut.model_validate(rule)
        if rule.channel:
            out.channel_name = rule.channel.name
        result.append(out)
    return PaginatedResponse(items=result, total=total, skip=skip, limit=limit)


@router.post("/rules", response_model=AlertRuleOut)
def create_rule(
    body: AlertRuleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    # Validate channel exists
    channel = db.get(NotificationChannel, body.channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    rule = AlertRule(
        name=body.name,
        event_type=EventType(body.event_type),
        channel_id=body.channel_id,
        filters=body.filters,
        enabled=body.enabled,
    )
    db.add(rule)
    db.flush()
    record_audit(
        db,
        action="create",
        resource_type="notification_rule",
        resource_id=rule.id,
        details={"name": rule.name, "event_type": rule.event_type,
                 "channel_id": rule.channel_id, "enabled": rule.enabled},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(rule)
    out = AlertRuleOut.model_validate(rule)
    out.channel_name = channel.name
    return out


@router.put("/rules/{rule_id}", response_model=AlertRuleOut)
def update_rule(
    rule_id: int,
    body: AlertRuleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    rule = db.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if body.name is not None:
        rule.name = body.name
    if body.event_type is not None:
        rule.event_type = EventType(body.event_type)
    if body.channel_id is not None:
        channel = db.get(NotificationChannel, body.channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")
        rule.channel_id = body.channel_id
    if body.filters is not None:
        rule.filters = body.filters
    if body.enabled is not None:
        rule.enabled = body.enabled
    record_audit(
        db,
        action="update",
        resource_type="notification_rule",
        resource_id=rule.id,
        details={"name": rule.name, "event_type": rule.event_type,
                 "channel_id": rule.channel_id, "enabled": rule.enabled},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(rule)
    out = AlertRuleOut.model_validate(rule)
    if rule.channel:
        out.channel_name = rule.channel.name
    return out


@router.delete("/rules/{rule_id}")
def delete_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    rule = db.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    rule_name = rule.name
    rule_event = rule.event_type
    rule_channel = rule.channel_id
    db.delete(rule)
    record_audit(
        db,
        action="delete",
        resource_type="notification_rule",
        resource_id=rule_id,
        details={"name": rule_name, "event_type": rule_event, "channel_id": rule_channel},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Notification logs (unified history: platform events + alertmanager alerts)
# ---------------------------------------------------------------------------

@router.get("/logs")
def list_logs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    unread_only: bool = Query(False),
    db: Session = Depends(get_db),
):
    q = db.query(NotificationLog)
    if unread_only:
        q = q.filter(NotificationLog.read.is_(False))
    total = q.count()
    items = q.order_by(NotificationLog.created_at.desc()).offset(skip).limit(limit).all()
    return {
        "items": [
            {
                "id": r.id,
                "source": r.source.value if hasattr(r.source, "value") else str(r.source),
                "event_type": r.event_type,
                "severity": r.severity.value if hasattr(r.severity, "value") else str(r.severity),
                "title": r.title,
                "message": r.message,
                "context": r.context or {},
                "read": r.read,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in items
        ],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.get("/logs/unread-count")
def unread_count(db: Session = Depends(get_db)):
    count = db.query(NotificationLog).filter(NotificationLog.read.is_(False)).count()
    return {"unread": count}


@router.patch("/logs/{log_id}/read")
def mark_read(log_id: int, db: Session = Depends(get_db)):
    log = db.get(NotificationLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Notification log not found")
    log.read = True
    db.commit()
    return {"ok": True}


@router.post("/logs/read-all")
def mark_all_read(db: Session = Depends(get_db)):
    db.query(NotificationLog).filter(NotificationLog.read.is_(False)).update({"read": True})
    db.commit()
    return {"ok": True}


@router.post("/webhook")
async def alertmanager_webhook(request: Request):
    """Receive alertmanager webhook alerts and log them."""
    body = await request.json()
    from backend.services.notification_service import receive_alertmanager_alert
    receive_alertmanager_alert(body)
    return {"ok": True}
