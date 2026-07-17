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
        with pytest.raises(PermitDenied, match="already waiting"):
            s.acquire(1)

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
