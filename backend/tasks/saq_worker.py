# -*- coding: utf-8 -*-
"""
SAQ worker lifecycle — Queue singleton + in-process Worker management.

The worker runs inside the FastAPI process as a background asyncio task,
sharing the same event loop.  ``start_saq_worker`` / ``stop_saq_worker``
are called from the FastAPI lifespan.

``enqueue_sync`` bridges synchronous callers (recycler running in an
APScheduler thread) into the async SAQ queue via the stored event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

import redis.asyncio as aioredis
from saq import Job, Queue, Worker

from backend.tasks.saq_tasks import SAQ_FUNCTIONS
from backend.core.metrics import record_saq_task

logger = logging.getLogger(__name__)

_SAQ_JOB_START_KEY = "_saq_metric_start"

_queue: Optional[Queue] = None
_worker: Optional[Worker] = None
_worker_task: Optional[asyncio.Task] = None
_loop: Optional[asyncio.AbstractEventLoop] = None

SAQ_CONCURRENCY = int(os.getenv("SAQ_CONCURRENCY", "10"))
SAQ_QUEUE_NAME = os.getenv("SAQ_QUEUE_NAME", "stp")
SAQ_ENQUEUE_WAIT_TIMEOUT = float(os.getenv("SAQ_ENQUEUE_WAIT_TIMEOUT", "5.0"))
REDIS_PING_TIMEOUT = float(os.getenv("REDIS_PING_TIMEOUT", "3.0"))


def get_queue() -> Queue:
    """Return the SAQ Queue singleton.  Raises if not initialised."""
    if _queue is None:
        raise RuntimeError(
            "SAQ queue not initialised — call init_saq_producer or start_saq_worker first"
        )
    return _queue


async def _before_process(ctx: dict) -> None:
    """SAQ hook: record job start time for duration measurement."""
    ctx[_SAQ_JOB_START_KEY] = time.monotonic()


async def _after_process(ctx: dict) -> None:
    """SAQ hook: record task metrics after job completion."""
    job: Job | None = ctx.get("job")
    if job is None:
        return
    task_name = job.function or "unknown"
    status = job.status.value if hasattr(job.status, "value") else str(job.status)
    start = ctx.pop(_SAQ_JOB_START_KEY, None)
    duration = time.monotonic() - start if start is not None else 0.0
    record_saq_task(task_name, status, duration)


def _on_worker_task_done(task: "asyncio.Task") -> None:
    """SAQ worker task exited — revoke admission-pump readiness (ADR-0026
    Step 4.1 hardening review).

    An unexpectedly dead worker means claimed PRECHECK runs can still be
    recovered by the reaper, but V2 prepare must stop minting NEW QUEUED runs
    that nothing will admit. Graceful stop also lands here (unmark is
    idempotent; the lifespan shutdown already unmarked first).
    """
    try:
        from backend.core.admission_queue import mark_queue_pump_ready
        mark_queue_pump_ready(False)
    except Exception:
        logger.debug("pump_ready_revoke_failed", exc_info=True)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("saq_worker_task_died — %s", exc)


async def verify_redis_connectivity(
    redis_url: str | None = None,
    *,
    timeout: float | None = None,
) -> None:
    """Ping Redis before SAQ worker startup. Raises RuntimeError on failure."""
    url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
    ping_timeout = REDIS_PING_TIMEOUT if timeout is None else timeout
    client = await aioredis.from_url(url, encoding="utf-8", decode_responses=True)
    try:
        await asyncio.wait_for(client.ping(), timeout=ping_timeout)
    except Exception as exc:
        raise RuntimeError(f"Redis unreachable at {url}: {exc}") from exc
    finally:
        await client.aclose()


def is_saq_producer_ready() -> bool:
    """True when the SAQ queue singleton is connected (enqueue path usable)."""
    return _queue is not None and _loop is not None


def is_saq_ready() -> bool:
    """True when enqueue is usable for admission / background tasks.

    - In-process worker mode (``STP_ENABLE_INPROCESS_SAQ=1``): queue connected
      **and** worker task alive.
    - External-worker mode (``STP_ENABLE_INPROCESS_SAQ=0``): producer connected
      is enough — an external process drains the same Redis queue.
    """
    if not is_saq_producer_ready():
        return False
    if os.getenv("STP_ENABLE_INPROCESS_SAQ", "1") == "1":
        return _worker_task is not None and not _worker_task.done()
    return True


async def init_saq_producer() -> None:
    """Connect the SAQ queue without starting an in-process worker.

    ADR-0026 P0: allows ``STP_ENABLE_INPROCESS_SAQ=0`` so enqueue / admission
    pump keep working while an external SAQ worker drains Redis.
    Idempotent when the queue is already connected.
    """
    global _queue, _loop

    if _queue is not None:
        if _loop is None:
            _loop = asyncio.get_running_loop()
        logger.info("saq_producer_start_skip already_connected")
        return

    _loop = asyncio.get_running_loop()
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    _queue = Queue.from_url(redis_url, name=SAQ_QUEUE_NAME)
    await _queue.connect()
    logger.info("saq_producer_started queue=%s", SAQ_QUEUE_NAME)


async def stop_saq_producer() -> None:
    """Disconnect the SAQ queue (producer-only / external-worker shutdown)."""
    global _queue, _loop

    if _queue is not None:
        try:
            await _queue.disconnect()
        except Exception:
            logger.debug("saq_producer_disconnect_error", exc_info=True)
        _queue = None
    _loop = None
    logger.info("saq_producer_stopped")


async def start_saq_worker() -> None:
    """Connect the queue and launch the SAQ worker as a background task.

    Idempotent: if the worker is already running, this is a no-op.  If a
    previous worker task exists but has exited, the old queue is
    disconnected before reconnecting.
    """
    global _queue, _worker, _worker_task, _loop

    if _queue is not None and _worker_task is not None and not _worker_task.done():
        logger.info("saq_worker_start_skip already_running")
        return

    # Drop a stale queue/worker so reconnect is clean (crash restart path).
    if _queue is not None:
        try:
            await _queue.disconnect()
        except Exception:
            logger.debug("saq_worker_disconnect_previous_error", exc_info=True)
        _queue = None
    _worker = None
    _worker_task = None
    _loop = None

    await init_saq_producer()

    _worker = Worker(
        _queue,
        functions=SAQ_FUNCTIONS,
        concurrency=SAQ_CONCURRENCY,
        before_process=_before_process,
        after_process=_after_process,
    )
    _worker_task = asyncio.create_task(_worker.start(), name="saq-worker")
    _worker_task.add_done_callback(_on_worker_task_done)
    # ADR-0026 Step 5a.1: SAQ worker is the admission executor — mark the pump
    # ready every time this worker starts (first boot + health-supervisor
    # restart). Idempotent with the main.py lifespan call; the done-callback
    # above unmarks it on exit.
    try:
        from backend.core.admission_queue import mark_queue_pump_ready
        mark_queue_pump_ready(True)
    except Exception:
        logger.debug("pump_ready_mark_failed", exc_info=True)
    logger.info(
        "saq_worker_started concurrency=%d queue=%s",
        SAQ_CONCURRENCY,
        SAQ_QUEUE_NAME,
    )


async def stop_saq_worker() -> None:
    """Gracefully stop the SAQ worker and disconnect the queue."""
    global _queue, _worker, _worker_task, _loop

    if _worker is not None:
        await _worker.stop()
        if _worker_task is not None:
            try:
                await asyncio.wait_for(_worker_task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.warning("saq_worker_stop_timeout — cancelling task")
                _worker_task.cancel()
        _worker = None
        _worker_task = None

    if _queue is not None:
        await _queue.disconnect()
        _queue = None

    _loop = None
    logger.info("saq_worker_stopped")


# ---------------------------------------------------------------------------
# Sync bridge — for callers running in sync threads (e.g. recycler)
# ---------------------------------------------------------------------------

class EnqueueSyncError(RuntimeError):
    """SAQ job could not be scheduled (worker down or event loop closed)."""


def enqueue_sync(
    task_name: str,
    *,
    key: str | None = None,
    timeout: int = 60,
    retries: int = 3,
    required: bool = False,
    **kwargs,
) -> bool:
    """Enqueue a SAQ job from a synchronous context.

    Returns True when the enqueue was scheduled on the event loop.
    When ``required=False`` (default, recycler/reaper compensating paths),
    logs a warning and returns False on failure.
    When ``required=True`` (user-facing dispatch), raises
    :class:`EnqueueSyncError` so the caller can fail fast (HTTP 503).

    When ``required=False``, uses ``call_soon_threadsafe`` (fire-and-forget).
    When ``required=True`` and called from a worker thread, blocks up to
    ``SAQ_ENQUEUE_WAIT_TIMEOUT`` via ``run_coroutine_threadsafe`` so callers
    can fail fast on Redis errors.  Calling ``required=True`` from the main
    event loop raises :class:`EnqueueSyncError` (would deadlock).
    """
    if _queue is None or _loop is None:
        msg = f"SAQ not running — cannot enqueue {task_name}"
        logger.warning("enqueue_sync called but SAQ not running — dropping %s", task_name)
        if required:
            raise EnqueueSyncError(msg)
        return False

    job = Job(
        function=task_name,
        kwargs=kwargs,
        key=key or "",
        timeout=timeout,
        retries=retries,
    )

    async def _do_enqueue():
        await _queue.enqueue(job)
        logger.info("enqueue_async_ok task=%s key=%s", task_name, key)

    on_main_loop = False
    try:
        on_main_loop = asyncio.get_running_loop() is _loop
    except RuntimeError:
        pass

    if required and not on_main_loop:
        try:
            future = asyncio.run_coroutine_threadsafe(_do_enqueue(), _loop)
        except RuntimeError:
            msg = f"event loop closed — cannot enqueue {task_name}"
            logger.warning("enqueue_sync: event loop closed — dropping %s", task_name)
            if required:
                raise EnqueueSyncError(msg)
            return False
        try:
            future.result(timeout=SAQ_ENQUEUE_WAIT_TIMEOUT)
        except Exception as exc:
            msg = f"enqueue failed for {task_name}: {exc}"
            logger.exception("enqueue_async_failed task=%s", task_name)
            raise EnqueueSyncError(msg) from exc
        return True

    if required and on_main_loop:
        raise EnqueueSyncError(
            f"cannot synchronously enqueue {task_name} from the event loop"
        )

    async def _do_enqueue_best_effort():
        try:
            await _do_enqueue()
        except Exception:
            logger.exception("enqueue_async_failed task=%s", task_name)

    try:
        _loop.call_soon_threadsafe(_loop.create_task, _do_enqueue_best_effort())
    except RuntimeError:
        msg = f"event loop closed — cannot enqueue {task_name}"
        logger.warning("enqueue_sync: event loop closed — dropping %s", task_name)
        if required:
            raise EnqueueSyncError(msg)
        return False
    return True


# ---------------------------------------------------------------------------
# Sync read helpers — for callers running in sync threads (e.g. reaper)
# ---------------------------------------------------------------------------

from typing import Any, Coroutine  # noqa: E402 (keep with related helpers)


async def _get_saq_job_state(key: str) -> dict | None:
    """Return the SAQ job dict for *key*, or None if not found."""
    if _queue is None:
        return None
    job = await _queue.job(key)
    return job.to_dict() if job else None


async def _is_worker_alive(worker_id: str | None) -> bool:
    """Return True if *worker_id* is present in the SAQ worker registry."""
    if not worker_id or _queue is None:
        return False
    info = await _queue.info()
    return worker_id in (info.get("workers") or {})


def _read_from_loop(coro: Coroutine[Any, Any, Any], timeout: float = 3.0) -> Any:
    """Run *coro* on the main event loop from a thread-pool thread.

    Only call from thread pool (APScheduler sync jobs), **never** from
    coroutines on ``_loop`` itself — doing so would deadlock.

    The short *timeout* is a safety net for Redis hangs; it should never
    fire in normal operation.  When it does fire the caller gets ``None``
    and a task may be left orphaned on the loop (acceptable for reaper
    correctness — orphan detection degrades gracefully to "skip").

    Callers must ensure *coro* is only constructed when ``_loop`` is known
    to be non-None, to avoid "coroutine was never awaited" warnings.
    """
    if _loop is None:
        return None
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=timeout)


def get_saq_job_state_sync(key: str) -> dict | None:
    """Sync wrapper: return SAQ job state dict, or None."""
    if _loop is None:
        return None
    return _read_from_loop(_get_saq_job_state(key))


def is_worker_alive_sync(worker_id: str | None) -> bool:
    """Sync wrapper: return True if the SAQ worker owning *worker_id* is alive."""
    if _loop is None:
        return False
    return bool(_read_from_loop(_is_worker_alive(worker_id)))
