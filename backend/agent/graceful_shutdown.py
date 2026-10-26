"""Agent graceful shutdown sequence extracted from ``main`` (#736).

Stop order is load-bearing (#784): archiver / disk monitor / event uploader
must stop outside the watcher ``log_signal_drainer`` branch so a disabled
watcher cannot leave daemons ticking after ``local_db.close()``.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import Executor
from typing import Any, Optional

from .api_client import _build_complete_payload
from .artifact_uploader import ArtifactUploader
from .contracts.device_return import (
    DISARM_RESULT_CONFIRMED,
    DISARM_RESULT_EXECUTED_UNVERIFIED,
    DISARM_RESULT_FAILED,
    DISARM_RESULT_INCOMPLETE,
    DISARM_RESULT_READBACK_FAILED,
    make_disarm_result,
)
from .event_uploader import EventUploader
from .local_disk_monitor import LocalDiskMonitor
from .log_archiver import LogArchiver

logger = logging.getLogger(__name__)

# Family results that already finished during the disarm window — keep as-is
# when persisting leftover jobs at shutdown cut-off (#3723 §3.2.4).
_FINISHED_DISARM_RESULTS = frozenset(
    {
        DISARM_RESULT_CONFIRMED,
        DISARM_RESULT_EXECUTED_UNVERIFIED,
        DISARM_RESULT_FAILED,
        DISARM_RESULT_READBACK_FAILED,
    }
)

_SHUTDOWN_INCOMPLETE_REASON = "shutdown_disarm_window_elapsed"



def _await_started_disarm(
    job_runner_state: Optional[Any],
    *,
    timeout_seconds: float = 20.0,
) -> None:
    """Wait up to ``timeout_seconds`` for active runners (disarm) to finish.

    Polls ``active_runners``; returns early when empty. Incomplete disarm after
    the window is marked ``incomplete`` when the scheduler shuts down / workers
    exit (#3723 §3.2.4).
    """
    if job_runner_state is None or timeout_seconds <= 0:
        return
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            active = getattr(job_runner_state, "active_runners", None) or {}
            if not active:
                return
        except Exception:
            logger.exception("shutdown_disarm_wait_inspect_failed")
            return
        time.sleep(0.2)
    try:
        leftover = list(getattr(job_runner_state, "active_runners", {}) or {})
    except Exception:
        leftover = []
    if leftover:
        logger.warning(
            "shutdown_disarm_window_elapsed leftover_jobs=%s timeout=%.1fs",
            leftover,
            timeout_seconds,
        )


def _iter_leftover_runners(
    job_runner_state: Any,
) -> list[tuple[int, Any]]:
    """Snapshot ``active_runners`` entries still present after the disarm window."""
    try:
        active = dict(getattr(job_runner_state, "active_runners", None) or {})
    except Exception:
        logger.exception("shutdown_incomplete_outbox_inspect_failed")
        return []
    out: list[tuple[int, Any]] = []
    for job_id, entry in active.items():
        runner: Any = entry
        if isinstance(entry, tuple) and len(entry) >= 2:
            runner = entry[1]
        out.append((int(job_id), runner))
    return out


def _disarm_results_for_shutdown_incomplete(runner: Any) -> dict[str, dict]:
    """Build per-family disarm_results marking unfinished families incomplete."""
    families = set(getattr(runner, "_footprint_families", None) or set())
    existing = dict(getattr(runner, "_disarm_results", None) or {})
    if not families:
        families = set(existing.keys())
    if not families:
        return {}
    results: dict[str, dict] = {}
    for family in families:
        prior = existing.get(family) or {}
        prior_result = prior.get("result")
        if prior_result in _FINISHED_DISARM_RESULTS:
            results[family] = dict(prior)
            continue
        results[family] = make_disarm_result(
            result=DISARM_RESULT_INCOMPLETE,
            step_id=str(prior.get("step_id") or ""),
            exit_code=prior.get("exit_code"),
            readback_summary=str(prior.get("readback_summary") or ""),
            reason=_SHUTDOWN_INCOMPLETE_REASON,
        )
    return results


def _persist_incomplete_disarm_to_outbox(
    job_runner_state: Optional[Any],
    local_db: Any,
    lease_renewer: Optional[Any] = None,
) -> int:
    """Write ``incomplete`` terminal facts for leftover jobs before LocalDB close.

    #3723 §3.2.4: after abort → ≤20s → scheduler.shutdown, unfinished disarm
    must land in the local outbox while the DB is still open. ``executor.shutdown
    (wait=False)`` intentionally does not wait for workers, so this path is the
    durability guarantee — not a late worker callback after ``local_db.close()``.
    """
    if job_runner_state is None or local_db is None:
        return 0
    enqueue = getattr(local_db, "enqueue_terminal", None)
    if not callable(enqueue):
        return 0
    leftovers = _iter_leftover_runners(job_runner_state)
    if not leftovers:
        return 0
    # Worker tokens identify local runners, not server leases (#3795).
    # Prefer the durable claim registry; use the renewer's lease cache only
    # when that job has no persisted lease token. Both are live before stop().
    try:
        lease_tokens = {
            int(entry["job_id"]): entry.get("fencing_token")
            for entry in local_db.get_active_jobs()
        }
    except Exception:
        logger.exception("shutdown_incomplete_outbox_registry_read_failed")
        lease_tokens = {}
    persisted = 0
    for job_id, runner in leftovers:
        disarm_results = _disarm_results_for_shutdown_incomplete(runner)
        if not disarm_results:
            continue
        fencing_token = lease_tokens.get(job_id)
        if not isinstance(fencing_token, str) or not fencing_token:
            get_token = getattr(lease_renewer, "get_fencing_token", None)
            fencing_token = get_token(job_id) if callable(get_token) else None
        if not isinstance(fencing_token, str) or not fencing_token:
            logger.warning(
                "shutdown_incomplete_outbox_skip_no_token job=%d", job_id,
            )
            continue
        payload = _build_complete_payload(
            {
                "status": "CANCELED",
                "exit_code": 0,
                "error_message": _SHUTDOWN_INCOMPLETE_REASON,
                "disarm_results": disarm_results,
            },
            fencing_token,
        )
        try:
            enqueue(job_id, payload)
            persisted += 1
            logger.info(
                "shutdown_incomplete_outbox_enqueued job=%d families=%s",
                job_id,
                sorted(disarm_results),
            )
        except ValueError as exc:
            # First terminal fact already durable (worker won the race).
            logger.info(
                "shutdown_incomplete_outbox_conflict job=%d: %s", job_id, exc,
            )
        except Exception:
            logger.exception(
                "shutdown_incomplete_outbox_failed job=%d", job_id,
            )
    return persisted



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
    # #3723 §3.2.4 / ADR-0061 D3: abort in-flight jobs FIRST so started disarm
    # steps can finish; wait up to 20s; then shut down the scheduler. Do not
    # change systemd TimeoutStopSec (unit stays 30s).
    if job_runner_state is not None:
        try:
            for _jid in list(job_runner_state.active_runners):
                job_runner_state.request_abort(_jid)
        except Exception:
            logger.exception("shutdown_cancel_runners_failed")
    _await_started_disarm(job_runner_state, timeout_seconds=20.0)
    # R07-F12 (#1012): scheduler.shutdown() wakes remaining permit waiters
    # (teardown waiters record incomplete → local outbox via complete_job).
    operation_scheduler.shutdown()
    # #3723 §3.2.4: after the 20s disarm window, must enter outbox drain
    # boundedly. Teardown no longer kills children (§3.2.2), so wait=True
    # can hang past systemd's 30s TimeoutStopSec and skip outbox flush.
    # wait=False returns immediately; worker threads may still finish
    # enqueueing incomplete while we drain what is already durable.
    executor.shutdown(wait=False, cancel_futures=False)
    # #3723 §3.2.4: leftovers after the 20s window → incomplete into local
    # outbox BEFORE drain/close. wait=False leaves workers running; closing
    # LocalDB first drops late complete_job enqueue (TerminalReportLostError
    # with empty outbox). Persist here so drain has something durable.
    try:
        n = _persist_incomplete_disarm_to_outbox(
            job_runner_state, local_db, lease_renewer=lease_renewer,
        )
        if n:
            logger.info("shutdown_incomplete_outbox_persisted count=%d", n)
    except Exception:
        logger.exception("shutdown_incomplete_outbox_persist_failed")
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
