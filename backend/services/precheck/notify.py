"""SocketIO invalidation hints for dispatch gate progress."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

PRECHECK_NOTIFY_DEBOUNCE_SECONDS = float(
    os.getenv("PRECHECK_NOTIFY_DEBOUNCE_SECONDS", "0.5")
)
_IMMEDIATE_PHASES = frozenset({"failed", "ready"})
_IMMEDIATE_DISPATCH_STATUSES = frozenset({"failed", "completed"})

_lock = threading.Lock()
_pending: dict[int, NotifyPayload] = {}
_timers: dict[int, threading.Timer] = {}


@dataclass(frozen=True)
class NotifyPayload:
    """Coarse invalidation markers pushed to PlanRun detail subscribers."""

    phase: str | None = None
    dispatch_status: str | None = None

    def merge(self, other: NotifyPayload) -> NotifyPayload:
        return NotifyPayload(
            phase=other.phase if other.phase is not None else self.phase,
            dispatch_status=(
                other.dispatch_status
                if other.dispatch_status is not None
                else self.dispatch_status
            ),
        )

    def to_event_dict(self) -> dict[str, str | None]:
        return {
            "phase": self.phase,
            "dispatch_status": self.dispatch_status,
        }

    def should_flush_immediately(self, prior: NotifyPayload | None) -> bool:
        if self.phase in _IMMEDIATE_PHASES:
            return True
        if self.dispatch_status in _IMMEDIATE_DISPATCH_STATUSES:
            return True
        if prior is not None and prior.phase != self.phase:
            return True
        return False


def reset_notify_debounce_state() -> None:
    """Clear debounce timers — for tests only."""
    with _lock:
        for timer in _timers.values():
            timer.cancel()
        _timers.clear()
        _pending.clear()


def _cancel_timer(plan_run_id: int) -> None:
    timer = _timers.pop(plan_run_id, None)
    if timer is not None:
        timer.cancel()


def _do_emit(plan_run_id: int, payload: NotifyPayload) -> None:
    try:
        from backend.realtime.socketio_server import schedule_emit
    except Exception:
        return
    try:
        schedule_emit(
            "precheck_update",
            {
                "type": "PRECHECK_UPDATE",
                "payload": payload.to_event_dict(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            namespace="/dashboard",
            room=f"plan_run:{plan_run_id}",
        )
    except Exception:
        logger.debug("emit_dispatch_gate_invalidation_failed", exc_info=True)


def _flush_debounced(plan_run_id: int) -> None:
    with _lock:
        payload = _pending.pop(plan_run_id, None)
        _timers.pop(plan_run_id, None)
    if payload is not None:
        _do_emit(plan_run_id, payload)


def emit_dispatch_gate_invalidation(
    plan_run_id: int,
    *,
    phase: str | None = None,
    dispatch_status: str | None = None,
    flush: bool = False,
) -> None:
    incoming = NotifyPayload(phase=phase, dispatch_status=dispatch_status)

    if PRECHECK_NOTIFY_DEBOUNCE_SECONDS <= 0 or flush:
        with _lock:
            prior = _pending.pop(plan_run_id, None)
            _cancel_timer(plan_run_id)
        merged = prior.merge(incoming) if prior else incoming
        _do_emit(plan_run_id, merged)
        return

    with _lock:
        prior = _pending.get(plan_run_id)
        merged = prior.merge(incoming) if prior else incoming

        if merged.should_flush_immediately(prior):
            _pending.pop(plan_run_id, None)
            _cancel_timer(plan_run_id)
            payload_to_emit = merged
        else:
            _pending[plan_run_id] = merged
            _cancel_timer(plan_run_id)
            timer = threading.Timer(
                PRECHECK_NOTIFY_DEBOUNCE_SECONDS,
                _flush_debounced,
                args=(plan_run_id,),
            )
            timer.daemon = True
            _timers[plan_run_id] = timer
            timer.start()
            return

    _do_emit(plan_run_id, payload_to_emit)


_emit_dispatch_gate_invalidation = emit_dispatch_gate_invalidation
