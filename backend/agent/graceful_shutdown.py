"""Agent graceful shutdown sequence extracted from ``main`` (#736).

Stop order is load-bearing (#784): archiver / disk monitor / event uploader
must stop outside the watcher ``log_signal_drainer`` branch so a disabled
watcher cannot leave daemons ticking after ``local_db.close()``.
"""

from __future__ import annotations

import logging
from concurrent.futures import Executor
from typing import Any, Optional

from .artifact_uploader import ArtifactUploader
from .event_uploader import EventUploader
from .local_disk_monitor import LocalDiskMonitor
from .log_archiver import LogArchiver

logger = logging.getLogger(__name__)


def shutdown_agent_runtime(
    *,
    coordinator: Any,
    operation_scheduler: Any,
    job_runner_state: Optional[Any],
    executor: Executor,
    step_trace_uploader: Any,
    outbox_drain: Any,
    log_signal_drainer: Any,
    recovery_sync_stop: Any,
    recovery_sync_thread: Any,
    heartbeat_thread: Any,
    lease_renewer: Any,
    mq_producer: Any,
    local_db: Any,
    sio_client: Any,
) -> None:
    """Drain in-flight work and stop background subsystems (SIGTERM/SIGINT)."""
    logger.info("agent_shutting_down, waiting for active tasks to finish...")
    coordinator.stop()
    operation_scheduler.shutdown()
    # R07-F12 (#1012): scheduler.shutdown() only wakes permit waiters; also
    # cancel already-running cruises so the executor drain below is bounded
    # and SIGTERM actually ends a patrol loop instead of retrying it.
    if job_runner_state is not None:
        try:
            for _jid in list(job_runner_state.active_runners):
                job_runner_state.request_abort(_jid)
        except Exception:
            logger.exception("shutdown_cancel_runners_failed")
    executor.shutdown(wait=True, cancel_futures=False)
    # Flush step traces via HTTP before shutdown
    try:
        flushed = step_trace_uploader.drain_sync()
        if flushed:
            logger.info("shutdown_step_trace_flushed count=%d", flushed)
    except Exception:
        logger.exception("shutdown_step_trace_flush_failed")
    step_trace_uploader.stop()
    # Final outbox drain: flush any un-acked terminal states
    try:
        flushed = outbox_drain.drain_sync()
        if flushed:
            logger.info("shutdown_outbox_flushed count=%d", flushed)
    except Exception:
        logger.exception("shutdown_outbox_flush_failed")
    outbox_drain.stop()
    # #784: LogArchiver / LocalDiskMonitor / EventUploader 在 watcher 门控
    # 之外启动——停机必须同作用域，不能包进 log_signal_drainer 分支，否则
    # watcher 禁用时 local_db.close() 后 daemon 仍 tick → LocalDB is closed。
    try:
        LocalDiskMonitor.instance().stop(timeout=5.0)
    except Exception:
        logger.exception("shutdown_local_disk_monitor_stop_failed")
    try:
        LogArchiver.instance().stop(timeout=5.0)
    except Exception:
        logger.exception("shutdown_log_archiver_stop_failed")
    try:
        EventUploader.instance().stop(timeout=5.0)
    except Exception:
        logger.exception("shutdown_event_uploader_stop_failed")
    # log_signal_outbox drainer + ArtifactUploader（仅 watcher 子系统启用时）
    if log_signal_drainer is not None:
        try:
            flushed = log_signal_drainer.tick_once()
            if flushed:
                logger.info("shutdown_log_signal_flushed count=%d", flushed)
        except Exception:
            logger.exception("shutdown_log_signal_flush_failed")
        log_signal_drainer.stop(timeout=5.0)
        try:
            ArtifactUploader.instance().stop(drain=True, timeout=5.0)
        except Exception:
            logger.exception("shutdown_artifact_uploader_stop_failed")
    recovery_sync_stop.set()
    try:
        recovery_sync_thread.join(timeout=5.0)
    except Exception:
        logger.exception("shutdown_recovery_sync_join_failed")
    heartbeat_thread.stop()
    lease_renewer.stop()
    mq_producer.close()
    local_db.close()
    sio_client.disconnect()
    logger.info("agent_shutdown_complete")
