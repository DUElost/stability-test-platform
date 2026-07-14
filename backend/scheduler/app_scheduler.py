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
RECONCILER_INTERVAL = int(os.getenv("RECONCILER_INTERVAL_SECONDS", "15"))
CRON_POLL_INTERVAL = float(os.getenv("CRON_POLL_INTERVAL", "30"))
RETENTION_CLEANUP_INTERVAL = int(os.getenv("RETENTION_CLEANUP_INTERVAL_SECONDS", "3600"))
QUEUE_DEPTH_INTERVAL = int(os.getenv("QUEUE_DEPTH_POLL_INTERVAL_SECONDS", "15"))
PRECHECK_REAPER_INTERVAL = int(os.getenv("PRECHECK_REAPER_INTERVAL_SECONDS", "45"))
CHAIN_RECONCILER_INTERVAL = int(
    os.getenv("CHAIN_RECONCILER_INTERVAL_SECONDS", "60")
)
# 一天扫一次 expired jti 即可:refresh 黑名单只在 user 主动 logout 时增长,
# 量级低;同时 expires_at 是 30 天后,过期窗口很宽,扫太频反而浪费 IO。
REVOKED_TOKEN_CLEANUP_INTERVAL = int(
    os.getenv("REVOKED_TOKEN_CLEANUP_INTERVAL_SECONDS", str(24 * 3600))
)

# ADR-0025 Sprint 4: auto_archive_interval  poll interval
AUTO_ARCHIVE_INTERVAL = int(os.getenv("AUTO_ARCHIVE_POLL_INTERVAL_SECONDS", "120"))

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
    from backend.scheduler.device_lease_reconciler import device_lease_reconcile_once
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
        _instrumented("device_lease_reconciler", device_lease_reconcile_once),
        IntervalTrigger(seconds=RECONCILER_INTERVAL),
        id="device_lease_reconciler",
        misfire_grace_time=MISFIRE_GRACE,
        conflict_policy=ConflictPolicy.replace,
    )
    logger.info("schedule_registered id=device_lease_reconciler interval=%ds", RECONCILER_INTERVAL)

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

    from backend.scheduler.precheck_reaper import precheck_reaper_job

    await scheduler.add_schedule(
        _instrumented("precheck_reaper", precheck_reaper_job),
        IntervalTrigger(seconds=PRECHECK_REAPER_INTERVAL),
        id="precheck_reaper",
        misfire_grace_time=MISFIRE_GRACE,
        conflict_policy=ConflictPolicy.replace,
    )
    logger.info(
        "schedule_registered id=precheck_reaper interval=%ds",
        PRECHECK_REAPER_INTERVAL,
    )

    from backend.scheduler.plan_chain_reconciler import reconcile_plan_chains

    await scheduler.add_schedule(
        _instrumented("plan_chain_reconciler", reconcile_plan_chains),
        IntervalTrigger(seconds=CHAIN_RECONCILER_INTERVAL),
        id="plan_chain_reconciler",
        misfire_grace_time=MISFIRE_GRACE,
        conflict_policy=ConflictPolicy.replace,
    )
    logger.info(
        "schedule_registered id=plan_chain_reconciler interval=%ds",
        CHAIN_RECONCILER_INTERVAL,
    )

    from backend.scheduler.revoked_token_cleanup import cleanup_revoked_refresh_tokens_job

    await scheduler.add_schedule(
        _instrumented("revoked_token_cleanup", cleanup_revoked_refresh_tokens_job),
        IntervalTrigger(seconds=REVOKED_TOKEN_CLEANUP_INTERVAL),
        id="revoked_token_cleanup",
        misfire_grace_time=timedelta(minutes=30),
        conflict_policy=ConflictPolicy.replace,
    )
    logger.info(
        "schedule_registered id=revoked_token_cleanup interval=%ds",
        REVOKED_TOKEN_CLEANUP_INTERVAL,
    )

    from backend.scheduler.cron_scheduler import auto_archive_sweep

    await scheduler.add_schedule(
        _instrumented("auto_archive_sweep", auto_archive_sweep),
        IntervalTrigger(seconds=AUTO_ARCHIVE_INTERVAL),
        id="auto_archive_sweep",
        misfire_grace_time=MISFIRE_GRACE,
        conflict_policy=ConflictPolicy.replace,
    )
    logger.info("schedule_registered id=auto_archive_sweep interval=%ds", AUTO_ARCHIVE_INTERVAL)
