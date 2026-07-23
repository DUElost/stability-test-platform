"""ADR-0026 admission queue — sole dispatch path (legacy precheck gate removed).

Production dispatch always flows: ``prepare_plan_run`` → ``QUEUED`` → pump →
``PRECHECK`` → ``plan_admission_task`` → ``RUNNING``.

``STP_PLAN_ADMISSION_QUEUE_ENABLED=0`` is rejected at prepare time (no legacy
fallback).  The pump must register via :func:`mark_queue_pump_ready` before
any PlanRun can be created.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_FLAG_ENV = "STP_PLAN_ADMISSION_QUEUE_ENABLED"

_queue_pump_ready = False
_warned_pump_not_ready = False


def admission_queue_flag_enabled() -> bool:
    """Operator env flag (default on). ``0`` disables dispatch entirely."""
    return os.getenv(_FLAG_ENV, "1") == "1"


def is_queue_pump_ready() -> bool:
    return _queue_pump_ready


def mark_queue_pump_ready(ready: bool = True) -> None:
    """Called by the queue pump when it starts / by tests."""
    global _queue_pump_ready, _warned_pump_not_ready
    _queue_pump_ready = ready
    _warned_pump_not_ready = False
    if ready:
        logger.info("admission_queue_pump_registered")


def admission_queue_enabled() -> bool:
    """True when admission dispatch is allowed (flag on AND pump registered)."""
    global _warned_pump_not_ready
    if not admission_queue_flag_enabled():
        return False
    if not _queue_pump_ready:
        if not _warned_pump_not_ready:
            logger.warning(
                "admission_queue_pump_not_ready — dispatch blocked until the "
                "queue pump registers; check APScheduler /health "
                "admission_queue_pump_ready",
            )
            _warned_pump_not_ready = True
        return False
    return True
