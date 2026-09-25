# -*- coding: utf-8 -*-
"""
SAQ worker lifecycle — in-process Worker management.

The worker runs inside the FastAPI process as a background asyncio task,
sharing the same event loop.  ``start_saq_worker`` / ``stop_saq_worker``
are called from the FastAPI lifespan.

入队 API、队列单例与只读探针在 ``backend/core/task_queue.py``（2026-09-25 拆出）：
本模块 import ``saq_tasks``（→ services），services 入队若再 import 本模块就成环；
所以生产者端口下沉到 core，本模块只管 worker。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from saq import Job, Worker

from backend.core import task_queue
from backend.core.metrics import record_saq_task
from backend.tasks.saq_tasks import SAQ_FUNCTIONS

logger = logging.getLogger(__name__)


class ControlPlaneWorker(Worker):
    """控制面专用 SAQ Worker:不接管 SIGINT/SIGTERM。

    停机排查(2026-08-17)根因:saq.Worker 启动时对 SIGINT/SIGTERM 执行
    ``loop.add_signal_handler``,会**覆盖同进程 uvicorn 的 signal.signal
    处理器**——生产(STP_ENABLE_INPROCESS_SAQ=1)下 systemd SIGTERM 只
    触发 SAQ 停止事件,uvicorn 的 should_exit 永远不被设置,进程无限服务
    直到 systemd 90s 后 SIGKILL(日志里从未出现 "Shutting down",停机
    窗口内心跳仍返回 200)。本类清空 SIGNALS:停机信号所有权归 uvicorn;
    SAQ 的优雅停止由 lifespan 收尾的 ``stop_saq_worker()`` 负责。
    """

    SIGNALS = []

_SAQ_JOB_START_KEY = "_saq_metric_start"

_worker: Optional[Worker] = None
_worker_task: Optional[asyncio.Task] = None

SAQ_CONCURRENCY = int(os.getenv("SAQ_CONCURRENCY", "10"))


def _worker_running() -> bool:
    """进程内 worker task 是否在跑（登记给 ``task_queue.is_saq_ready``）。"""
    return _worker_task is not None and not _worker_task.done()


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


async def start_saq_worker() -> None:
    """Connect the queue and launch the SAQ worker as a background task.

    Idempotent: if the worker is already running, this is a no-op.  If a
    previous worker task exists but has exited, the old queue is
    disconnected before reconnecting.
    """
    global _worker, _worker_task

    if task_queue.is_queue_connected() and _worker_running():
        logger.info("saq_worker_start_skip already_running")
        return

    # Drop a stale queue/worker so reconnect is clean (crash restart path).
    await task_queue.disconnect_queue(swallow_errors=True)
    _worker = None
    _worker_task = None

    await task_queue.init_saq_producer()
    task_queue.register_worker_alive_probe(_worker_running)

    _worker = ControlPlaneWorker(
        task_queue.get_queue(),
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
        task_queue.SAQ_QUEUE_NAME,
    )


async def stop_saq_worker() -> None:
    """Gracefully stop the SAQ worker and disconnect the queue."""
    global _worker, _worker_task

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

    await task_queue.disconnect_queue(swallow_errors=False)
    logger.info("saq_worker_stopped")
