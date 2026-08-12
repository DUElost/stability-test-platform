# -*- coding: utf-8 -*-
"""
SAQ task functions — async jobs processed by the in-process SAQ worker.

Each function receives a SAQ context dict as the first positional argument
and keyword arguments that were passed at enqueue time.
"""

import logging
import asyncio
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

asyncio_sleep = asyncio.sleep
asyncio_to_thread = asyncio.to_thread

_MERGE_SYNC_TIMEOUT = 300
_UPLOAD_WAIT_INTERVAL = 5
_UPLOAD_WAIT_MAX = 660  # DLE pending poll budget (merge_task waits on REMOTE/ARCHIVED)
# merge 子进程 + DLE wait + 余量
_MERGE_TASK_SAQ_TIMEOUT = _MERGE_SYNC_TIMEOUT + _UPLOAD_WAIT_MAX + 120


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


async def plan_admission_task(ctx: dict, *, plan_run_id: int, attempt_id: str) -> None:
    """ADR-0026 Step 4 — one V2 admission attempt (slow verify + short tx)."""
    from backend.services.admission_pump import plan_admission_task as _impl
    await _impl(ctx, plan_run_id=plan_run_id, attempt_id=attempt_id)


def _query_hosts_for_scan(
    plan_run_id: int, is_final: bool = False,
) -> tuple[list[tuple[str, dict]], list[str]]:
    """同步查询 scan_task 所需的 host + scan_now payload，由 asyncio.to_thread 调用。

    下发对象是本 PlanRun 涉及的全部 host；每台收到同一份跨主机设备名单。
    """
    from backend.core.database import SessionLocal
    from backend.services.plan_run_scan_scope import (
        build_scan_now_payload,
        iter_plan_run_scan_hosts,
    )

    db = SessionLocal()
    try:
        triggered: list[tuple[str, dict]] = []
        skipped: list[str] = []
        for host_id, host_status in iter_plan_run_scan_hosts(db, plan_run_id):
            if host_status == "ONLINE":
                triggered.append((
                    host_id,
                    build_scan_now_payload(
                        db, plan_run_id, host_id, is_final=is_final,
                    ),
                ))
            else:
                skipped.append(host_id)
        return triggered, skipped
    finally:
        db.close()


