# -*- coding: utf-8 -*-
"""
SAQ task functions — async jobs processed by the in-process SAQ worker.

Each function receives a SAQ context dict as the first positional argument
and keyword arguments that were passed at enqueue time.
"""

import logging
import asyncio

logger = logging.getLogger(__name__)

asyncio_sleep = asyncio.sleep
asyncio_to_thread = asyncio.to_thread

_MERGE_SYNC_TIMEOUT = 300
_UPLOAD_WAIT_INTERVAL = 5
_UPLOAD_WAIT_MAX = 660  # upload_task SAQ timeout=600，留余量
_DEVICES_POLL_INTERVAL = 10
_DEVICES_POLL_MAX = 300
_TERMINAL_SAQ_STATUSES = frozenset({"complete", "failed", "aborted"})
# merge 子进程 + upload SAQ poll + devices NFS poll + 余量
_MERGE_TASK_SAQ_TIMEOUT = (
    _MERGE_SYNC_TIMEOUT + _UPLOAD_WAIT_MAX + _DEVICES_POLL_MAX + 120
)


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


def _query_hosts_for_scan(plan_run_id: int) -> tuple[list[str], list[str]]:
    """同步查询 scan_task 所需的 host 列表，由 asyncio.to_thread 调用。"""
    from backend.core.database import SessionLocal
    from backend.models.job import JobInstance
    from backend.models.host import Host
    from sqlalchemy import select, distinct

    db = SessionLocal()
    try:
        host_rows = db.execute(
            select(distinct(JobInstance.host_id), Host.status)
            .join(Host, Host.id == JobInstance.host_id)
            .where(JobInstance.plan_run_id == plan_run_id)
        ).all()
    finally:
        db.close()

    triggered: list[str] = []
    skipped: list[str] = []
    for host_id, host_status in host_rows:
        if host_status == "ONLINE":
            triggered.append(host_id)
        else:
            skipped.append(host_id)
    return triggered, skipped


async def scan_task(ctx: dict, *, plan_run_id: int, is_final: bool = False) -> None:
    """ADR-0025 Sprint 4: 归档-2 向各 ONLINE agent 下发 scan_now → 轮询 NFS → 注册 DB → 串行 enqueue upload + merge。

    1. emit scan_now to each ONLINE agent
    2. poll NFS dedup/{plan_run_id}/ for *_org.xls files (max 300s)
    3. call run_scan_sync to register artifacts in plan_run_artifact
    4. enqueue upload_task and merge_task (merge_task chains extract_task on success)
    """
    from backend.realtime.socketio_server import emit_agent_control

    logger.info("saq_scan_start plan_run=%d final=%s", plan_run_id, is_final)

    try:
        triggered, skipped = await asyncio.to_thread(_query_hosts_for_scan, plan_run_id)
        for host_id in triggered:
            await emit_agent_control(
                host_id, "scan_now",
                payload={"plan_run_id": plan_run_id, "is_final": is_final},
            )

        logger.info(
            "saq_scan_dispatched plan_run=%d triggered=%d skipped=%d",
            plan_run_id, len(triggered), len(skipped),
        )
    except Exception:
        logger.exception("saq_scan_failed plan_run=%d", plan_run_id)
        raise

    if triggered:
        from backend.services.dedup_scan import run_scan_sync

        _SCAN_POLL_INTERVAL = 10
        _SCAN_POLL_MAX_WAIT = 300
        elapsed = 0
        registered = 0
        n_triggered = len(triggered)
        while elapsed < _SCAN_POLL_MAX_WAIT:
            await asyncio_sleep(_SCAN_POLL_INTERVAL)
            elapsed += _SCAN_POLL_INTERVAL
            n_new = await asyncio_to_thread(run_scan_sync, plan_run_id)
            if n_new:
                registered += int(n_new)
            if registered >= n_triggered:
                break
            logger.info(
                "saq_scan_poll plan_run=%d elapsed=%ds registered=%d/%d",
                plan_run_id, elapsed, registered, n_triggered,
            )

        if registered == 0:
            await asyncio_to_thread(run_scan_sync, plan_run_id)

        logger.info(
            "saq_scan_registered plan_run=%d artifacts=%d/%d waited=%ds",
            plan_run_id, registered, n_triggered, elapsed,
        )

    from backend.tasks.saq_worker import get_queue
    from saq import Job as SaqJob

    try:
        queue = get_queue()
        await queue.enqueue(
            SaqJob(
                function="upload_task",
                kwargs={"plan_run_id": plan_run_id},
                key=f"upload:{plan_run_id}",
                timeout=600,
                retries=2,
                retry_delay=10.0,
                retry_backoff=True,
            )
        )
        await queue.enqueue(
            SaqJob(
                function="merge_task",
                kwargs={"plan_run_id": plan_run_id},
                key=f"merge:{plan_run_id}",
                timeout=_MERGE_TASK_SAQ_TIMEOUT,
                retries=2,
                retry_delay=10.0,
                retry_backoff=True,
            )
        )
    except Exception as e:
        logger.error("saq_scan_enqueue_followup_failed plan_run=%d: %s", plan_run_id, e)

    logger.info("saq_scan_done plan_run=%d", plan_run_id)


