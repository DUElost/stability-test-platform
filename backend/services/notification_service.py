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
from backend.models.notification import (
    AlertRule,
    EventType,
    NotificationChannel,
    NotificationDelivery,
    NotificationLog,
    NotificationSeverity,
    NotificationSource,
)
from backend.services.notification_delivery import (
    DeliveryOutcome,
    DeliveryResult,
    accepted,
    classify_exception,
    classify_http_status,
    rejected_permanent,
)

logger = logging.getLogger(__name__)

# SMTP config from env (optional)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
# #1122：网络 deadline —— 无超时的 SMTP 会把通知线程挂死在 connect/read 上，
# 8 个 worker 很快被拖光，后续所有通知持续积压。
SMTP_TIMEOUT_SECONDS = max(
    1.0,
    float(os.getenv("STP_SMTP_TIMEOUT_SECONDS", "15")),
)


def _channel_deadline(env_key: str, default: float) -> float:
    """#1167 P4（D3）：每通道显式 deadline（数值属实现/配置）。

    WEBHOOK/DINGTALK 走 ``STP_NOTIFY_<TYPE>_TIMEOUT_S``；EMAIL 复用
    ``STP_SMTP_TIMEOUT_SECONDS``（见 SMTP_TIMEOUT_SECONDS）。
    """
    raw = os.getenv(env_key, "").strip()
    if not raw:
        return default
    try:
        return max(1.0, float(raw))
    except ValueError:
        logger.warning("invalid_timeout_env key=%s value=%r", env_key, raw)
        return default


WEBHOOK_TIMEOUT_SECONDS = _channel_deadline("STP_NOTIFY_WEBHOOK_TIMEOUT_S", 10.0)
DINGTALK_TIMEOUT_SECONDS = _channel_deadline("STP_NOTIFY_DINGTALK_TIMEOUT_S", 10.0)


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


def send_to_channel(channel: NotificationChannel, message: str) -> DeliveryResult:
    """Send a message through the specified channel（#1167 P1：归一化 DeliveryResult）。

    不再以异常表达投递失败——调用方按 ``result.accepted`` / ``outcome`` 判定
    （ADR-0036 D1/D9）。配置缺失属 REJECTED_PERMANENT（重试无意义）。
    """
    config = channel.config or {}
    channel_type = channel.type.value if hasattr(channel.type, "value") else str(channel.type)

    if channel_type == "WEBHOOK":
        return _send_webhook(config.get("url", ""), message)
    if channel_type == "DINGTALK":
        return _send_dingtalk(config.get("url", ""), config.get("secret", ""), message)
    if channel_type == "EMAIL":
        return _send_email(config.get("to", ""), config.get("subject_prefix", "[Stability]"), message)
    return rejected_permanent(f"Unknown channel type: {channel_type}", channel_type=channel_type)


def _delivery_error_result(
    resp: requests.Response, channel_type: str,
) -> DeliveryResult | None:
    """非 2xx → 归一化失败结果（credential-safe，#1214）。

    ``requests.raise_for_status()`` embeds the full request URL (which may carry
    ``access_token`` / ``sign`` query credentials) in the exception text; that
    text is persisted and can reach the LLM context. Surface only the status.
    """
    status = getattr(resp, "status_code", None)
    if not isinstance(status, int) or status < 400:
        return None
    reason = (getattr(resp, "reason", "") or "").strip()
    detail = f"HTTP {status}"
    if reason:
        detail = f"{detail} {reason}"
    # Belt-and-braces: a custom adapter could still smuggle a URL via reason.
    from backend.core.redaction import redact_secrets

    result = classify_http_status(status, channel_type=channel_type)
    return DeliveryResult(result.outcome, redact_secrets(detail), channel_type)


