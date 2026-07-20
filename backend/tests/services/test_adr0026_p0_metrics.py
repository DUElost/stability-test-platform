"""ADR-0026 P0 metrics helpers — queue latency / renew / aggregation / concurrency."""

from __future__ import annotations

from backend.core import metrics as m


def test_record_admission_queue_latency_accepts_non_negative():
    m.record_admission_queue_latency(12.5)
    m.record_admission_queue_latency(-1)  # ignored
    m.set_admission_queue_depth(3)


def test_record_lease_extend_batch_counts_outcomes():
    m.record_lease_extend_batch(
        {"renewed": 2, "stale_token": 1, "lease_missing": 0},
        batch_size=3,
    )


def test_record_plan_run_aggregation_duration_paths():
    m.record_plan_run_aggregation_duration(0.01, "counters")
    m.record_plan_run_aggregation_duration(0.05, "full_scan")
    m.record_plan_run_devices_query_duration(0.02)


def test_record_host_operation_concurrency():
    m.record_host_operation_concurrency(
        "host-a", held=2, max_slots=5, waiting=1,
    )


def test_operation_scheduler_concurrency_snapshot():
    from backend.agent.operation_scheduler import OperationScheduler

    s = OperationScheduler(max_concurrent=3)
    p1 = s.acquire(10)
    p2 = s.acquire(20)
    snap = s.concurrency_snapshot()
    assert snap["held"] == 2
    assert snap["max"] == 3
    assert snap["waiting"] == 0
    assert snap["held_devices"] == 2
    assert s.waiter_count == 0
    p1.release()
    p2.release()
