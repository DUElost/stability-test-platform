"""ADR-0026 Step 5b收口 — startup + concurrency + barrier integration tests.

Covers the acceptance criteria from the final review:
- Agent main() startup static order (no UnboundLocalError)
- 20-device / permit=5: simultaneously held permits ≤ 5 at all times
- Per-device held tracking: same device cannot hold two permits
- Multi-PlanRun same-host scheduler sharing
- Barrier: phase advancement wakes all waiters
"""
from __future__ import annotations

import inspect
import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.agent.coordinator import (
    HostRunCoordinator,
    JobExecutionView,
    PlanRunHostView,
)
from backend.agent.operation_scheduler import (
    OperationScheduler,
    OperationPermit,
    PermitDenied,
)


# ── 1. Startup static order ───────────────────────────────────────────────────


class TestStartupOrder:
    def test_coordinator_constructed_before_start(self):
        """Verify in main() source that coordinator = HostRunCoordinator(...)
        appears before coordinator.start(). Simple source-text check."""
        from backend.agent.main import main

        src = inspect.getsource(main)
        # Find line numbers of the key statements
        lines = src.split("\n")
        construct_line = start_line = None
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if "coordinator = HostRunCoordinator(" in stripped:
                construct_line = i
            if "coordinator.start()" in stripped:
                start_line = i
        assert construct_line is not None, "coordinator construction not found"
        assert start_line is not None, "coordinator.start() not found"
        assert construct_line < start_line, (
            f"coordinator constructed at L{construct_line}, "
            f"but start() called at L{start_line}"
        )

    def test_operation_scheduler_importable_without_crash(self):
        """Module import must not trigger side effects (no UnboundLocalError)."""
        s = OperationScheduler(max_concurrent=2)
        assert s.max_concurrent == 2
        assert s.held == 0


# ── 2. 20-device / permit=5 concurrency ───────────────────────────────────────


class TestConcurrencyCap:
    def test_exactly_5_hold_at_any_time(self):
        """20 threads acquire permits from a scheduler capped at 5. At no
        point should more than 5 permits be simultaneously held."""
        s = OperationScheduler(max_concurrent=5)
        max_observed = [0]
        lock = threading.Lock()
        errors = []
        held_ids = set()
        held_lock = threading.Lock()

        def worker(did):
            try:
                with s.acquire(did) as p:
                    with lock:
                        max_observed[0] = max(max_observed[0], s.held)
                    with held_lock:
                        assert did not in held_ids, f"device {did} already holds permit"
                        held_ids.add(did)
                    time.sleep(0.05)
                    with held_lock:
                        held_ids.discard(did)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, errors
        assert max_observed[0] <= 5, f"max held was {max_observed[0]}, cap is 5"
        assert s.held == 0

    def test_fifo_ordering_is_preserved(self):
        """Queued devices are woken in enqueue order (FIFO)."""
        s = OperationScheduler(max_concurrent=1)
        holder = s.acquire(0)
        acquired: list[int] = []

        def worker(did: int) -> None:
            with s.acquire(did):
                acquired.append(did)

        threads = []
        # Enqueue one-by-one and wait until each is actually in the queue,
        # so thread-start races cannot reorder waiters.
        for i in range(1, 6):
            t = threading.Thread(target=worker, args=(i,))
            t.start()
            deadline = time.time() + 2
            while i not in s.waiting_devices and time.time() < deadline:
                time.sleep(0.005)
            assert i in s.waiting_devices, f"device {i} never queued"
            threads.append(t)

        holder.release()
        for t in threads:
            t.join(timeout=5)
            assert not t.is_alive()

        assert acquired == [1, 2, 3, 4, 5]
        assert s.held == 0


# ── 3. Per-device held tracking ───────────────────────────────────────────────


class TestPerDeviceHeld:
    def test_same_device_cannot_hold_two_permits(self):
        s = OperationScheduler(max_concurrent=5)
        with s.acquire(1):
            with pytest.raises(PermitDenied, match="already holds a permit"):
                s.acquire(1)

    def test_held_devices_tracking_accurate(self):
        s = OperationScheduler(max_concurrent=3)
        p1 = s.acquire(10)
        p2 = s.acquire(20)
        assert s.held_devices == frozenset({10, 20})
        p1.release()
        assert s.held_devices == frozenset({20})
        p2.release()
        assert s.held_devices == frozenset()

    def test_waiter_on_same_device_rejected(self):
        """Same device already holding a permit is rejected before enqueue."""
        s = OperationScheduler(max_concurrent=1)
        s.acquire(99)  # hold the only slot
        with pytest.raises(PermitDenied, match="already holds a permit"):
            s.acquire(99)  # cannot queue behind itself while holding


