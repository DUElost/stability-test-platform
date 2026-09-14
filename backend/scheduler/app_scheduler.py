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
import time
from datetime import timedelta
from typing import Callable

from apscheduler import AsyncScheduler, ConflictPolicy, TaskDefaults
from apscheduler.triggers.interval import IntervalTrigger

from backend.core.metrics import (
    record_apscheduler_job,
    saq_queue_depth as saq_queue_depth_gauge,
    PROMETHEUS_AVAILABLE,
)

from backend.core.settings.scheduler import (
    SchedulerSettings,
    get_scheduler_settings,
)

logger = logging.getLogger(__name__)

# 调度节奏与回收旋钮已收敛到分域 Settings（ADR-0042 P1 试点）：
#   backend/core/settings/scheduler.py（字段名 = 原 env 名小写，默认值不变）
# 取值保持惰性（`_sched()` 逐次读缓存），不在 import 时固化（ADR-0042 D4）。


def _sched() -> SchedulerSettings:
    return get_scheduler_settings()

MISFIRE_GRACE = timedelta(seconds=60)

# ADR-0027 P3-3: singleton jobs that must not multi-run across control-plane
# instances. ``admission_pump`` / ``counter_reconcile`` keep *internal*
# leadership (already tested) — do not wrap them again (nested advisory
# locks on different DB sessions would deadlock the tick).
SINGLETON_SCHEDULE_IDS: frozenset[str] = frozenset({
    "recycler",
    "session_watchdog",
    "device_lease_reconciler",
    "cron_check",
    "retention_cleanup",
    "precheck_reaper",
    "plan_chain_reconciler",
    "revoked_token_cleanup",
    "auto_archive_sweep",
})


def _with_leadership(job_name: str, func: Callable) -> Callable:
    """Skip the tick when this process is not the elected scheduler leader."""
    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_guard(*args, **kwargs):
            from backend.core.leader_election import hold_scheduler_leadership

            with hold_scheduler_leadership(job_name) as is_leader:
                if not is_leader:
                    logger.debug("scheduler_job_skipped_not_leader job=%s", job_name)
                    return {"skipped_not_leader": 1}
                return await func(*args, **kwargs)

        return async_guard

    @functools.wraps(func)
    def sync_guard(*args, **kwargs):
        from backend.core.leader_election import hold_scheduler_leadership

        with hold_scheduler_leadership(job_name) as is_leader:
            if not is_leader:
                logger.debug("scheduler_job_skipped_not_leader job=%s", job_name)
                return {"skipped_not_leader": 1}
            return func(*args, **kwargs)

    return sync_guard


