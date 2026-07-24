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

_DEFAULT_MAX_CONCURRENT_OPERATIONS = 5


def configured_max_concurrent_operations() -> int:
    """Read the host-global operation cap from the current environment.

    This is intentionally evaluated at construction/reload time rather than
    only at module import so the Agent control-plane reload command can apply
    a changed ``STP_MAX_CONCURRENT_OPERATIONS`` without restarting workers.
    """
    raw = os.getenv("STP_MAX_CONCURRENT_OPERATIONS", str(_DEFAULT_MAX_CONCURRENT_OPERATIONS))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "invalid_max_concurrent_operations raw=%r default=%d",
            raw,
            _DEFAULT_MAX_CONCURRENT_OPERATIONS,
        )
        value = _DEFAULT_MAX_CONCURRENT_OPERATIONS
    return max(value, 1)


class PermitDenied(Exception):
    """Base class for a permit request that cannot be granted."""


class PermitWaitTimeout(PermitDenied):
    """The caller may retry after checking abort/lease state."""


class PermitCancelled(PermitDenied):
    """The waiting job was explicitly cancelled or aborted."""


class SchedulerShutdown(PermitDenied):
    """The Agent is shutting down; callers must stop retrying immediately."""


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

    def __init__(
        self,
        max_concurrent: int | None = None,
        *,
        event_factory: type | None = None,
    ) -> None:
        self._max_concurrent = (
            max_concurrent
            if max_concurrent is not None
            else configured_max_concurrent_operations()
        )
        self._held: int = 0
        self._peak_held: int = 0
        self._peak_waiting: int = 0
        self._acquired_total: int = 0
        self._queued_total: int = 0
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
        # Per-device held tracking: a device can hold at most ONE permit at a
        # time (a job shouldn't be running two script steps concurrently).
        self._held_devices: set[int] = set()
        # Devices promoted by _wake_one_locked whose acquire() has not yet
        # returned the OperationPermit to the caller. cancel_device must
        # release these slots; once handoff completes, abort must not.
        self._pending_handoff: set[int] = set()
        # Test seam: inject a custom Event class to pause between wake and
        # the cancelled check (cancel-after-promote coverage).
        self._event_factory = event_factory or threading.Event

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def held(self) -> int:
        with self._lock:
            return self._held

    @property
    def waiter_count(self) -> int:
        with self._lock:
            return len(self._waiters)

    def concurrency_snapshot(self) -> dict[str, int]:
        """Observability snapshot for heartbeat /metrics bridge."""
        with self._lock:
            return {
                "held": self._held,
                "max": self._max_concurrent,
                "waiting": len(self._waiters),
                "held_devices": len(self._held_devices),
                "peak_held": self._peak_held,
                "peak_waiting": self._peak_waiting,
                "acquired_total": self._acquired_total,
                "queued_total": self._queued_total,
            }

    def set_max_concurrent(self, n: int) -> None:
        """Hot-adjust the cap. Shrinking does NOT preempt held permits —
        only new waiters are gated. Growing may wake pending waiters."""
        with self._lock:
            self._max_concurrent = max(n, 1)
            if self._held < self._max_concurrent:
                self._wake_one_locked()

    def reload_from_env(self) -> int:
        """Apply the current environment cap and return the effective value."""
        value = configured_max_concurrent_operations()
        self.set_max_concurrent(value)
        return value

    def acquire(self, device_id: int, timeout: float | None = None) -> OperationPermit:
        """Block until a permit is available or timeout / abort / shutdown.

        Returns an ``OperationPermit`` context manager. Raises
        ``PermitDenied`` on abort (waiter event set by cancel()), shutdown,
        or timeout.

        Per-device fairness: ``cancel_device(device_id)`` only wakes that
        specific waiter; ``shutdown()`` wakes all.
        """
        event: threading.Event | None = None
        with self._lock:
            # Check under the same lock used by shutdown(); otherwise a
            # shutdown between the initial check and enqueue could leave a
            # waiter behind with no future waker.
            if self._shutdown:
                raise SchedulerShutdown("scheduler shut down")
            if device_id in self._waiters:
                # Per-device fairness: only one waiter per device.
                raise PermitDenied(f"device {device_id} already waiting")
            # Must reject before enqueue — otherwise a device that already
            # holds the only slot waits forever for itself to release.
            if device_id in self._held_devices:
                raise PermitDenied(
                    f"device {device_id} already holds a permit"
                )
            if self._held < self._max_concurrent and not self._waiters:
                # Fast path: slot available, no one ahead.
                self._held += 1
                self._held_devices.add(device_id)
                self._acquired_total += 1
                self._peak_held = max(self._peak_held, self._held)
                return OperationPermit(self, device_id)
            # Queue
            event = self._event_factory()
            self._waiters[device_id] = event
            self._queued_total += 1
            self._peak_waiting = max(self._peak_waiting, len(self._waiters))

        # Wait outside the lock so releases can wake us.
        assert event is not None
        signaled = event.wait(timeout=timeout)
        # The waker accounts for a slot before setting the event. Resolve the
        # timeout/promote/cancel race under the scheduler lock; an event.is_set
        # check outside the lock could miss a concurrent promotion and leak
        # the reserved slot.
        timed_out = False
        if not signaled:
            with self._lock:
                if (
                    device_id not in self._pending_handoff
                    and device_id not in self._cancelled
                    and not self._shutdown
                ):
                    self._waiters.pop(device_id, None)
                    timed_out = True
        if timed_out:
            raise PermitWaitTimeout(f"permit wait timed out after {timeout}s")

        shutdown = False
        cancelled = False
        with self._lock:
            if self._shutdown:
                self._release_pending_handoff_locked(device_id)
                shutdown = True
            else:
                # Check cancellation flag (set by cancel_device during wait).
                # - Still queued when cancelled: slot was never consumed.
                # - Promoted then cancelled: release the reserved slot.
                cancelled = device_id in self._cancelled
                if cancelled:
                    self._cancelled.discard(device_id)
                    if device_id in self._pending_handoff:
                        self._release_pending_handoff_locked(device_id)
                        self._wake_one_locked()
                else:
                    self._pending_handoff.discard(device_id)
        if shutdown:
            raise SchedulerShutdown("scheduler shut down during wait")
        if cancelled:
            raise PermitCancelled(f"permit cancelled for device {device_id}")

        return OperationPermit(self, device_id)

    def _release(self, permit: OperationPermit) -> None:
        with self._lock:
            self._held = max(0, self._held - 1)
            self._held_devices.discard(permit.device_id)
            self._wake_one_locked()

    def _release_pending_handoff_locked(self, device_id: int) -> None:
        """Release a slot reserved for a waiter that never took ownership."""
        if device_id not in self._pending_handoff:
            return
        self._pending_handoff.discard(device_id)
        self._held = max(0, self._held - 1)
        self._held_devices.discard(device_id)

    def _wake_one_locked(self) -> None:
        """Caller holds self._lock. Promote the oldest waiter if slots available."""
        if self._shutdown:
            return
        while self._waiters and self._held < self._max_concurrent:
            device_id, event = self._waiters.popitem(last=False)
            self._held += 1
            self._held_devices.add(device_id)
            self._pending_handoff.add(device_id)
            self._acquired_total += 1
            self._peak_held = max(self._peak_held, self._held)
            event.set()

    def cancel_device(self, device_id: int) -> None:
        """Abort one WAITING job. Idempotent.

        Two waiter states:
        - Still in ``_waiters``: wake without a slot (held never incremented).
        - Already promoted (``_pending_handoff``): mark cancelled; ``acquire``
          releases the slot when it observes the flag.

        Does NOT touch a device that already received its ``OperationPermit``
        (handoff complete). Executing steps abort via the pipeline signal path.
        """
        with self._lock:
            event = self._waiters.pop(device_id, None)
            if event is not None or device_id in self._pending_handoff:
                self._cancelled.add(device_id)
            # else: already holding / unknown — no-op
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

    @property
    def held_devices(self) -> frozenset[int]:
        with self._lock:
            return frozenset(self._held_devices)

    @property
    def waiting_devices(self) -> list[int]:
        with self._lock:
            return list(self._waiters.keys())