async def scan_task(ctx: dict, *, plan_run_id: int, is_final: bool = False) -> None:
    """ADR-0025 Sprint 4: 归档-2 向各 ONLINE agent 下发 scan_now → 轮询 NFS → 注册 DB → enqueue merge。

    1. emit scan_now to each ONLINE agent
    2. poll NFS dedup/{plan_run_id}/ for *_org.xls files (max 300s)
    3. call run_scan_sync to register artifacts in plan_run_artifact
    4. enqueue merge_task (merge_task chains extract_task on success; events via EventUploader/DLE)
    """
    from backend.realtime.socketio_server import emit_agent_control

    logger.info("saq_scan_start plan_run=%d final=%s", plan_run_id, is_final)

    # Watermark for this round's completeness check, taken before scan_now goes
    # out: artifacts registered earlier belong to a previous incremental round.
    round_started_at = datetime.now(timezone.utc)
    scan_round_id = round_started_at.isoformat()

    try:
        triggered_rows, skipped = await asyncio.to_thread(
            _query_hosts_for_scan, plan_run_id, is_final,
        )
        triggered = [host_id for host_id, _payload in triggered_rows]
        for host_id, payload in triggered_rows:
            await emit_agent_control(host_id, "scan_now", payload=payload)

        logger.info(
            "saq_scan_dispatched plan_run=%d triggered=%d skipped=%d",
            plan_run_id, len(triggered), len(skipped),
        )
    except Exception:
        logger.exception("saq_scan_failed plan_run=%d", plan_run_id)
        raise

    if triggered:
        from backend.services.dedup_scan import (
            count_hosts_with_scan_artifacts,
            record_scan_archive_state,
            run_scan_sync,
        )

        _SCAN_POLL_INTERVAL = 10
        _SCAN_POLL_MAX_WAIT = 300
        elapsed = 0
        registered = 0
        hosts_done = 0
        n_triggered = len(triggered)
        # Completeness is counted per host, scoped to this round's triggered set,
        # and bounded below by round_started_at: each host uploads 2 matching
        # files, and incremental scans reuse the plan_run_id, so neither a file
        # count nor a run-wide host count nor a host's earlier-round artifacts
        # mean "every host we just asked has delivered this time".
        while elapsed < _SCAN_POLL_MAX_WAIT:
            await asyncio_sleep(_SCAN_POLL_INTERVAL)
            elapsed += _SCAN_POLL_INTERVAL
            n_new = await asyncio_to_thread(
                run_scan_sync, plan_run_id, scan_round_id=scan_round_id,
            )
            if n_new:
                registered += int(n_new)
            hosts_done = await asyncio_to_thread(
                count_hosts_with_scan_artifacts, plan_run_id, triggered,
                since=round_started_at,
            )
            if hosts_done >= n_triggered:
                break
            logger.info(
                "saq_scan_poll plan_run=%d elapsed=%ds hosts=%d/%d artifacts=%d",
                plan_run_id, elapsed, hosts_done, n_triggered, registered,
            )

        # Poll exhausted with some hosts still missing: retry once so an _org.xls
        # that landed inside the last interval still gets registered and merged.
        if hosts_done < n_triggered:
            n_final = await asyncio_to_thread(
                run_scan_sync, plan_run_id, scan_round_id=scan_round_id,
            )
            if n_final:
                registered += int(n_final)
                hosts_done = await asyncio_to_thread(
                    count_hosts_with_scan_artifacts, plan_run_id, triggered,
                    since=round_started_at,
                )

        logger.info(
            "saq_scan_registered plan_run=%d hosts=%d/%d artifacts=%d waited=%ds",
            plan_run_id, hosts_done, n_triggered, registered, elapsed,
        )

        # Agent-side scan failures only log locally, so a fleet-wide
        # misconfiguration otherwise ends as a SUCCESS run with no report at all.
        if hosts_done == 0:
            logger.error(
                "saq_scan_no_artifacts plan_run=%d hosts_triggered=%d waited=%ds",
                plan_run_id, n_triggered, elapsed,
            )
        elif hosts_done < n_triggered:
            # Deliberately still chained: a report covering the hosts that did
            # deliver beats no report at all, which is the failure mode this
            # whole path exists to remove. The shortfall has to be loud instead.
            logger.warning(
                "saq_scan_partial_artifacts plan_run=%d hosts=%d/%d waited=%ds",
                plan_run_id, hosts_done, n_triggered, elapsed,
            )
        await asyncio_to_thread(
            record_scan_archive_state,
            plan_run_id,
            hosts_triggered=n_triggered,
            artifacts_registered=registered,
            hosts_with_artifacts=hosts_done,
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
        merge_kwargs = {
            "plan_run_id": plan_run_id,
            "scan_round_id": scan_round_id,
            "round_started_at": round_started_at.isoformat(),
        }
        logger.info("saq_scan_enqueue_upload_and_merge plan_run=%d", plan_run_id)
        await queue.enqueue(
            SaqJob(
                function="merge_task",
                kwargs=merge_kwargs,
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


async def upload_task(ctx: dict, *, plan_run_id: int) -> None:
    """ADR-0028 方案 A：scan 后标记有效事件为 UPLOAD_PENDING，由 Agent EventUploader 上送。

    只上送 scan xls Path 列引用的有效事件（过滤模型——CIFS 只收精选子集）。
    EventUploader 轮询 state=UPLOAD_PENDING 的事件并执行 copytree。
    """
    from backend.core.database import SessionLocal
    from backend.services.dedup_extract import collect_upload_event_dir_names

    logger.info("saq_upload_start plan_run=%d", plan_run_id)

    try:
        def _mark() -> int:
            db = SessionLocal()
            try:
                event_dir_names = collect_upload_event_dir_names(db, plan_run_id)
                if not event_dir_names:
                    logger.info("saq_upload_no_event_dirs plan_run=%d", plan_run_id)
                    return 0

                # Build LIKE patterns from dir basenames
                from sqlalchemy import text as sa_text
                patterns = [f"%/{d}" for d in event_dir_names]
                clauses = " OR ".join(["device_log_event.local_path LIKE :p%d" % i for i in range(len(patterns))])
                params = {"p%d" % i: p for i, p in enumerate(patterns)}

                result = db.execute(
                    sa_text(
                        f"UPDATE device_log_event SET state = 'UPLOAD_PENDING', "
                        f"updated_at = now() "
                        f"WHERE state = 'LOCAL' AND plan_run_id = :pid AND ({clauses})"
                    ),
                    {"pid": plan_run_id, **params},
                )
                db.commit()
                marked = result.rowcount
                logger.info(
                    "saq_upload_marked plan_run=%d marked=%d dirs=%d",
                    plan_run_id, marked, len(event_dir_names),
                )
                return marked
            finally:
                db.close()

        marked = await asyncio_to_thread(_mark)
    except Exception:
        logger.exception("saq_upload_failed plan_run=%d", plan_run_id)
        raise

    logger.info("saq_upload_done plan_run=%d marked=%d", plan_run_id, marked)


async def _enqueue_extract_task(plan_run_id: int) -> None:
    """enqueue extract_task（merge 成功后等待 DLE REMOTE，再链式 extract）。"""
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


async def _wait_for_remote_device_log_events(plan_run_id: int) -> bool:
    """连续上送模式：轮询 plan_run 关联事件是否全部 REMOTE/ARCHIVED。"""
    from backend.core.database import SessionLocal
    from backend.services.device_log_event import count_pending_upload_events

    elapsed = 0
    while elapsed < _UPLOAD_WAIT_MAX:
        def _pending() -> int:
            db = SessionLocal()
            try:
                return count_pending_upload_events(db, plan_run_id)
            finally:
                db.close()

        pending = await asyncio_to_thread(_pending)
        if pending == 0:
            return True
        logger.info(
            "saq_upload_continuous_wait plan_run=%d pending=%d elapsed=%ds",
            plan_run_id, pending, elapsed,
        )
        await asyncio_sleep(_UPLOAD_WAIT_INTERVAL)
        elapsed += _UPLOAD_WAIT_INTERVAL
    return False


async def _count_remote_device_log_events(plan_run_id: int) -> int:
    from backend.core.database import SessionLocal
    from backend.services.device_log_event import count_remote_events

    def _count() -> int:
        db = SessionLocal()
        try:
            return count_remote_events(db, plan_run_id)
        finally:
            db.close()

    return await asyncio_to_thread(_count)


async def merge_task(
    ctx: dict,
    *,
    plan_run_id: int,
    scan_round_id: str | None = None,
    round_started_at: str | None = None,
) -> None:
    """ADR-0025 Sprint 4: 归档-2 集中合并（-merge_files 各 agent _org.xls）。"""
    from backend.services.dedup_scan import run_merge_sync

    round_dt = None
    if round_started_at:
        try:
            round_dt = datetime.fromisoformat(round_started_at.replace("Z", "+00:00"))
        except ValueError:
            logger.warning(
                "saq_merge_invalid_round_started_at plan_run=%d value=%r",
                plan_run_id, round_started_at,
            )

    logger.info("saq_merge_start plan_run=%d round=%s", plan_run_id, scan_round_id)
    try:
        result = await asyncio.to_thread(
            run_merge_sync,
            plan_run_id,
            scan_round_id=scan_round_id,
            round_started_at=round_dt,
        )
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

    upload_ready = await _wait_for_remote_device_log_events(plan_run_id)
    if not upload_ready:
        logger.warning(
            "saq_merge_extract_best_effort plan_run=%d reason=upload_not_ready",
            plan_run_id,
        )

    n_devices = await _count_remote_device_log_events(plan_run_id)
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


async def install_agent_task(
    ctx: dict,
    *,
    host_id: str,
    initiated_by: str | None = None,
    console_run_id: str | None = None,
) -> dict:
    """UI 触发的 Agent 首次安装：ansible-playbook install_agent.yml（become 喂 sudo 密码）。

    装完 install_agent.sh 自动落 /etc/sudoers.d/stability-test-agent NOPASSWD，
    解锁后续 UI 热更新（execute_hot_update）的免密 sudo rsync/systemctl。
    """
    from backend.services.agent_installer import run_install_agent_sync

    logger.info(
        "saq_install_agent_start host=%s by=%s console_run_id=%s",
        host_id,
        initiated_by,
        console_run_id,
    )
    try:
        result = await asyncio.to_thread(
            run_install_agent_sync,
            host_id,
            initiated_by,
            console_run_id=console_run_id,
        )
    except Exception:
        logger.exception("saq_install_agent_failed host=%s", host_id)
        raise

    # Record audit outcome (best-effort).
    try:
        from backend.core.audit import record_audit
        from backend.core.database import SessionLocal
        from backend.models.host import Host
        db = SessionLocal()
        try:
            host = db.get(Host, host_id)
            if host and result.get("ok"):
                from datetime import datetime, timezone

                extra = dict(host.extra or {})
                extra["agent_installed"] = True
                extra["agent_installed_at"] = datetime.now(timezone.utc).isoformat()
                host.extra = extra
            record_audit(
                db,
                action="install_agent",
                resource_type="host",
                resource_id=host_id,
                details={
                    "host_id": host_id,
                    "ip": host.ip if host else None,
                    "ok": bool(result.get("ok")),
                    "rc": result.get("rc"),
                    "log_path": result.get("log_path"),
                    "console_run_id": result.get("console_run_id"),
                    "message": result.get("message"),
                    "initiated_by": initiated_by,
                },
                username=initiated_by,
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.warning("install_agent_audit_failed host=%s", host_id, exc_info=True)

    logger.info("saq_install_agent_done host=%s ok=%s", host_id, result.get("ok"))
    return result


SAQ_FUNCTIONS = [
    post_completion_task,
    send_notification_task,
    publish_control_command,
    precheck_and_dispatch_task,
    plan_admission_task,
    scan_task,
    merge_task,
    extract_task,
    install_agent_task,
]
