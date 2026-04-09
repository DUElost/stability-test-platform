# -*- coding: utf-8 -*-
"""
APScheduler 4.x initialisation — single entry-point for all periodic jobs.

Replaces the legacy daemon threads (cron_scheduler, recycler) and asyncio
background tasks (session_watchdog, heartbeat_monitor) with a unified
AsyncScheduler managed by the FastAPI lifespan.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from apscheduler import AsyncScheduler, ConflictPolicy
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

RECYCLER_INTERVAL = int(os.getenv("RUN_RECYCLE_INTERVAL_SECONDS", "30"))
WATCHDOG_INTERVAL = int(os.getenv("SESSION_WATCHDOG_INTERVAL_SECONDS", "15"))
CRON_POLL_INTERVAL = float(os.getenv("CRON_POLL_INTERVAL", "30"))
RETENTION_CLEANUP_INTERVAL = int(os.getenv("RETENTION_CLEANUP_INTERVAL_SECONDS", "3600"))

MISFIRE_GRACE = timedelta(seconds=60)


def create_scheduler() -> AsyncScheduler:
    """Return a *not-yet-started* ``AsyncScheduler`` instance.

    The caller must use it as an ``async with`` context manager (or call
    ``__aenter__`` / ``__aexit__`` manually) and then invoke
    ``register_schedules`` + ``start_in_background``.
    """
    return AsyncScheduler()


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
        recycle_once,
        IntervalTrigger(seconds=RECYCLER_INTERVAL),
        id="recycler",
        misfire_grace_time=MISFIRE_GRACE,
        conflict_policy=ConflictPolicy.replace,
    )
    logger.info("schedule_registered id=recycler interval=%ds", RECYCLER_INTERVAL)

    await scheduler.add_schedule(
        session_watchdog_once,
        IntervalTrigger(seconds=WATCHDOG_INTERVAL),
        id="session_watchdog",
        misfire_grace_time=MISFIRE_GRACE,
        conflict_policy=ConflictPolicy.replace,
    )
    logger.info("schedule_registered id=session_watchdog interval=%ds", WATCHDOG_INTERVAL)

    await scheduler.add_schedule(
        check_and_fire_schedules,
        IntervalTrigger(seconds=int(CRON_POLL_INTERVAL)),
        id="cron_check",
        misfire_grace_time=MISFIRE_GRACE,
        conflict_policy=ConflictPolicy.replace,
    )
    logger.info("schedule_registered id=cron_check interval=%ds", int(CRON_POLL_INTERVAL))

    await scheduler.add_schedule(
        run_retention_cleanup,
        IntervalTrigger(seconds=RETENTION_CLEANUP_INTERVAL),
        id="retention_cleanup",
        misfire_grace_time=timedelta(minutes=10),
        conflict_policy=ConflictPolicy.replace,
    )
    logger.info(
        "schedule_registered id=retention_cleanup interval=%ds",
        RETENTION_CLEANUP_INTERVAL,
    )