def _query_hosts_for_upload(plan_run_id: int) -> tuple[set[str], list[tuple[str, str]], list[str]]:
    """同步查询 upload_task 所需 host 列表与 event_dir_names。

    返回 (hosts_with_scan, host_rows, event_dir_names)。
    """
    from backend.core.database import SessionLocal
    from backend.models.job import JobInstance
    from backend.models.plan_run_artifact import PlanRunArtifact
    from backend.models.host import Host
    from backend.services.dedup_extract import collect_upload_event_dir_names
    from sqlalchemy import select, distinct

    db = SessionLocal()
    try:
        scan_rows = db.execute(
            select(PlanRunArtifact.host_id)
            .where(
                PlanRunArtifact.plan_run_id == plan_run_id,
                PlanRunArtifact.artifact_type == "scan_result_xls",
            )
        ).scalars().all()
        hosts_with_scan = set(scan_rows)

        host_rows = db.execute(
            select(distinct(JobInstance.host_id), Host.status)
            .join(Host, Host.id == JobInstance.host_id)
            .where(JobInstance.plan_run_id == plan_run_id)
        ).all()

        event_dir_names = collect_upload_event_dir_names(db, plan_run_id)
    finally:
        db.close()

    return hosts_with_scan, host_rows, event_dir_names


