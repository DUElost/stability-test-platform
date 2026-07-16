"""ADR-0026 admission-queue feature gate (P1 step 2).

Two conditions gate every writer of QUEUED/PRECHECK:

1. ``STP_PLAN_ADMISSION_QUEUE_ENABLED=1`` — operator intent (default off).
2. The queue pump has registered via :func:`mark_queue_pump_ready` — the
   component that drains QUEUED does not exist until P1 step 4; without this
   gate an eagerly-set env flag would silently strand PlanRuns in QUEUED
   forever (reviewer-required protection).

``admission_queue_enabled()`` is the ONLY predicate production code may use
to decide whether to produce QUEUED/PRECHECK. The env flag alone
(:func:`admission_queue_flag_enabled`) is exposed for observability/tests.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_FLAG_ENV = "STP_PLAN_ADMISSION_QUEUE_ENABLED"

_queue_pump_ready = False
_warned_pump_not_ready = False


def admission_queue_flag_enabled() -> bool:
    """Operator env flag only — NOT sufficient to produce QUEUED/PRECHECK."""
    return os.getenv(_FLAG_ENV, "0") == "1"


def is_queue_pump_ready() -> bool:
    return _queue_pump_ready


def mark_queue_pump_ready(ready: bool = True) -> None:
    """Called by the queue pump when it starts (P1 step 4) / by tests."""
    global _queue_pump_ready, _warned_pump_not_ready
    _queue_pump_ready = ready
    if ready:
        _warned_pump_not_ready = False
        logger.info("admission_queue_pump_registered")


def admission_queue_enabled() -> bool:
    """True only when the env flag is on AND the queue pump is registered.

    A PlanRun put into QUEUED with no pump running would never be admitted,
    so flag-on/pump-absent deliberately resolves to False (with a one-shot
    warning) instead of half-enabling the path.
    """
    global _warned_pump_not_ready
    if not admission_queue_flag_enabled():
        return False
    if not _queue_pump_ready:
        if not _warned_pump_not_ready:
            logger.warning(
                "admission_queue_flag_set_but_pump_not_ready — "
                "%s=1 has no effect until the queue pump registers "
                "(P1 step 4); legacy dispatch path stays active",
                _FLAG_ENV,
            )
            _warned_pump_not_ready = True
        return False
    return True
