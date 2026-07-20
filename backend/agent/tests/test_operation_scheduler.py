"""ADR-0026 Step 5b — OperationScheduler unit tests."""
import threading
import time

import pytest

from backend.agent.operation_scheduler import (
    OperationScheduler,
    PermitDenied,
)


class TestOperationScheduler:
    def test_acquire_up_to_cap(self):
        s = OperationScheduler(max_concurrent=2)
        p1 = s.acquire(1)
        p2 = s.acquire(2)
        assert s.held == 2
        p1.release()
        assert s.held == 1
        p2.release()
        assert s.held == 0

    def test_third_waits_then_proceeds(self):
        s = OperationScheduler(max_concurrent=2)
        p1 = s.acquire(1)
        p2 = s.acquire(2)

        result = []
        def waiter():
            p = s.acquire(3)
            result.append("got")
            p.release()

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.1)
        assert result == []  # still waiting
        p1.release()
        t.join(timeout=2)
        assert not t.is_alive()
        assert result == ["got"]
        p2.release()

    def test_per_device_fairness(self):
        s = OperationScheduler(max_concurrent=1)
        s.acquire(1)  # hold the only slot

        # Second job on the SAME device must raise immediately
        # (before enqueue — otherwise it waits forever for itself).
        with pytest.raises(PermitDenied, match="already holds a permit"):
            s.acquire(1)

    def test_already_waiting_rejected(self):
        s = OperationScheduler(max_concurrent=1)
        s.acquire(1)  # fill cap so device 2 must queue

        started = threading.Event()
        result = []

        def waiter():
            started.set()
            try:
                s.acquire(2)
            except PermitDenied as exc:
                result.append(str(exc))

        t = threading.Thread(target=waiter)
        t.start()
        assert started.wait(timeout=2)
        time.sleep(0.1)  # ensure device 2 is queued
        with pytest.raises(PermitDenied, match="already waiting"):
            s.acquire(2)
        s.cancel_device(2)
        t.join(timeout=2)
        assert not t.is_alive()
        assert result and "cancelled" in result[0]

    def test_cancel_device_wakes_waiter(self):
        s = OperationScheduler(max_concurrent=1)
        s.acquire(1)  # hold

        result = []
        def waiter():
            try:
                s.acquire(2)
            except PermitDenied:
                result.append("denied")

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.1)
        s.cancel_device(2)
        t.join(timeout=2)
        assert result == ["denied"]

    def test_cancel_after_promote_releases_slot(self):
        """Promote → cancel before acquire returns → slot must be released.

        Reproduces the Path-2 window: waiter is in _held_devices /
        _pending_handoff but has not yet received OperationPermit.
        """
        in_post_wake = threading.Event()
        allow_finish = threading.Event()

        class PausingEvent(threading.Event):
            def wait(self, timeout=None):
                ok = super().wait(timeout=timeout)
                if ok:
                    in_post_wake.set()
                    assert allow_finish.wait(timeout=5)
                return ok

        s = OperationScheduler(max_concurrent=1, event_factory=PausingEvent)
        holder = s.acquire(1)

        result = []

        def waiter():
            try:
                permit = s.acquire(40, timeout=5)
                result.append("got")
                permit.release()
            except PermitDenied:
                result.append("denied")

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.15)
        assert 40 in s.waiting_devices

        holder.release()  # promotes 40 into _pending_handoff
        assert in_post_wake.wait(timeout=2)
        assert s.held == 1
        assert 40 in s.held_devices

        s.cancel_device(40)
        allow_finish.set()
        t.join(timeout=3)

        assert result == ["denied"]
        assert s.held == 0
        assert 40 not in s.held_devices
        # Cap still usable
        p = s.acquire(50, timeout=1)
        assert s.held == 1
        p.release()

    def test_cancel_while_holding_is_noop(self):
        """cancel_device after acquire returned must not release the slot."""
        s = OperationScheduler(max_concurrent=1)
        p = s.acquire(10)
        assert s.held == 1
        s.cancel_device(10)  # handoff complete — no-op
        assert s.held == 1
        assert 10 in s.held_devices
        p.release()
        assert s.held == 0

    def test_shutdown_wakes_all(self):
        s = OperationScheduler(max_concurrent=1)
        s.acquire(1)

        results = []
        def waiter(did):
            try:
                s.acquire(did)
            except PermitDenied:
                results.append(1)

        threads = [threading.Thread(target=waiter, args=(i + 2,)) for i in range(2)]
        for t in threads:
            t.start()
        time.sleep(0.15)
        s.shutdown()
        for t in threads:
            t.join(timeout=3)
        assert sum(results) == 2

    def test_hot_adjust_up_wakes_waiter(self):
        s = OperationScheduler(max_concurrent=1)
        p = s.acquire(1)

        result = []
        def waiter():
            p2 = s.acquire(2)
            result.append("got")
            p2.release()

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.1)
        s.set_max_concurrent(2)
        t.join(timeout=2)
        assert result == ["got"]
        p.release()

    def test_held_never_goes_negative(self):
        s = OperationScheduler(max_concurrent=1)
        p = s.acquire(1)
        p.release()
        p.release()  # double-release — benign
        assert s.held == 0

    def test_shrink_does_not_preempt(self):
        s = OperationScheduler(max_concurrent=3)
        p1 = s.acquire(1)
        p2 = s.acquire(2)
        p3 = s.acquire(3)
        assert s.held == 3
        s.set_max_concurrent(1)  # shrink — held permits stay
        assert s.held == 3
        p1.release()
        p2.release()
        p3.release()