async def upload_task(ctx: dict, *, plan_run_id: int) -> None:
    """ADR-0025 Sprint 4: 归档-2 向各 ONLINE agent 下发 upload_events SocketIO 指令。

    Agent 端收到后扫描本地 HDD 事件目录并上送到 15.4 CIFS devices/。
    仅对已有 scan_result_xls 的 host 下发（有 scan 产物才有事件可上送）。
    """
    from backend.realtime.socketio_server import emit_agent_control

    logger.info("saq_upload_start plan_run=%d", plan_run_id)

    try:
        hosts_with_scan, host_rows, event_dir_names = await asyncio.to_thread(
            _query_hosts_for_upload, plan_run_id,
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

            await emit_agent_control(
                host_id, "upload_events",
                payload={
                    "plan_run_id": plan_run_id,
                    "event_dir_names": event_dir_names,
                },
            )
            triggered.append(host_id)

        logger.info(
            "saq_upload_dispatched plan_run=%d triggered=%d skipped=%d event_dirs=%d",
            plan_run_id, len(triggered), len(skipped), len(event_dir_names),
        )
    except Exception:
        logger.exception("saq_upload_failed plan_run=%d", plan_run_id)
        raise

    logger.info("saq_upload_done plan_run=%d", plan_run_id)


async def _enqueue_extract_task(plan_run_id: int) -> None:
    """enqueue extract_task（§9 时序：extract 依赖 upload + merge 均完成）。"""
    from backend.tasks.saq_worker import get_queue
    from saq import Job as SaqJob

    queue = get_queue()
    await queue.enqueue(
        SaqJob(
            function="extract_task",
            kwargs={"plan_run_id": plan_run_id},
            key=f"extract:{plan_run_id}",
            timeout=300,
            retries=2,
            retry_delay=10.0,
            retry_backoff=True,
        )
    )
    logger.info("saq_enqueued_extract plan_run=%d", plan_run_id)


async def _wait_for_upload_task(plan_run_id: int) -> bool:
    """poll upload:{plan_run_id} SAQ job 直至终态；超时返回 False（仍允许 best-effort extract）。"""
    from backend.tasks.saq_worker import get_saq_job_state_sync

    key = f"upload:{plan_run_id}"
    elapsed = 0
    while elapsed < _UPLOAD_WAIT_MAX:
        state = await asyncio_to_thread(get_saq_job_state_sync, key)
        if state is not None:
            status = state.get("status")
            if status in _TERMINAL_SAQ_STATUSES:
                logger.info(
                    "saq_merge_upload_ready plan_run=%d upload_status=%s waited=%ds",
                    plan_run_id, status, elapsed,
                )
                return True
            logger.info(
                "saq_merge_upload_wait plan_run=%d upload_status=%s elapsed=%ds",
                plan_run_id, status, elapsed,
            )
        await asyncio_sleep(_UPLOAD_WAIT_INTERVAL)
        elapsed += _UPLOAD_WAIT_INTERVAL

    logger.warning(
        "saq_merge_upload_wait_timeout plan_run=%d waited=%ds",
        plan_run_id, elapsed,
    )
    return False


def _count_devices_event_dirs_sync(plan_run_id: int) -> int:
    """统计 NFS devices/{plan_run_id}/ 下时间戳事件目录数。"""
    import os
    from pathlib import Path

    from backend.agent.aee.event_dirs import is_event_dir_basename

    nfs_root = os.getenv("STP_AEE_NFS_ROOT", os.getenv("STP_WATCHER_NFS_BASE_DIR", "")).strip()
    if not nfs_root:
        return 0
    devices_dir = Path(nfs_root) / "devices" / str(plan_run_id)
    if not devices_dir.is_dir():
        return 0
    return sum(
        1 for p in devices_dir.iterdir()
        if p.is_dir() and is_event_dir_basename(p.name)
    )


async def _wait_for_devices_on_nfs(plan_run_id: int) -> int:
    """poll NFS devices/{plan_run_id}/ 直至出现事件目录；超时返回 0（best-effort extract）。"""
    elapsed = 0
    while elapsed < _DEVICES_POLL_MAX:
        count = await asyncio_to_thread(_count_devices_event_dirs_sync, plan_run_id)
        if count > 0:
            logger.info(
                "saq_merge_devices_ready plan_run=%d dirs=%d waited=%ds",
                plan_run_id, count, elapsed,
            )
            return count
        logger.info(
            "saq_merge_devices_wait plan_run=%d elapsed=%ds",
            plan_run_id, elapsed,
        )
        await asyncio_sleep(_DEVICES_POLL_INTERVAL)
        elapsed += _DEVICES_POLL_INTERVAL

    logger.warning(
        "saq_merge_devices_wait_timeout plan_run=%d waited=%ds",
        plan_run_id, elapsed,
    )
    return 0


async def merge_task(ctx: dict, *, plan_run_id: int) -> None:
    """ADR-0025 Sprint 4: 归档-2 集中合并（-merge_files 各 agent _org.xls）。"""
    from backend.services.dedup_scan import run_merge_sync

    logger.info("saq_merge_start plan_run=%d", plan_run_id)
    try:
        result = await asyncio.to_thread(run_merge_sync, plan_run_id)
    except Exception:
        logger.exception("saq_merge_failed plan_run=%d", plan_run_id)
        raise
    logger.info("saq_merge_done plan_run=%d", plan_run_id)

    if result != "ok":
        logger.info(
            "saq_merge_skip_extract plan_run=%d result=%r",
            plan_run_id, result,
        )
        return

    upload_ready = await _wait_for_upload_task(plan_run_id)
    if not upload_ready:
        logger.warning(
            "saq_merge_extract_best_effort plan_run=%d reason=upload_saq_timeout",
            plan_run_id,
        )

    n_devices = await _wait_for_devices_on_nfs(plan_run_id)
    if n_devices == 0:
        logger.warning(
            "saq_merge_extract_best_effort plan_run=%d reason=devices_empty_or_timeout",
            plan_run_id,
        )

    try:
        await _enqueue_extract_task(plan_run_id)
    except Exception as e:
        logger.error(
            "saq_merge_enqueue_extract_failed plan_run=%d: %s",
            plan_run_id, e,
        )


def _run_extract_sync(plan_run_id: int) -> int:
    """同步执行 extract（NFS 文件拷贝），由 asyncio.to_thread 调用。"""
    from backend.services.dedup_extract import run_extract_sync

    return run_extract_sync(plan_run_id)


async def extract_task(ctx: dict, *, plan_run_id: int) -> None:
    """ADR-0025 Sprint 4 归档-3: copy devices/ + merge xls → jira/{plan_run_id}/

    所有同步文件 IO（NFS 拷贝、DB 查询）通过 asyncio.to_thread 在线程池中执行，
    不阻塞事件循环。NFS 挂载点超时/中断时事件循环保持响应。
    """
    logger.info("saq_extract_start plan_run=%d", plan_run_id)
    await asyncio.to_thread(_run_extract_sync, plan_run_id)


SAQ_FUNCTIONS = [
    post_completion_task,
    send_notification_task,
    publish_control_command,
    precheck_and_dispatch_task,
    scan_task,
    upload_task,
    merge_task,
    extract_task,
]
