"""APScheduler executor routing: sync ticks must not run on the event loop."""

from __future__ import annotations

import asyncio

from backend.scheduler.app_scheduler import (
    _job_executor_for,
    _poll_saq_queue_depth,
    create_scheduler,
)
from backend.services.admission_pump import pump_admission_tick
from backend.tasks.session_watchdog import session_watchdog_once


def test_job_executor_for_sync_vs_async():
    assert _job_executor_for(pump_admission_tick) == "threadpool"
    assert _job_executor_for(session_watchdog_once) == "async"
    assert _job_executor_for(_poll_saq_queue_depth) == "async"


def test_create_scheduler_defaults_to_threadpool():
    sched = create_scheduler()
    assert sched.task_defaults.job_executor == "threadpool"


def test_instrumented_preserves_asyncness_for_executor_routing():
    from backend.scheduler.app_scheduler import _instrumented

    sync_wrapped = _instrumented("admission_pump", pump_admission_tick)
    async_wrapped = _instrumented("session_watchdog", session_watchdog_once, singleton=True)
    assert not asyncio.iscoroutinefunction(sync_wrapped)
    assert asyncio.iscoroutinefunction(async_wrapped)
    assert _job_executor_for(sync_wrapped) == "threadpool"
    assert _job_executor_for(async_wrapped) == "async"
