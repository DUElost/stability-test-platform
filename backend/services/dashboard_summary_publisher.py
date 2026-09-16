# -*- coding: utf-8 -*-
"""Coalesced dashboard-summary WS push (#2324 / ADR-0026 observation plane).

Heartbeat and other writers mark a dirty flag; a single flush ≤1Hz (default)
recomputes the summary and broadcasts ``dashboard_summary``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from backend.core.database import SessionLocal
from backend.core.metrics import dashboard_summary_push_total
from backend.services.dashboard_summary import compute_dashboard_summary

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL = 1.0

_loop: Optional[asyncio.AbstractEventLoop] = None
_dirty: bool = False
_flush_handle: Optional[asyncio.TimerHandle] = None
_flush_task: Optional[asyncio.Task] = None


def _push_interval_seconds() -> float:
    raw = os.getenv("STP_DASHBOARD_SUMMARY_PUSH_INTERVAL_SECONDS", str(_DEFAULT_INTERVAL))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_INTERVAL
    return value if value > 0 else _DEFAULT_INTERVAL


def bind_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Bind the ASGI event loop used for coalesced flush scheduling."""
    global _loop
    _loop = loop


def _reset_for_tests() -> None:
    """Clear publisher state between tests."""
    global _loop, _dirty, _flush_handle, _flush_task
    if _flush_handle is not None:
        _flush_handle.cancel()
    if _flush_task is not None and not _flush_task.done():
        _flush_task.cancel()
    _loop = None
    _dirty = False
    _flush_handle = None
    _flush_task = None


def schedule_dashboard_summary_push() -> None:
    """Mark summary dirty and ensure a coalesced flush is armed."""
    global _dirty, _flush_handle
    _dirty = True
    loop = _loop
    if loop is None or loop.is_closed():
        return
    if _flush_handle is not None:
        return

    def _arm_flush() -> None:
        global _flush_handle, _flush_task
        _flush_handle = None
        if loop.is_closed():
            return
        _flush_task = loop.create_task(_flush_dashboard_summary())

    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    if running is loop:
        _flush_handle = loop.call_later(_push_interval_seconds(), _arm_flush)
    else:
        def _schedule_on_loop() -> None:
            global _flush_handle
            if _flush_handle is not None:
                return
            _flush_handle = loop.call_later(_push_interval_seconds(), _arm_flush)

        loop.call_soon_threadsafe(_schedule_on_loop)


async def _flush_dashboard_summary() -> None:
    global _dirty, _flush_task
    _flush_task = None
    if not _dirty:
        return
    _dirty = False

    db = SessionLocal()
    try:
        summary = await asyncio.to_thread(compute_dashboard_summary, db)
    except Exception:
        logger.exception("dashboard_summary_compute_failed")
        schedule_dashboard_summary_push()
        return
    finally:
        db.close()

    try:
        from backend.realtime.socketio_server import broadcast_dashboard_summary

        await broadcast_dashboard_summary(summary)
        dashboard_summary_push_total.inc()
    except Exception:
        logger.exception("dashboard_summary_broadcast_failed")
        schedule_dashboard_summary_push()
