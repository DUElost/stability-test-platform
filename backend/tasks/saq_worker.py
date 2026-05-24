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


def get_queue() -> Queue:
    """Return the SAQ Queue singleton.  Raises if not initialised."""
    if _queue is None:
        raise RuntimeError("SAQ queue not initialised — call start_saq_worker first")
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

    if _queue is not None:
        try:
            await _queue.disconnect()
        except Exception:
            logger.debug("saq_worker_disconnect_previous_error", exc_info=True)
        _queue = None
    _worker = None
    _worker_task = None

    _loop = asyncio.get_running_loop()

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    _queue = Queue.from_url(redis_url, name=SAQ_QUEUE_NAME)
    await _queue.connect()

    _worker = Worker(
        _queue,
        functions=SAQ_FUNCTIONS,
        concurrency=SAQ_CONCURRENCY,
        before_process=_before_process,
        after_process=_after_process,
    )
    _worker_task = asyncio.create_task(_worker.start(), name="saq-worker")
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

    Uses ``call_soon_threadsafe`` to schedule an async enqueue on the main
    event loop.  Does NOT block for the result.

    Note: ``run_coroutine_threadsafe`` + ``future.result()`` deadlocks when
    the SAQ Worker is running on the same event loop (Worker holds internal
    state that prevents ``Queue.enqueue`` from completing synchronously).
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
        try:
            await _queue.enqueue(job)
            logger.info("enqueue_async_ok task=%s key=%s", task_name, key)
        except Exception:
            logger.exception("enqueue_async_failed task=%s", task_name)

    try:
        _loop.call_soon_threadsafe(_loop.create_task, _do_enqueue())
        return True
    except RuntimeError:
        msg = f"event loop closed — cannot enqueue {task_name}"
        logger.warning("enqueue_sync: event loop closed — dropping %s", task_name)
        if required:
            raise EnqueueSyncError(msg)
        return False


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
