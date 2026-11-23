"""Thread-local pre-outbound guards preventing stale mount/capacity sends after reload ACK (#4077)."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

_STATE = threading.local()


@contextmanager
def bind_heartbeat_stale_check(check: Optional[Callable[[], bool]]) -> Iterator[None]:
    """Bind a per-thread staleness predicate checked right before HTTP/WS heartbeat transmit."""
    prev = getattr(_STATE, "heartbeat_stale_check", None)
    _STATE.heartbeat_stale_check = check
    try:
        yield
    finally:
        _STATE.heartbeat_stale_check = prev


def is_bound_heartbeat_stale() -> bool:
    """Return True if the current thread's bound heartbeat snapshot has been superseded."""
    check = getattr(_STATE, "heartbeat_stale_check", None)
    return bool(check()) if callable(check) else False


@contextmanager
def bind_claim_capacity_reader(reader: Optional[Callable[[], int]]) -> Iterator[None]:
    """Bind a per-thread capacity reader refreshed right before claim HTTP transmit."""
    prev = getattr(_STATE, "claim_capacity_reader", None)
    _STATE.claim_capacity_reader = reader
    try:
        yield
    finally:
        _STATE.claim_capacity_reader = prev


def resolve_bound_claim_capacity(initial_capacity: int) -> int:
    """Clamp *initial_capacity* against the current thread's bound claim capacity reader."""
    reader = getattr(_STATE, "claim_capacity_reader", None)
    if not callable(reader):
        return initial_capacity
    return max(min(initial_capacity, int(reader())), 0)
