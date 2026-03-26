# -*- coding: utf-8 -*-
"""
Notification Dispatcher Service

Dispatches notifications to configured channels when events occur.
Runs in a background thread (fire-and-forget) to avoid blocking callers.
"""

import json
import logging
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from typing import Any, Dict, Optional

import requests

from backend.core.database import SessionLocal
from backend.models.notification import AlertRule, EventType, NotificationChannel

logger = logging.getLogger(__name__)

# SMTP config from env (optional)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")


def _format_message(event_type: str, context: Dict[str, Any]) -> str:
    """Format a human-readable notification message."""
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    if event_type == EventType.RUN_COMPLETED.value:
        return (
            f"[Task Completed] Task run #{context.get('run_id')} finished successfully.\n"
            f"Task: {context.get('task_name', 'unknown')} ({context.get('task_type', '')})\n"
            f"Device: {context.get('device_serial', 'N/A')}\n"
            f"Time: {ts}"
        )
    elif event_type == EventType.RUN_FAILED.value:
        return (
            f"[Task Failed] Task run #{context.get('run_id')} failed.\n"
            f"Task: {context.get('task_name', 'unknown')} ({context.get('task_type', '')})\n"
            f"Error: {context.get('error_message', 'N/A')}\n"
            f"Device: {context.get('device_serial', 'N/A')}\n"
            f"Time: {ts}"
        )
    elif event_type == EventType.DEVICE_OFFLINE.value:
        return (
            f"[Device Offline] Device {context.get('device_serial', 'unknown')} went offline.\n"
            f"Host: {context.get('host_name', 'N/A')}\n"
            f"Time: {ts}"
        )
    elif event_type == EventType.RISK_HIGH.value:
        return (
            f"[High Risk Alert] Run #{context.get('run_id')} flagged as HIGH risk.\n"
            f"Task: {context.get('task_name', 'unknown')}\n"
            f"Summary: {context.get('risk_summary', 'N/A')}\n"
            f"Time: {ts}"
        )
    else:
        return f"[{event_type}] {json.dumps(context, ensure_ascii=False, default=str)}"


def _matches_filters(rule_filters: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Check if the event context matches the rule's filter criteria."""
    if not rule_filters:
        return True
    for key, value in rule_filters.items():
        ctx_value = context.get(key)
        if isinstance(value, list):
            if ctx_value not in value:
                return False
        elif ctx_value != value:
            return False
    return True


def send_to_channel(channel: NotificationChannel, message: str) -> None:
    """Send a message through the specified channel. Raises on failure."""
    config = channel.config or {}
    channel_type = channel.type.value if hasattr(channel.type, "value") else str(channel.type)

    if channel_type == "WEBHOOK":
        _send_webhook(config.get("url", ""), message)
    elif channel_type == "DINGTALK":
        _send_dingtalk(config.get("url", ""), config.get("secret", ""), message)
    elif channel_type == "EMAIL":
        _send_email(config.get("to", ""), config.get("subject_prefix", "[Stability]"), message)
    else:
        raise ValueError(f"Unknown channel type: {channel_type}")


def _send_webhook(url: str, message: str) -> None:
    if not url:
        raise ValueError("Webhook URL not configured")
    resp = requests.post(
        url,
        json={"text": message, "content": message},
        timeout=10,
    )
    resp.raise_for_status()


def _send_dingtalk(url: str, secret: str, message: str) -> None:
    if not url:
        raise ValueError("DingTalk webhook URL not configured")

    headers = {"Content-Type": "application/json"}
    payload = {
        "msgtype": "text",
        "text": {"content": message},
    }

    if secret:
        import hashlib
        import hmac
        import base64
        import urllib.parse
        import time as _time

        timestamp = str(round(_time.time() * 1000))
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(
            secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        url = f"{url}&timestamp={timestamp}&sign={sign}"

    resp = requests.post(url, json=payload, headers=headers, timeout=10)
    resp.raise_for_status()


def _send_email(to: str, subject_prefix: str, message: str) -> None:
    if not to:
        raise ValueError("Email recipient not configured")
    if not SMTP_HOST:
        raise ValueError("SMTP_HOST not configured in environment")

    msg = MIMEText(message, "plain", "utf-8")
    msg["Subject"] = f"{subject_prefix} Notification"
    msg["From"] = SMTP_FROM or SMTP_USER
    msg["To"] = to

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        if SMTP_PORT != 25:
            server.starttls()
        if SMTP_USER and SMTP_PASSWORD:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(msg["From"], [to], msg.as_string())


def dispatch_notification(event_type: str, context: Dict[str, Any]) -> None:
    """
    Dispatch notifications for an event. Opens its own DB session.
    Safe to call from any thread.
    """
    try:
        db = SessionLocal()
        try:
            rules = (
                db.query(AlertRule)
                .filter(AlertRule.event_type == event_type, AlertRule.enabled.is_(True))
                .all()
            )

            if not rules:
                return

            message = _format_message(event_type, context)

            for rule in rules:
                if not _matches_filters(rule.filters or {}, context):
                    continue

                channel = rule.channel
                if not channel or not channel.enabled:
                    continue

                try:
                    send_to_channel(channel, message)
                    logger.info(
                        "notification_sent",
                        extra={
                            "rule_id": rule.id,
                            "channel_id": channel.id,
                            "event_type": event_type,
                        },
                    )
                except Exception as exc:
                    logger.warning(
                        "notification_send_failed",
                        extra={
                            "rule_id": rule.id,
                            "channel_id": channel.id,
                            "error": str(exc),
                        },
                    )
        finally:
            db.close()
    except Exception:
        logger.exception("dispatch_notification_failed", extra={"event_type": event_type})


def dispatch_notification_async(event_type: str, context: Dict[str, Any]) -> None:
    """Fire-and-forget wrapper — submits to bounded thread pool."""
    from backend.core.thread_pool import submit as pool_submit
    pool_submit(dispatch_notification, event_type, context)
