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
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Any, Dict

import requests
from sqlalchemy.orm import joinedload

from backend.core.database import SessionLocal
from backend.models.notification import AlertRule, EventType, NotificationChannel, NotificationLog, NotificationSeverity, NotificationSource

logger = logging.getLogger(__name__)

# SMTP config from env (optional)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")


class NotificationDeliveryError(RuntimeError):
    """One or more notification channels failed (#1117).

    Raised so SAQ ``send_notification_task`` can retry. ``succeeded`` /
    ``failed`` carry channel ids for observability and idempotent resend.
    """

    def __init__(
        self,
        message: str,
        *,
        succeeded: list[int] | None = None,
        failed: list[dict[str, Any]] | None = None,
    ):
        super().__init__(message)
        self.succeeded = list(succeeded or [])
        self.failed = list(failed or [])


def _format_message(event_type: str, context: Dict[str, Any]) -> str:
    """Format a human-readable notification message."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

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
    _raise_if_dingtalk_business_error(resp)


def _raise_if_dingtalk_business_error(resp: requests.Response) -> None:
    """#1120: DingTalk returns HTTP 200 with ``errcode != 0`` on business failure.

    Treat any non-zero errcode as a delivery error so callers (SAQ / test
    channel) do not report success.
    """
    try:
        body = resp.json()
    except ValueError:
        # Non-JSON body with 2xx: nothing further to validate.
        return
    if not isinstance(body, dict):
        return
    errcode = body.get("errcode", 0)
    try:
        code_int = int(errcode) if errcode is not None else 0
    except (TypeError, ValueError):
        raise RuntimeError(
            f"DingTalk API error: invalid errcode={errcode!r} body={body!r}"
        ) from None
    if code_int != 0:
        errmsg = body.get("errmsg", "unknown")
        raise RuntimeError(
            f"DingTalk API error errcode={code_int} errmsg={errmsg}"
        )


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


def _delivery_identity(event_type: str, context: Dict[str, Any]) -> tuple[Any, ...]:
    """Stable identity for idempotent channel delivery across SAQ retries."""
    return (
        event_type,
        context.get("run_id"),
        context.get("task_id"),
        context.get("device_serial"),
    )


def _load_prior_channel_delivery(
    db, event_type: str, context: Dict[str, Any],
) -> tuple[int | None, dict[str, Any]]:
    """Return (log_id, channel_delivery) from the latest matching log, if any."""
    run_id = context.get("run_id")
    if run_id is None:
        return None, {}
    rows = (
        db.query(NotificationLog)
        .filter(NotificationLog.event_type == event_type)
        .order_by(NotificationLog.id.desc())
        .limit(30)
        .all()
    )
    identity = _delivery_identity(event_type, context)
    for log in rows:
        ctx = log.context if isinstance(log.context, dict) else {}
        if _delivery_identity(event_type, ctx) != identity:
            continue
        delivery = ctx.get("channel_delivery")
        if isinstance(delivery, dict):
            return log.id, dict(delivery)
        return log.id, {}
    return None, {}


def _persist_channel_delivery(log_id: int, delivery: dict[str, Any]) -> None:
    with SessionLocal() as db:
        log = db.get(NotificationLog, log_id)
        if log is None:
            return
        ctx = dict(log.context or {}) if isinstance(log.context, dict) else {}
        ctx["channel_delivery"] = delivery
        log.context = ctx
        db.commit()


def dispatch_notification(event_type: str, context: Dict[str, Any]) -> None:
    """
    Dispatch notifications for an event. Opens its own DB session.

    Channel send failures raise ``NotificationDeliveryError`` so SAQ can retry
    (#1117). Already-successful channels (recorded on NotificationLog.context
    ``channel_delivery``) are skipped on retry.
    """
    message = _format_message(event_type, context)
    severity = NotificationSeverity.WARNING if event_type in (
        EventType.RUN_FAILED.value, EventType.DEVICE_OFFLINE.value, EventType.RISK_HIGH.value,
    ) else NotificationSeverity.INFO

    try:
        with SessionLocal() as db:
            prior_log_id, prior_delivery = _load_prior_channel_delivery(
                db, event_type, context,
            )
            if prior_log_id is not None:
                log_id = prior_log_id
                log = db.get(NotificationLog, log_id)
                log_created = (
                    log.created_at.isoformat()
                    if log is not None and log.created_at
                    else None
                )
                # Keep in-app log once; only re-emit on first create.
                emit_new = False
            else:
                log = NotificationLog(
                    source=NotificationSource.PLATFORM,
                    event_type=event_type,
                    severity=severity,
                    title=event_type.replace("_", " ").title(),
                    message=message,
                    context=dict(context or {}),
                )
                db.add(log)
                db.commit()
                db.refresh(log)
                log_id = log.id
                log_created = log.created_at.isoformat() if log.created_at else None
                emit_new = True

            rules = (
                db.query(AlertRule)
                .options(joinedload(AlertRule.channel))
                .filter(AlertRule.event_type == event_type, AlertRule.enabled.is_(True))
                .all()
            )

            pending_dispatches = []
            for rule in rules:
                if not _matches_filters(rule.filters or {}, context):
                    continue
                channel = rule.channel
                if not channel or not channel.enabled:
                    continue
                pending_dispatches.append(
                    {
                        "rule_id": rule.id,
                        "channel_id": channel.id,
                        "channel_type": channel.type,
                        "channel_config": dict(channel.config or {}),
                    }
                )
    except Exception:
        logger.exception("dispatch_notification_failed", extra={"event_type": event_type})
        raise

    if emit_new:
        _emit_notification_socketio(
            log_id,
            NotificationSource.PLATFORM.value,
            event_type,
            severity.value,
            event_type.replace("_", " ").title(),
            message,
            log_created,
        )

    if not pending_dispatches:
        return

    delivery: dict[str, Any] = dict(prior_delivery)
    succeeded: list[int] = []
    failed: list[dict[str, Any]] = []

    for dispatch in pending_dispatches:
        channel_id = int(dispatch["channel_id"])
        ch_key = str(channel_id)
        prior = delivery.get(ch_key)
        if isinstance(prior, dict) and prior.get("status") == "ok":
            succeeded.append(channel_id)
            continue

        channel = NotificationChannel(
            id=channel_id,
            type=dispatch["channel_type"],
            config=dispatch["channel_config"],
            enabled=True,
        )
        try:
            send_to_channel(channel, message)
            delivery[ch_key] = {"status": "ok"}
            succeeded.append(channel_id)
            logger.info(
                "notification_sent",
                extra={
                    "rule_id": dispatch["rule_id"],
                    "channel_id": channel_id,
                    "event_type": event_type,
                },
            )
        except Exception as exc:
            delivery[ch_key] = {"status": "failed", "error": str(exc)}
            failed.append({
                "rule_id": dispatch["rule_id"],
                "channel_id": channel_id,
                "error": str(exc),
            })
            logger.warning(
                "notification_send_failed",
                extra={
                    "rule_id": dispatch["rule_id"],
                    "channel_id": channel_id,
                    "error": str(exc),
                },
            )

    try:
        _persist_channel_delivery(log_id, delivery)
    except Exception:
        logger.exception(
            "notification_delivery_persist_failed",
            extra={"log_id": log_id, "event_type": event_type},
        )

    if failed:
        raise NotificationDeliveryError(
            f"notification channel delivery failed for {len(failed)} channel(s)",
            succeeded=succeeded,
            failed=failed,
        )


def dispatch_notification_async(event_type: str, context: Dict[str, Any]) -> None:
    """Fire-and-forget wrapper — submits to bounded thread pool."""
    from backend.core.thread_pool import submit as pool_submit

    def _safe() -> None:
        try:
            dispatch_notification(event_type, context)
        except Exception:
            # Thread-pool callers have no SAQ retry; keep best-effort semantics.
            logger.exception(
                "dispatch_notification_async_failed",
                extra={"event_type": event_type},
            )

    pool_submit(_safe)


def _emit_notification_socketio(
    log_id: int,
    source: str,
    event_type: str,
    severity: str,
    title: str,
    message: str,
    created_at: str | None,
) -> None:
    """Emit notification:new to all dashboard clients (best-effort)."""
    try:
        from backend.realtime.socketio_server import schedule_emit

        # 信封格式与其余 /dashboard 事件一致({type, payload, timestamp}):
        # 前端 NotificationBell 按 msg.type 判事件,此前裸发字段导致
        # "notification:new 广播了但前端收不到" (#268 多Worker 审计 B2)。
        # schedule_emit 走主事件循环(run_coroutine_threadsafe),可跨线程调用,
        # 取代原先 loop.create_task/asyncio.run 的易错舞步(#281 CR 意见)。
        envelope = {
            "type": "notification:new",
            "payload": {
                "id": log_id,
                "source": source,
                "event_type": event_type,
                "severity": severity,
                "title": title,
                "message": message,
                "created_at": created_at,
            },
            "timestamp": created_at,
        }
        schedule_emit("notification:new", envelope)
    except Exception:
        logger.debug("emit_notification_socketio_failed", exc_info=True)


def receive_alertmanager_alert(alert_data: Dict[str, Any]) -> None:
    """Process an alertmanager webhook payload and log it."""
    try:
        status = alert_data.get("status", "firing")
        alerts = alert_data.get("alerts", [])
        for alert in alerts:
            labels = alert.get("labels", {})
            annotations = alert.get("annotations", {})
            alertname = labels.get("alertname", "UnknownAlert")
            severity = labels.get("severity", "warning")

            sev = NotificationSeverity.CRITICAL if severity == "critical" else (
                NotificationSeverity.WARNING if severity == "warning" else NotificationSeverity.INFO
            )

            title = f"[{alertname}] {status}"
            message = annotations.get("description", annotations.get("summary", ""))

            with SessionLocal() as db:
                log = NotificationLog(
                    source=NotificationSource.ALERTMANAGER,
                    event_type=alertname,
                    severity=sev,
                    title=title,
                    message=message,
                    context={"labels": labels, "annotations": annotations, "status": status},
                )
                db.add(log)
                db.commit()
                db.refresh(log)
                log_id = log.id
                log_created = log.created_at.isoformat() if log.created_at else None

            _emit_notification_socketio(
                log_id, NotificationSource.ALERTMANAGER.value, alertname, sev.value, title, message, log_created
            )
    except Exception:
        logger.exception("receive_alertmanager_alert_failed")