def _send_webhook(url: str, message: str) -> DeliveryResult:
    if not url:
        return rejected_permanent("Webhook URL not configured", channel_type="WEBHOOK")
    try:
        resp = requests.post(
            url,
            json={"text": message, "content": message},
            timeout=WEBHOOK_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - adapter contract：异常归一化
        return classify_exception(exc, channel_type="WEBHOOK")
    err = _delivery_error_result(resp, "WEBHOOK")
    if err is not None:
        return err
    return accepted("WEBHOOK", f"HTTP {getattr(resp, 'status_code', '?')}")


def _send_dingtalk(url: str, secret: str, message: str) -> DeliveryResult:
    if not url:
        return rejected_permanent("DingTalk webhook URL not configured", channel_type="DINGTALK")

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

    try:
        resp = requests.post(
            url, json=payload, headers=headers, timeout=DINGTALK_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - adapter contract：异常归一化
        return classify_exception(exc, channel_type="DINGTALK")
    err = _delivery_error_result(resp, "DINGTALK")
    if err is not None:
        return err
    biz = _dingtalk_business_error_result(resp)
    if biz is not None:
        return biz
    return accepted("DINGTALK", f"HTTP {getattr(resp, 'status_code', '?')}")


def _dingtalk_business_error_result(resp: requests.Response) -> DeliveryResult | None:
    """#1120 / #1167 D2: DingTalk returns HTTP 200 with ``errcode != 0`` on
    business failure.

    业务拒绝按 REJECTED_PERMANENT 归一化（配置/鉴权/参数类，重试无意义）；
    errcode → 结果类的细分映射属 adapter contract，后续可依实测数据把
    限流类 errcode 调整为 TRANSIENT（见 Agent Note Revisit）。
    """
    try:
        body = resp.json()
    except ValueError:
        # Non-JSON body with 2xx: nothing further to validate.
        return None
    if not isinstance(body, dict):
        return None
    errcode = body.get("errcode", 0)
    try:
        code_int = int(errcode) if errcode is not None else 0
    except (TypeError, ValueError):
        from backend.core.redaction import redact_secrets

        return rejected_permanent(
            redact_secrets(f"DingTalk API error: invalid errcode={errcode!r}"),
            channel_type="DINGTALK",
        )
    if code_int != 0:
        from backend.core.redaction import redact_secrets

        errmsg = body.get("errmsg", "unknown")
        return rejected_permanent(
            redact_secrets(f"DingTalk API error errcode={code_int} errmsg={errmsg}"),
            channel_type="DINGTALK",
        )
    return None


def _send_email(to: str, subject_prefix: str, message: str) -> DeliveryResult:
    if not to:
        return rejected_permanent("Email recipient not configured", channel_type="EMAIL")
    if not SMTP_HOST:
        return rejected_permanent("SMTP_HOST not configured in environment", channel_type="EMAIL")

    msg = MIMEText(message, "plain", "utf-8")
    msg["Subject"] = f"{subject_prefix} Notification"
    msg["From"] = SMTP_FROM or SMTP_USER
    msg["To"] = to

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as server:
            if SMTP_PORT != 25:
                server.starttls()
            if SMTP_USER and SMTP_PASSWORD:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(msg["From"], [to], msg.as_string())
    except Exception as exc:  # noqa: BLE001 - adapter contract：异常归一化
        return classify_exception(exc, channel_type="EMAIL")
    return accepted("EMAIL", "smtp accepted")


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


def _delivery_state_for(result: DeliveryResult) -> str:
    """D6 生命周期词表（小写）：accepted / retrying / failed。"""
    if result.accepted:
        return "accepted"
    return "retrying" if result.retryable else "failed"


def _persist_delivery_facts(log_id: int, facts: list[dict[str, Any]]) -> None:
    """#1167 P4（D6）：投递结果落 ``notification_delivery``（业务事实层）。

    每通道一行（unique(log, channel)）：首次 insert、重试在原行累加
    ``attempt_count`` 并覆盖 outcome/last_error/state/updated_at。
    与 ``NotificationLog.context.channel_delivery``（过渡 JSONB）双写——
    本表为权威读取源；历史日志无本表行时读取侧回落 JSONB。
    """
    if not facts:
        return
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        for fact in facts:
            row = (
                db.query(NotificationDelivery)
                .filter(
                    NotificationDelivery.notification_log_id == log_id,
                    NotificationDelivery.channel_id == fact["channel_id"],
                )
                .one_or_none()
            )
            result: DeliveryResult = fact["result"]
            if row is None:
                row = NotificationDelivery(
                    notification_log_id=log_id,
                    channel_id=fact["channel_id"],
                    channel_type=fact["channel_type"],
                    state=_delivery_state_for(result),
                    outcome=result.outcome.value,
                    attempt_count=1,
                    last_error=result.detail or None,
                    requested_at=now,
                    updated_at=now,
                )
                db.add(row)
            else:
                row.state = _delivery_state_for(result)
                row.outcome = result.outcome.value
                row.attempt_count = int(row.attempt_count or 0) + 1
                row.last_error = result.detail or None
                row.channel_type = fact["channel_type"]
                row.updated_at = now
        db.commit()


def _load_delivery_fact_outcomes(db, log_id: int) -> dict[int, str]:
    """读取投递事实表：channel_id → outcome（本表权威；无行返回空 = 回落 JSONB）。"""
    rows = (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.notification_log_id == log_id)
        .all()
    )
    return {
        int(r.channel_id): (r.outcome or "")
        for r in rows
        if r.channel_id is not None
    }


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

    #1167 P1/P2（ADR-0036 D1/D2/D5）：适配器结果归一化为 ``DeliveryResult``；
    只有**可重试失败**（REJECTED_TRANSIENT / UNKNOWN）抛
    ``NotificationDeliveryError`` 让 SAQ 重试；永久拒绝如实记录但不重试。
    Already-successful channels (recorded on NotificationLog.context
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
            # #1167 P4（D6）：投递事实表为权威读取源；无行 = 历史日志，回落
            # JSONB（prior_delivery）。
            prior_fact_outcomes = (
                _load_delivery_fact_outcomes(db, prior_log_id)
                if prior_log_id is not None
                else {}
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
    delivery_facts: list[dict[str, Any]] = []
    succeeded: list[int] = []
    failed: list[dict[str, Any]] = []
    retryable_failed = 0

    for dispatch in pending_dispatches:
        channel_id = int(dispatch["channel_id"])
        ch_key = str(channel_id)
        prior = delivery.get(ch_key)
        # D7 投递级幂等：本通道已 ACCEPTED 的尝试不重发（重试只补失败通道）。
        # 事实表为权威；历史日志（无表行）回落 JSONB——兼容仅有 status 字段
        # 的 P1 前旧记录。
        accepted_before = False
        if channel_id in prior_fact_outcomes:
            accepted_before = (
                prior_fact_outcomes[channel_id] == DeliveryOutcome.ACCEPTED.value
            )
        elif isinstance(prior, dict):
            accepted_before = (
                prior.get("status") == "ok"
                or prior.get("outcome") == DeliveryOutcome.ACCEPTED.value
            )
        if accepted_before:
            succeeded.append(channel_id)
            continue

        channel = NotificationChannel(
            id=channel_id,
            type=dispatch["channel_type"],
            config=dispatch["channel_config"],
            enabled=True,
        )
        try:
            # #1167 P1（D9）：适配器归一化返回 DeliveryResult；异常视为契约外
            # 实现异常，保守归类（不吞、不猜成功）。
            result = send_to_channel(channel, message)
            if not isinstance(result, DeliveryResult):
                from backend.services.notification_delivery import unknown

                result = unknown(
                    f"adapter returned non-DeliveryResult: {type(result).__name__}",
                    channel_type=str(dispatch["channel_type"]),
                )
        except Exception as exc:  # noqa: BLE001 - 落地为结果类，不在此层吞语义
            result = classify_exception(exc, channel_type=str(dispatch["channel_type"]))

        delivery_facts.append({
            "channel_id": channel_id,
            "channel_type": str(
                getattr(dispatch["channel_type"], "value", dispatch["channel_type"])
            ),
            "result": result,
        })
        if result.accepted:
            delivery[ch_key] = result.record()
            succeeded.append(channel_id)
            logger.info(
                "notification_sent",
                extra={
                    "rule_id": dispatch["rule_id"],
                    "channel_id": channel_id,
                    "event_type": event_type,
                },
            )
        else:
            delivery[ch_key] = result.record()
            failed.append({
                "rule_id": dispatch["rule_id"],
                "channel_id": channel_id,
                "outcome": result.outcome.value,
                "retryable": result.retryable,
                "error": result.detail,
            })
            if result.retryable:
                retryable_failed += 1
            logger.warning(
                "notification_send_failed",
                extra={
                    "rule_id": dispatch["rule_id"],
                    "channel_id": channel_id,
                    "outcome": result.outcome.value,
                    "retryable": result.retryable,
                    "error": result.detail,
                },
            )

    try:
        # P4（D6）：事实表为权威（1:N 可查、可索引）；JSONB 双写为过渡兼容。
        _persist_delivery_facts(log_id, delivery_facts)
    except Exception:
        logger.exception(
            "notification_delivery_facts_persist_failed",
            extra={"log_id": log_id, "event_type": event_type},
        )
    try:
        _persist_channel_delivery(log_id, delivery)
    except Exception:
        logger.exception(
            "notification_delivery_persist_failed",
            extra={"log_id": log_id, "event_type": event_type},
        )

    # D5：只有可重试失败（REJECTED_TRANSIENT / UNKNOWN）才让 SAQ 重试；
    # 永久拒绝（配置/鉴权/语义错）已记录事实，重试无意义。
    if retryable_failed:
        raise NotificationDeliveryError(
            f"notification channel delivery failed for {retryable_failed} "
            f"retryable channel(s) ({len(failed)} failed total)",
            succeeded=succeeded,
            failed=failed,
        )


def _notification_job_key(event_type: str, context: Dict[str, Any]) -> str:
    """SAQ 去重键：同一事件的重复终态/心跳不重复入队（D7 去重键形态之一）。"""
    return (
        f"notif:{event_type}:"
        f"{context.get('run_id')}:{context.get('device_serial') or ''}"
    )


def dispatch_notification_async(event_type: str, context: Dict[str, Any]) -> None:
    """生产投递入口（#1167 P3 / ADR-0036 D4/D7）——入 SAQ，唯一 retry owner。

    入队即返回；SAQ 未运行 / enqueue 失败 → 降级 best-effort 线程池直达
    （**无重试语义**，仅告警一次，保证可用性）；本函数不向调用方外溢异常。
    投递级幂等由 ``dispatch_notification`` 的 channel_delivery 记录保证
    （重试不重发已 ACCEPTED 通道）。
    """
    enqueued = False
    try:
        from backend.tasks.saq_worker import enqueue_sync

        enqueued = enqueue_sync(
            "send_notification_task",
            key=_notification_job_key(event_type, context),
            timeout=120,
            retries=3,
            event_type=event_type,
            context=dict(context or {}),
        )
    except Exception:
        logger.exception(
            "notification_enqueue_failed", extra={"event_type": event_type},
        )
        enqueued = False

    if enqueued:
        return

    logger.warning(
        "notification_enqueue_unavailable_fallback_pool",
        extra={"event_type": event_type},
    )
    _dispatch_notification_via_pool(event_type, context)


def _dispatch_notification_via_pool(event_type: str, context: Dict[str, Any]) -> None:
    """降级路径：有界线程池 fire-and-forget（无重试语义）。

    #1122：队列满即拒绝（PoolQueueFullError）——丢弃并记 warning/metric。
    """
    from backend.core.thread_pool import PoolQueueFullError, submit as pool_submit

    def _safe() -> None:
        try:
            dispatch_notification(event_type, context)
        except Exception:
            # 本路径无 SAQ 重试；保持 best-effort 语义。
            logger.exception(
                "dispatch_notification_async_failed",
                extra={"event_type": event_type},
            )

    try:
        pool_submit(_safe)
    except PoolQueueFullError:
        logger.warning(
            "dispatch_notification_dropped_queue_full",
            extra={"event_type": event_type},
        )


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