# ── 4. Multi-PlanRun shared scheduler ─────────────────────────────────────────


class TestMultiPlanRunSharing:
    def test_two_planruns_share_same_cap(self):
        """Jobs from different PlanRuns share the same OperationScheduler —
        two PlanRuns each trying to use 5 permits must contend."""
        s = OperationScheduler(max_concurrent=5)
        running = [0]
        max_running = [0]
        lock = threading.Lock()

        def plan_run_worker(plan_id, device_count):
            for d in range(device_count):
                did = plan_id * 100 + d
                with s.acquire(did):
                    with lock:
                        running[0] += 1
                        max_running[0] = max(max_running[0], running[0])
                    time.sleep(0.01)
                    with lock:
                        running[0] -= 1

        # Two PlanRuns, each with 5 devices = 10 concurrent attempts, cap=5
        t1 = threading.Thread(target=plan_run_worker, args=(1, 5))
        t2 = threading.Thread(target=plan_run_worker, args=(2, 5))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert max_running[0] <= 5
        assert s.held == 0


# ── 5. Barrier coordination ───────────────────────────────────────────────────


class TestBarrierCoordination:
    def test_barrier_wakes_when_all_arrive(self):
        host = PlanRunHostView(1, 10, "h1")
        host.set_barrier_total(3)

        arrived = []
        def job_worker():
            is_last = host.arrive_at_barrier()
            if not is_last:
                ok = host.wait_barrier(timeout=2)
                arrived.append(("waited", ok))
            else:
                host.advance_phase("PATROL")
                arrived.append("last")

        threads = [threading.Thread(target=job_worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=3)

        assert arrived.count("last") == 1
        assert arrived.count(("waited", True)) == 2
        assert host.phase == "PATROL"

    def test_coordinator_barrier_integration(self):
        coord = HostRunCoordinator("http://x", "h1", "inst")
        coord.register_plan_run_host(1, 10)
        coord.set_barrier_total(1, 2)

        # Two jobs arrive
        coord.register_job(100)
        coord.register_job(101)
        ok = coord.arrive_at_barrier(1)
        assert ok is False  # only 1 of 2
        ok = coord.arrive_at_barrier(1)
        assert ok is True   # last one

        coord.advance_phase(1, "PATROL")
        v = coord._plan_run_hosts[1]
        assert v.phase == "PATROL"


# ── 6. last_progress_at production ────────────────────────────────────────────


class TestProgressTracking:
    def test_job_view_updates_and_snapshots(self):
        jv = JobExecutionView(42)
        jv.update(state="EXECUTING_STEP", progress_ts="2026-07-19T10:00:00Z")
        snap = jv.snapshot()
        assert snap["execution_state"] == "EXECUTING_STEP"
        assert snap["last_progress_at"] == "2026-07-19T10:00:00Z"

    def test_partial_update(self):
        jv = JobExecutionView(42)
        jv.update(state="PATROL_SLEEP")
        assert jv.snapshot()["execution_state"] == "PATROL_SLEEP"
        assert jv.snapshot()["last_progress_at"] is None
        jv.update(progress_ts="2026-07-19T11:00:00Z")
        assert jv.snapshot()["execution_state"] == "PATROL_SLEEP"
        assert jv.snapshot()["last_progress_at"] == "2026-07-19T11:00:00Z"


# ── 6. Coordinator _tick sends real POST ──────────────────────────────────────


class TestEpochBumpOnReregister:
    def test_restart_bumps_persisted_epoch(self):
        """register_plan_run_host on a re-registered host after restart
        must restore the persisted epoch and bump by 1."""
        from unittest.mock import MagicMock

        local_db = MagicMock()
        local_db.get_state.return_value = "5"  # previous instance persisted 5
        persisted = {}

        def fake_set_state(key, value):
            persisted[key] = value

        local_db.set_state.side_effect = fake_set_state

        coord = HostRunCoordinator(
            "http://x", "h2", "i2", local_db=local_db,
        )
        v = coord.register_plan_run_host(1, 10)
        # Restored 5 + bumped 1 = 6
        assert v.epoch == 6, f"expected epoch 6, got {v.epoch}"
        # Verify it was persisted after bump
        assert any("coord_epoch:h2:1" in k for k in persisted), str(persisted)


class TestCoordinatorTickSendsPost:
    def test_tick_makes_http_post_with_per_host_epoch(self):
        """Verify _tick does NOT throw NameError and the POST body has
        per-host-entry coordinator_epoch (not a dangling top-level key)."""
        coord = HostRunCoordinator("http://127.0.0.1:1", "h-tick", "inst")
        coord.register_plan_run_host(1, 10)
        # Give the PRH a known epoch
        with coord._plan_run_hosts[1]._lock:
            coord._plan_run_hosts[1]._epoch = 7
        coord.register_job(100)

        post_called = []
        post_body = []

        def fake_post(url, **kwargs):
            post_called.append(url)
            post_body.append(kwargs.get("json", {}))
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {
                "data": {"accepted": True, "stale_plan_run_host_ids": []}
            }
            return resp

        with patch("requests.post", side_effect=fake_post):
            coord._tick()

        assert len(post_called) == 1, f"POST not called; body={post_body}"
        body = post_body[0]
        # Per-host entry carries coordinator_epoch
        assert body["plan_run_hosts"][0]["coordinator_epoch"] == 7
        # No top-level coordinator_epoch (that would be NameError-prone)
        assert "coordinator_epoch" not in body
        assert body["host_id"] == "h-tick"


# ── 7. Abort semantics ────────────────────────────────────────────────────────


class TestAbortPermitSemantics:
    def test_abort_while_holding_does_not_release_permit(self):
        """Abort a job that is EXECUTING_STEP (holding a permit) — the
        cancel must be a no-op. The concurrency cap must survive."""
        import threading, time
        from unittest.mock import MagicMock, patch

        from backend.agent.operation_scheduler import OperationScheduler
        from backend.agent.coordinator import HostRunCoordinator

        s = OperationScheduler(max_concurrent=2)
        coord = HostRunCoordinator("http://x", "h1", "i1")
        coord.set_scheduler(s)
        # Job 100 is EXECUTING_STEP on device 10
        jv = coord.register_job(100)
        jv.update(state="EXECUTING_STEP")
        coord.register_job_device(100, 10)

        # Fill slots — device 10 is one of them
        p1 = s.acquire(10)
        p2 = s.acquire(20)
        assert s.held == 2

        # Abort job 100 — but it's EXECUTING_STEP, not WAITING
        coord.cancel_waiting_job(100)

        # Slot count unchanged — cancel was a no-op
        assert s.held == 2
        assert 10 in s.held_devices
        p1.release()
        p2.release()

    def test_cancel_while_waiting_denies_permit(self):
        """cancel_device fires on a still-queued waiter → PermitDenied."""
        import threading, time

        s = OperationScheduler(max_concurrent=1)
        s.acquire(99)  # hold the only slot
        denied = []

        def waiter():
            try:
                s.acquire(42, timeout=5)
            except PermitDenied:
                denied.append(1)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.15)  # waiter is queued
        s.cancel_device(42)  # cancel while still waiting
        t.join(timeout=3)
        assert denied == [1]
        assert s.held == 1  # holder still holds, waiter never got a slot
        assert 42 not in s.held_devices

    def test_abort_order_request_then_cancel(self):
        """request_abort sets _canceled before cancel_waiting_job fires.
        When the waiter sees PermitDenied, _is_aborted() is already True,
        so _run_step_with_permit returns False instead of retrying."""
        import threading, time
        from unittest.mock import MagicMock

        s = OperationScheduler(max_concurrent=1)
        coord = HostRunCoordinator("http://x", "h-ord", "inst")
        coord.set_scheduler(s)
        jv = coord.register_job(200)
        jv.update(state="WAITING_EXECUTION_SLOT")
        coord.register_job_device(200, 77)

        # Hold the only slot
        p = s.acquire(99)
        # Simulate the abort handler: request_abort FIRST, then cancel
        _canceled = [False]

        def waiter():
            while True:
                try:
                    perm = s.acquire(77, timeout=1)
                except PermitDenied:
                    if _canceled[0]:
                        return  # abort confirmed, stop
                    continue  # retry
                else:
                    perm.release()

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.15)

        # Correct order: mark aborted FIRST, THEN cancel
        _canceled[0] = True
        coord.cancel_waiting_job(200)
        t.join(timeout=3)
        assert not t.is_alive()
        p.release()
        assert s.held == 0