def _instrumented(job_name: str, func: Callable, *, singleton: bool = False) -> Callable:
    """Wrap a scheduler job with optional leadership + Prometheus timing.

    Preserves sync/async nature: sync jobs stay sync (APScheduler runs them
    in a thread pool), async jobs stay async (run on the event loop).
    Mixing these up causes deadlocks — sync functions that call
    ``enqueue_sync`` must NOT run on the event loop.
    """
    target = _with_leadership(job_name, func) if singleton else func

    if asyncio.iscoroutinefunction(target):
        @functools.wraps(target)
        async def async_wrapper(*args, **kwargs):
            t0 = time.monotonic()
            try:
                result = await target(*args, **kwargs)
                record_apscheduler_job(job_name, "success", time.monotonic() - t0)
                return result
            except Exception:
                record_apscheduler_job(job_name, "error", time.monotonic() - t0)
                raise
        return async_wrapper
    else:
        @functools.wraps(target)
        def sync_wrapper(*args, **kwargs):
            t0 = time.monotonic()
            try:
                result = target(*args, **kwargs)
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

    Default executor is ``threadpool`` so sync ticks (admission pump, recycler,
    precheck reaper, …) can call ``enqueue_sync(..., required=True)`` without
    hitting the event-loop deadlock path in ``saq_worker.enqueue_sync``.
    Truly async jobs must pass ``job_executor="async"`` at ``add_schedule``.
    """
    return AsyncScheduler(
        task_defaults=TaskDefaults(job_executor="threadpool"),
    )


def _job_executor_for(func: Callable) -> str:
    """Pick APScheduler executor: async coroutines stay on the loop."""
    return "async" if asyncio.iscoroutinefunction(func) else "threadpool"


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

    async def _add(
        func: Callable,
        trigger: IntervalTrigger,
        *,
        id: str,
        misfire_grace_time=MISFIRE_GRACE,
    ) -> None:
        await scheduler.add_schedule(
            func,
            trigger,
            id=id,
            job_executor=_job_executor_for(func),
            misfire_grace_time=misfire_grace_time,
            conflict_policy=ConflictPolicy.replace,
        )

    await _add(
        _instrumented("recycler", recycle_once, singleton=True),
        IntervalTrigger(seconds=_sched().run_recycle_interval_seconds),
        id="recycler",
    )
    logger.info("schedule_registered id=recycler interval=%ds", _sched().run_recycle_interval_seconds)

    await _add(
        _instrumented("session_watchdog", session_watchdog_once, singleton=True),
        IntervalTrigger(seconds=_sched().session_watchdog_interval_seconds),
        id="session_watchdog",
    )
    logger.info("schedule_registered id=session_watchdog interval=%ds", _sched().session_watchdog_interval_seconds)

    await _add(
        _instrumented(
            "device_lease_reconciler", device_lease_reconcile_once, singleton=True
        ),
        IntervalTrigger(seconds=_sched().reconciler_interval_seconds),
        id="device_lease_reconciler",
    )
    logger.info("schedule_registered id=device_lease_reconciler interval=%ds", _sched().reconciler_interval_seconds)

    await _add(
        _instrumented("cron_check", check_and_fire_schedules, singleton=True),
        IntervalTrigger(seconds=int(_sched().cron_poll_interval)),
        id="cron_check",
    )
    logger.info("schedule_registered id=cron_check interval=%ds", int(_sched().cron_poll_interval))

    await _add(
        _instrumented("retention_cleanup", run_retention_cleanup, singleton=True),
        IntervalTrigger(seconds=_sched().retention_cleanup_interval_seconds),
        id="retention_cleanup",
        misfire_grace_time=timedelta(minutes=10),
    )
    logger.info(
        "schedule_registered id=retention_cleanup interval=%ds",
        _sched().retention_cleanup_interval_seconds,
    )

    await _add(
        _poll_saq_queue_depth,
        IntervalTrigger(seconds=_sched().queue_depth_poll_interval_seconds),
        id="saq_queue_depth_poll",
    )
    logger.info("schedule_registered id=saq_queue_depth_poll interval=%ds", _sched().queue_depth_poll_interval_seconds)

    from backend.scheduler.precheck_reaper import precheck_reaper_job

    await _add(
        _instrumented("precheck_reaper", precheck_reaper_job, singleton=True),
        IntervalTrigger(seconds=_sched().precheck_reaper_interval_seconds),
        id="precheck_reaper",
    )
    logger.info(
        "schedule_registered id=precheck_reaper interval=%ds",
        _sched().precheck_reaper_interval_seconds,
    )

    from backend.scheduler.plan_chain_reconciler import reconcile_plan_chains

    await _add(
        _instrumented("plan_chain_reconciler", reconcile_plan_chains, singleton=True),
        IntervalTrigger(seconds=_sched().chain_reconciler_interval_seconds),
        id="plan_chain_reconciler",
    )
    logger.info(
        "schedule_registered id=plan_chain_reconciler interval=%ds",
        _sched().chain_reconciler_interval_seconds,
    )

    from backend.scheduler.revoked_token_cleanup import cleanup_revoked_refresh_tokens_job

    await _add(
        _instrumented(
            "revoked_token_cleanup",
            cleanup_revoked_refresh_tokens_job,
            singleton=True,
        ),
        IntervalTrigger(seconds=_sched().revoked_token_cleanup_interval_seconds),
        id="revoked_token_cleanup",
        misfire_grace_time=timedelta(minutes=30),
    )
    logger.info(
        "schedule_registered id=revoked_token_cleanup interval=%ds",
        _sched().revoked_token_cleanup_interval_seconds,
    )

    from backend.scheduler.cron_scheduler import auto_archive_sweep

    await _add(
        _instrumented("auto_archive_sweep", auto_archive_sweep, singleton=True),
        IntervalTrigger(seconds=_sched().auto_archive_poll_interval_seconds),
        id="auto_archive_sweep",
    )
    logger.info("schedule_registered id=auto_archive_sweep interval=%ds", _sched().auto_archive_poll_interval_seconds)

    # ── ADR-0026 Step 4: admission queue pump ──
    # Registered unconditionally: with the env flag off the pump runs in
    # drain-only mode (prepare stops creating new QUEUED runs, the pump keeps
    # admitting existing ones until the queue is empty — reviewer boundary #5).
    # An idle tick is one indexed no-op query; the tick itself short-circuits
    # while the SAQ producer is down. Pump READINESS is marked in main.py after
    # producer (+ optional in-process worker) start succeeds (ADR-0026 P0:
    # STP_ENABLE_INPROCESS_SAQ=0 keeps producer alive for external workers).
    from backend.services.admission_pump import pump_admission_tick

    await _add(
        _instrumented("admission_pump", pump_admission_tick),
        IntervalTrigger(seconds=_sched().stp_admission_pump_interval_seconds),
        id="admission_pump",
    )
    logger.info("schedule_registered id=admission_pump interval=%ds", _sched().stp_admission_pump_interval_seconds)

    # ADR-0026 §6: low-frequency counter reconciliation (self-heal drift)
    from backend.scheduler.counter_reconciler import reconcile_plan_run_counters_once

    await _add(
        _instrumented("counter_reconcile", reconcile_plan_run_counters_once),
        IntervalTrigger(seconds=_sched().stp_counter_reconcile_interval_seconds),
        id="counter_reconcile",
    )
    logger.info(
        "schedule_registered id=counter_reconcile interval=%ds",
        _sched().stp_counter_reconcile_interval_seconds,
    )

    # #556: drain signal↔DLE link backlog outside the request path
    from backend.scheduler.signal_link_reconciler import reconcile_signal_links_once

    await _add(
        _instrumented("signal_link_reconcile", reconcile_signal_links_once),
        IntervalTrigger(seconds=_sched().stp_signal_link_reconcile_interval_seconds),
        id="signal_link_reconcile",
    )
    logger.info(
        "schedule_registered id=signal_link_reconcile interval=%ds",
        _sched().stp_signal_link_reconcile_interval_seconds,
    )
