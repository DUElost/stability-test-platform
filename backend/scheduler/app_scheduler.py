# -*- coding: utf-8 -*-
"""
APScheduler 4.x initialisation — single entry-point for all periodic jobs.

Replaces the legacy daemon threads (cron_scheduler, recycler) and asyncio
background tasks (session_watchdog, heartbeat_monitor) with a unified
AsyncScheduler managed by the FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import time
from datetime import timedelta
from typing import Callable

from apscheduler import AsyncScheduler, ConflictPolicy
from apscheduler.triggers.interval import IntervalTrigger

from backend.core.metrics import (
    record_apscheduler_job,
    saq_queue_depth as saq_queue_depth_gauge,
    PROMETHEUS_AVAILABLE,
)

logger = logging.getLogger(__name__)

RECYCLER_INTERVAL = int(os.getenv("RUN_RECYCLE_INTERVAL_SECONDS", "30"))
WATCHDOG_INTERVAL = int(os.getenv("SESSION_WATCHDOG_INTERVAL_SECONDS", "15"))
CRON_POLL_INTERVAL = float(os.getenv("CRON_POLL_INTERVAL", "30"))
RETENTION_CLEANUP_INTERVAL = int(os.getenv("RETENTION_CLEANUP_INTERVAL_SECONDS", "3600"))
QUEUE_DEPTH_INTERVAL = int(os.getenv("QUEUE_DEPTH_POLL_INTERVAL_SECONDS", "15"))

MISFIRE_GRACE = timedelta(seconds=60)


def _instrumented(job_name: str, func: Callable) -> Callable:
    """Wrap a scheduler job function with Prometheus timing/counting.

    Preserves sync/async nature: sync jobs stay sync (APScheduler runs them
    in a thread pool), async jobs stay async (run on the event loop).
    Mixing these up causes deadlocks — sync functions that call
    ``enqueue_sync`` must NOT run on the event loop.
    """
    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            t0 = time.monotonic()
            try:
                result = await func(*args, **kwargs)
                record_apscheduler_job(job_name, "success", time.monotonic() - t0)
                return result
            except Exception:
                record_apscheduler_job(job_name, "error", time.monotonic() - t0)
                raise
        return async_wrapper
    else:
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            t0 = time.monotonic()
            try:
                result = func(*args, **kwargs)
                record_apscheduler_job(job_name, "success", time.monotonic() - t0)
                return result
            except Exception:
                record_apscheduler_job(job_name, "error", time.monotonic() - t0)
                raise
        return sync_wrapper


def create_scheduler() -> AsyncScheduler:
    """Return a *not-yet-started* ``AsyncScheduler`` instance.

    The caller must use it as an ``async with`` context manager (or call
    ``__aenter__`` / ``__aexit__`` manually) and then invoke
    ``register_schedules`` + ``start_in_background``.
    """
    return AsyncScheduler()


async def _poll_saq_queue_depth() -> None:
    """Periodic job: sample SAQ queue depth and expose via Prometheus gauge."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        from backend.tasks.saq_worker import get_queue
        queue = get_queue()
        depth = await queue.count("queued")
        saq_queue_depth_gauge.labels(queue_name=queue.name).set(depth)
    except RuntimeError:
        pass
    except Exception:
        logger.debug("saq_queue_depth_poll_failed", exc_info=True)


async def register_schedules(scheduler: AsyncScheduler) -> None:
    """Register every periodic job with the scheduler.

    Must be called **after** the scheduler context manager has been entered
    (i.e. after ``await scheduler.__aenter__()``).
    """
    from backend.scheduler.recycler import recycle_once
    from backend.scheduler.cron_scheduler import (
        check_and_fire_schedules,
        run_retention_cleanup,
    )
    from backend.tasks.session_watchdog import session_watchdog_once

    await scheduler.add_schedule(
        _instrumented("recycler", recycle_once),
        IntervalTrigger(seconds=RECYCLER_INTERVAL),
        id="recycler",
        misfire_grace_time=MISFIRE_GRACE,
        conflict_policy=ConflictPolicy.replace,
    )
    logger.info("schedule_registered id=recycler interval=%ds", RECYCLER_INTERVAL)

    await scheduler.add_schedule(
        _instrumented("session_watchdog", session_watchdog_once),
        IntervalTrigger(seconds=WATCHDOG_INTERVAL),
        id="session_watchdog",
        misfire_grace_time=MISFIRE_GRACE,
        conflict_policy=ConflictPolicy.replace,
    )
    logger.info("schedule_registered id=session_watchdog interval=%ds", WATCHDOG_INTERVAL)

    await scheduler.add_schedule(
        _instrumented("cron_check", check_and_fire_schedules),
        IntervalTrigger(seconds=int(CRON_POLL_INTERVAL)),
        id="cron_check",
        misfire_grace_time=MISFIRE_GRACE,
        conflict_policy=ConflictPolicy.replace,
    )
    logger.info("schedule_registered id=cron_check interval=%ds", int(CRON_POLL_INTERVAL))

    await scheduler.add_schedule(
        _instrumented("retention_cleanup", run_retention_cleanup),
        IntervalTrigger(seconds=RETENTION_CLEANUP_INTERVAL),
        id="retention_cleanup",
        misfire_grace_time=timedelta(minutes=10),
        conflict_policy=ConflictPolicy.replace,
    )
    logger.info(
        "schedule_registered id=retention_cleanup interval=%ds",
        RETENTION_CLEANUP_INTERVAL,
    )

    await scheduler.add_schedule(
        _poll_saq_queue_depth,
        IntervalTrigger(seconds=QUEUE_DEPTH_INTERVAL),
        id="saq_queue_depth_poll",
        misfire_grace_time=MISFIRE_GRACE,
        conflict_policy=ConflictPolicy.replace,
    )
    logger.info("schedule_registered id=saq_queue_depth_poll interval=%ds", QUEUE_DEPTH_INTERVAL)
