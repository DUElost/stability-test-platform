"""ADR-0026 Step 5b — per-host OperationScheduler (singleton).

Host-global permit semaphore that decouples "how many devices are RUNNING"
from "how many script/ADB operations are executing concurrently." All
PlanRuns, Jobs, and HostRunCoordinators on the same host SHARE this
scheduler — a per-PlanRun scheduler would double the effective cap.

Properties:
- FIFO by default (thread-safe queue), per-device fairness prevents one
  device from starving others.
- Permit count is hot-adjustable; shrinking never preempts held permits,
  only gates new entries.
- Abort/cancellation wakes awaiting job with a dedicated exception so the
  caller can distinguish "permit denied" from "step failed."
- Shutdown wakes every waiter with the same exception so cleanup paths
  always drain (no hung threads at process exit).
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENT_OPERATIONS = int(
    os.getenv("STP_MAX_CONCURRENT_OPERATIONS", "5")
)


class PermitDenied(Exception):
    """Raised when a waiter is cancelled (abort) or the scheduler shuts down."""


class OperationPermit:
    """Context-managed permit. Release on __exit__ or explicit release()."""

    def __init__(self, scheduler: "OperationScheduler", device_id: int) -> None:
        self._scheduler = scheduler
        self._device_id = device_id
        self._held = True

    @property
    def device_id(self) -> int:
        return self._device_id

    def release(self) -> None:
        if self._held:
            self._held = False
            self._scheduler._release(self)

    def __enter__(self) -> "OperationPermit":
        return self

    def __exit__(self, *args) -> None:
        self.release()


class OperationScheduler:
    """Per-host singleton; a single instance gate-keeps ALL concurrent script
    operations on this host regardless of which PlanRun or Job owns them."""

    def __init__(self, max_concurrent: int | None = None) -> None:
        self._max_concurrent = (
            max_concurrent
            if max_concurrent is not None
            else _DEFAULT_MAX_CONCURRENT_OPERATIONS
        )
        self._held: int = 0
        self._lock = threading.Lock()
        # OrderedDict as a FIFO with O(1) membership + ordered iteration.
        # Key = device_id, value = threading.Event.  Per-device fairness:
        # at most one waiter per device — subsequent jobs on the same device
        # queue behind the existing waiter.
        self._waiters: OrderedDict[int, threading.Event] = OrderedDict()
        self._shutdown = False
        # Cancelled device_ids (aborted during wait). The waker checks this
        # and raises PermitDenied instead of returning a permit.
        self._cancelled: set[int] = set()

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def held(self) -> int:
        with self._lock:
            return self._held

    def set_max_concurrent(self, n: int) -> None:
        """Hot-adjust the cap. Shrinking does NOT preempt held permits —
        only new waiters are gated. Growing may wake pending waiters."""
        with self._lock:
            self._max_concurrent = max(n, 1)
            if self._held < self._max_concurrent:
                self._wake_one_locked()

    def acquire(self, device_id: int, timeout: float | None = None) -> OperationPermit:
        """Block until a permit is available or timeout / abort / shutdown.

        Returns an ``OperationPermit`` context manager. Raises
        ``PermitDenied`` on abort (waiter event set by cancel()), shutdown,
        or timeout.

        Per-device fairness: ``cancel_device(device_id)`` only wakes that
        specific waiter; ``shutdown()`` wakes all.
        """
        if self._shutdown:
            raise PermitDenied("scheduler shut down")

        event: threading.Event | None = None
        with self._lock:
            if device_id in self._waiters:
                # Per-device fairness: only one waiter per device.
                raise PermitDenied(f"device {device_id} already waiting")
            if self._held < self._max_concurrent and not self._waiters:
                # Fast path: slot available, no one ahead.
                self._held += 1
                return OperationPermit(self, device_id)
            # Queue
            event = threading.Event()
            self._waiters[device_id] = event

        # Wait outside the lock so releases can wake us.
        assert event is not None
        signaled = event.wait(timeout=timeout)
        # The waker has already accounted for the slot (held incremented
        # before the event is set).  If this is a spurious wake we just
        # return the permit — the waker already consumed the slot.
        if not signaled:
            # Timeout — remove ourselves from the queue and clean up.
            with self._lock:
                self._waiters.pop(device_id, None)
            raise PermitDenied(f"permit wait timed out after {timeout}s")

        if self._shutdown:
            raise PermitDenied("scheduler shut down during wait")

        # Check cancellation flag (set by cancel_device during wait).
        with self._lock:
            cancelled = device_id in self._cancelled
            if cancelled:
                self._cancelled.discard(device_id)
                self._held = max(0, self._held - 1)
                self._wake_one_locked()
        if cancelled:
            raise PermitDenied(f"permit cancelled for device {device_id}")

        return OperationPermit(self, device_id)

    def _release(self, permit: OperationPermit) -> None:
        with self._lock:
            self._held = max(0, self._held - 1)
            self._wake_one_locked()

    def _wake_one_locked(self) -> None:
        """Caller holds self._lock. Promote the oldest waiter if slots available."""
        if self._shutdown:
            return
        while self._waiters and self._held < self._max_concurrent:
            device_id, event = self._waiters.popitem(last=False)
            self._held += 1
            event.set()

    def cancel_device(self, device_id: int) -> None:
        """Abort one waiting job (abort/cancellation). Idempotent.

        Sets a cancellation flag so the waiter raises PermitDenied instead of
        returning a permit (the slot is released back to the next waiter)."""
        with self._lock:
            self._cancelled.add(device_id)
            event = self._waiters.pop(device_id, None)
        if event is not None:
            event.set()

    def shutdown(self) -> None:
        """Wake every waiter with a shutdown signal. Idempotent."""
        with self._lock:
            self._shutdown = True
            waiters = list(self._waiters.items())
            self._waiters.clear()
        for _device_id, event in waiters:
            event.set()
        logger.info(
            "operation_scheduler_shutdown held=%d woken=%d",
            self._held, len(waiters),
        )

    # ── testing / observability hooks ──────────────────────────────────────

    def _waiting_devices(self) -> list[int]:
        with self._lock:
            return list(self._waiters.keys())
