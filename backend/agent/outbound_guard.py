"""Thread-local outbound guards and epoch drain gate aligning reload ACK with transmit (#4077)."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Dict, Iterator, Optional

_STATE = threading.local()


class OutboundDispatchGate:
    """Epoch-based drain gate aligning reload ACK with in-flight HTTP/WS dispatches."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._generation: int = 0
        self._inflight: Dict[int, int] = {}

    @contextmanager
    def send_window(self) -> Iterator[int]:
        """Enter an outbound transmit critical section under the current epoch."""
        with self._lock:
            gen = self._generation
            self._inflight[gen] = self._inflight.get(gen, 0) + 1
        try:
            yield gen
        finally:
            with self._lock:
                remaining = self._inflight.get(gen, 1) - 1
                if remaining <= 0:
                    self._inflight.pop(gen, None)
                    self._cond.notify_all()
                else:
                    self._inflight[gen] = remaining

    def advance_and_drain(self) -> int:
        """Bump generation for future sends and wait until all pre-bump sends finish."""
        with self._lock:
            cutoff_gen = self._generation
            self._generation += 1
            while any(gen <= cutoff_gen for gen in self._inflight):
                self._cond.wait()
            return self._generation


@contextmanager
def bind_heartbeat_stale_check(
    check: Optional[Callable[[], bool]],
    *,
    dispatch_gate: Optional[OutboundDispatchGate] = None,
) -> Iterator[None]:
    """Bind a per-thread staleness predicate and optional dispatch gate for HTTP/WS heartbeat."""
    prev_check = getattr(_STATE, "heartbeat_stale_check", None)
    prev_gate = getattr(_STATE, "heartbeat_dispatch_gate", None)
    _STATE.heartbeat_stale_check = check
    _STATE.heartbeat_dispatch_gate = (
        dispatch_gate if isinstance(dispatch_gate, OutboundDispatchGate) else None
    )
    try:
        yield
    finally:
        _STATE.heartbeat_stale_check = prev_check
        _STATE.heartbeat_dispatch_gate = prev_gate


@contextmanager
def enter_bound_heartbeat_send() -> Iterator[None]:
    """Enter the bound heartbeat dispatch gate window if present on the current thread."""
    gate = getattr(_STATE, "heartbeat_dispatch_gate", None)
    if gate is None:
        yield
        return
    with gate.send_window():
        yield


def is_bound_heartbeat_stale() -> bool:
    """Return True if the current thread's bound heartbeat snapshot has been superseded."""
    check = getattr(_STATE, "heartbeat_stale_check", None)
    return bool(check()) if callable(check) else False


@contextmanager
def bind_claim_capacity_reader(
    reader: Optional[Callable[[], int]],
    *,
    dispatch_gate: Optional[OutboundDispatchGate] = None,
) -> Iterator[None]:
    """Bind a per-thread capacity reader and optional dispatch gate for claim HTTP transmit."""
    prev_reader = getattr(_STATE, "claim_capacity_reader", None)
    prev_gate = getattr(_STATE, "claim_dispatch_gate", None)
    _STATE.claim_capacity_reader = reader
    _STATE.claim_dispatch_gate = (
        dispatch_gate if isinstance(dispatch_gate, OutboundDispatchGate) else None
    )
    try:
        yield
    finally:
        _STATE.claim_capacity_reader = prev_reader
        _STATE.claim_dispatch_gate = prev_gate


@contextmanager
def enter_bound_claim_send() -> Iterator[None]:
    """Enter the bound claim dispatch gate window if present on the current thread."""
    gate = getattr(_STATE, "claim_dispatch_gate", None)
    if gate is None:
        yield
        return
    with gate.send_window():
        yield


def resolve_bound_claim_capacity(initial_capacity: int) -> int:
    """Clamp *initial_capacity* against the current thread's bound claim capacity reader."""
    reader = getattr(_STATE, "claim_capacity_reader", None)
    if not callable(reader):
        return initial_capacity
    return max(min(initial_capacity, int(reader())), 0)
