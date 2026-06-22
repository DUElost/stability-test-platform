# -*- coding: utf-8 -*-
"""
SAQ task functions — async jobs processed by the in-process SAQ worker.

Each function receives a SAQ context dict as the first positional argument
and keyword arguments that were passed at enqueue time.
"""

import logging
import asyncio

logger = logging.getLogger(__name__)


async def post_completion_task(ctx: dict, *, job_id: int) -> None:
    """Generate report + JIRA draft for a terminal JobInstance.

    Idempotent: skips if ``post_processed_at`` is already set.
    """
    from backend.services.post_completion import run_post_completion_async

    logger.info("saq_post_completion_start job_id=%d", job_id)
    try:
        await asyncio.to_thread(run_post_completion_async, job_id)
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


async def precheck_and_dispatch_task(ctx: dict, *, plan_run_id: int) -> None:
    """ADR-0021 — Run the dispatch gate for ``plan_run_id``.

    Defers to :func:`backend.services.plan_precheck.precheck_and_dispatch_task`
    to keep the heavy logic out of this module's import surface.
    """
    from backend.services.plan_precheck import (
        precheck_and_dispatch_task as _impl,
    )
    await _impl(ctx, plan_run_id=plan_run_id)


async def scan_task(ctx: dict, *, plan_run_id: int, is_final: bool = False) -> None:
    """ADR-0025 Sprint 4: 归档-2 向各 ONLINE agent 下发 scan_now SocketIO 指令。

    Agent 本地执行 start_log_scan -dedup_org → 上送 _org.xls →
    控制面 run_scan_sync 注册产物到 plan_run_artifact 表。
    """
    from backend.core.database import SessionLocal
    from backend.models.job import JobInstance
    from backend.models.host import Host
    from backend.realtime.socketio_server import emit_agent_control
    from sqlalchemy import select, distinct

    logger.info("saq_scan_start plan_run=%d final=%s", plan_run_id, is_final)

    db = SessionLocal()
    try:
        host_rows = db.execute(
            select(distinct(JobInstance.host_id), Host.status)
            .join(Host, Host.id == JobInstance.host_id)
            .where(JobInstance.plan_run_id == plan_run_id)
        ).all()

        triggered: list[str] = []
        skipped: list[str] = []
        for host_id, host_status in host_rows:
            if host_status == "ONLINE":
                await emit_agent_control(
                    host_id, "scan_now",
                    payload={"plan_run_id": plan_run_id, "is_final": is_final},
                )
                triggered.append(host_id)
            else:
                skipped.append(host_id)

        logger.info(
            "saq_scan_dispatched plan_run=%d triggered=%d skipped=%d",
            plan_run_id, len(triggered), len(skipped),
        )
    except Exception:
        logger.exception("saq_scan_failed plan_run=%d", plan_run_id)
        raise
    finally:
        db.close()

    logger.info("saq_scan_done plan_run=%d", plan_run_id)


async def upload_task(ctx: dict, *, plan_run_id: int) -> None:
    """ADR-0025 Sprint 4: 归档-2 向各 ONLINE agent 下发 upload_events SocketIO 指令。

    从 PlanRunArtifact 的 _org.xls 行和 JobLogSignal 提取事件目录名，
    逐 host 下发 upload_events 让 Agent 上送事件目录到 15.4 CIFS。
    """
    from backend.core.database import SessionLocal
    from backend.models.job import JobInstance, JobLogSignal
    from backend.models.plan_run_artifact import PlanRunArtifact
    from backend.models.host import Host
    from backend.realtime.socketio_server import emit_agent_control
    from sqlalchemy import select, distinct
    from pathlib import Path

    logger.info("saq_upload_start plan_run=%d", plan_run_id)

    db = SessionLocal()
    try:
        scan_rows = db.execute(
            select(PlanRunArtifact.host_id)
            .where(
                PlanRunArtifact.plan_run_id == plan_run_id,
                PlanRunArtifact.artifact_type == "scan_result_xls",
                PlanRunArtifact.storage_uri.contains("_org.xls"),
            )
        ).scalars().all()
        hosts_with_scan = set(scan_rows)

        host_rows = db.execute(
            select(distinct(JobInstance.host_id), Host.status)
            .join(Host, Host.id == JobInstance.host_id)
            .where(JobInstance.plan_run_id == plan_run_id)
        ).all()

        job_ids_subq = select(JobInstance.id).where(
            JobInstance.plan_run_id == plan_run_id
        )

        triggered: list[str] = []
        skipped: list[str] = []
        for host_id, host_status in host_rows:
            if host_status != "ONLINE":
                skipped.append(host_id)
                continue
            if host_id not in hosts_with_scan:
                skipped.append(host_id)
                continue

            signals = db.execute(
                select(JobLogSignal.path_on_device)
                .where(
                    JobLogSignal.job_id.in_(job_ids_subq),
                    JobLogSignal.host_id == host_id,
                    JobLogSignal.artifact_uri.isnot(None),
                )
            ).scalars().all()
            event_dir_names = list({
                Path(p).name for p in signals if p
            })

            if not event_dir_names:
                logger.info("saq_upload_skip_no_events host=%s plan_run=%d", host_id, plan_run_id)
                continue

            await emit_agent_control(
                host_id, "upload_events",
                payload={
                    "plan_run_id": plan_run_id,
                    "event_dir_names": event_dir_names,
                },
            )
            triggered.append(host_id)

        logger.info(
            "saq_upload_dispatched plan_run=%d triggered=%d skipped=%d",
            plan_run_id, len(triggered), len(skipped),
        )
    except Exception:
        logger.exception("saq_upload_failed plan_run=%d", plan_run_id)
        raise
    finally:
        db.close()

    logger.info("saq_upload_done plan_run=%d", plan_run_id)


async def merge_task(ctx: dict, *, plan_run_id: int) -> None:
    """ADR-0025 Sprint 4: 归档-2 集中合并（-merge_files 各 agent _org.xls）。"""
    from backend.services.dedup_scan import run_merge_sync

    logger.info("saq_merge_start plan_run=%d", plan_run_id)
    try:
        await asyncio.to_thread(run_merge_sync, plan_run_id)
    except Exception:
        logger.exception("saq_merge_failed plan_run=%d", plan_run_id)
        raise
    logger.info("saq_merge_done plan_run=%d", plan_run_id)


SAQ_FUNCTIONS = [
    post_completion_task,
    send_notification_task,
    publish_control_command,
    precheck_and_dispatch_task,
    scan_task,
    upload_task,
    merge_task,
]
