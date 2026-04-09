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
from typing import Optional

from saq import Job, Queue, Worker

from backend.tasks.saq_tasks import SAQ_FUNCTIONS

logger = logging.getLogger(__name__)

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


async def start_saq_worker() -> None:
    """Connect the queue and launch the SAQ worker as a background task."""
    global _queue, _worker, _worker_task, _loop

    _loop = asyncio.get_running_loop()

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    _queue = Queue.from_url(redis_url, name=SAQ_QUEUE_NAME)
    await _queue.connect()

    _worker = Worker(
        _queue,
        functions=SAQ_FUNCTIONS,
        concurrency=SAQ_CONCURRENCY,
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
        _worker.stop()
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

def enqueue_sync(
    task_name: str,
    *,
    key: str | None = None,
    timeout: int = 60,
    retries: int = 3,
    **kwargs,
) -> None:
    """Enqueue a SAQ job from a synchronous context.

    Uses ``asyncio.run_coroutine_threadsafe`` to schedule the enqueue
    coroutine on the main event loop stored at worker-start time.
    """
    if _queue is None or _loop is None:
        logger.warning("enqueue_sync called but SAQ not running — dropping %s", task_name)
        return

    job = Job(
        function=task_name,
        kwargs=kwargs,
        key=key or "",
        timeout=timeout,
        retries=retries,
    )
    future = asyncio.run_coroutine_threadsafe(_queue.enqueue(job), _loop)
    try:
        future.result(timeout=10)
    except Exception:
        logger.exception("enqueue_sync failed for %s", task_name)
