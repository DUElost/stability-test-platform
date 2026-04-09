# -*- coding: utf-8 -*-
"""
SAQ task functions — async jobs processed by the in-process SAQ worker.

Each function receives a SAQ context dict as the first positional argument
and keyword arguments that were passed at enqueue time.
"""

import logging

logger = logging.getLogger(__name__)


async def post_completion_task(ctx: dict, *, job_id: int) -> None:
    """Generate report + JIRA draft for a terminal JobInstance.

    Idempotent: skips if ``post_processed_at`` is already set.
    """
    from backend.services.post_completion import run_post_completion_async

    logger.info("saq_post_completion_start job_id=%d", job_id)
    try:
        run_post_completion_async(job_id)
    except Exception:
        logger.exception("saq_post_completion_failed job_id=%d", job_id)
        raise
    logger.info("saq_post_completion_done job_id=%d", job_id)


async def send_notification_task(
    ctx: dict, *, event_type: str, context: dict
) -> None:
    """Dispatch notification to configured channels (webhook, DingTalk, email).

    Runs synchronously inside the async task because the underlying
    ``dispatch_notification`` opens its own DB session and makes blocking
    HTTP calls — acceptable for a worker thread.
    """
    import asyncio

    from backend.services.notification_service import dispatch_notification

    logger.info("saq_notification_start event_type=%s", event_type)
    try:
        await asyncio.to_thread(dispatch_notification, event_type, context)
    except Exception:
        logger.exception("saq_notification_failed event_type=%s", event_type)
        raise
    logger.info("saq_notification_done event_type=%s", event_type)


async def publish_control_command(
    ctx: dict, *, host_id: str, command: str, payload: dict | None = None
) -> None:
    """Publish a control command (abort / pause / backpressure) to an agent via SocketIO."""
    logger.info(
        "saq_control_command host_id=%s command=%s", host_id, command,
    )
    try:
        from backend.realtime.socketio_server import get_sio
        sio = get_sio()
        await sio.emit("control", {
            "command": command,
            "payload": payload or {},
        }, namespace="/agent", room=f"agent:{host_id}")
        logger.info("saq_control_command_sent host_id=%s command=%s", host_id, command)
    except Exception:
        logger.exception("saq_control_command_failed host_id=%s command=%s", host_id, command)
        raise


SAQ_FUNCTIONS = [post_completion_task, send_notification_task, publish_control_command]
