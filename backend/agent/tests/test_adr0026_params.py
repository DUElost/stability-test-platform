"""ADR-0026 pending-list parameter invariants + permit contention sim."""

from __future__ import annotations

from backend.core.adr0026_params import (
    DEFAULTS,
    aging_effective_priority,
    simulate_permit_contention,
    validate_param_invariants,
)


def test_defaults_satisfy_invariants():
    assert validate_param_invariants() == []


def test_aging_boost_caps_and_steps():
    assert aging_effective_priority(0, 0) == 0
    assert aging_effective_priority(0, 1799) == 0
    assert aging_effective_priority(0, 1800) == 1
    assert aging_effective_priority(0, 1800 * 5) == 5
    assert aging_effective_priority(0, 1800 * 99) == 5  # capped
    assert aging_effective_priority(3, 1800 * 2) == 5


def test_low_priority_eventually_outranks_static_high():
    """After max aging, priority-0 matches a static priority-5 peer."""
    aged = aging_effective_priority(0, DEFAULTS.admission_aging_step_seconds * 5)
    assert aged == aging_effective_priority(5, 0)


def test_coord_timeout_invariant_rejects_tight_window():
    errs = validate_param_invariants(
        coord_interval=30,
        coord_timeout=60,  # < 3×30
    )
    assert any("coord_timeout" in e for e in errs)


def test_permit5_under_capacity_for_20_device_host():
    # 20 devices, 30s step cadence, cap=5 → demand 0.67 ops/s, cap 0.17/s wait…
    # With 30s steps the capacity is 5/30 ≈ 0.167 ops/s; demand 20/30 ≈ 0.667
    # → saturated. Mean wait should stay well under coordinator timeout.
    stats = simulate_permit_contention(
        devices=20,
        permit_cap=5,
        step_duration_s=30.0,
        wall_s=3600.0,
    )
    assert stats["utilization"] == 1.0
    assert stats["mean_queue_wait_s"] < DEFAULTS.coordinator_heartbeat_timeout_seconds


def test_permit5_idle_when_few_devices():
    stats = simulate_permit_contention(
        devices=4,
        permit_cap=5,
        step_duration_s=30.0,
        wall_s=600.0,
    )
    assert stats["utilization"] < 1.0
    assert stats["mean_queue_wait_s"] == 0.0


def test_raising_permit_reduces_wait_under_storm():
    low = simulate_permit_contention(
        devices=40, permit_cap=5, step_duration_s=20.0, wall_s=600.0
    )
    high = simulate_permit_contention(
        devices=40, permit_cap=10, step_duration_s=20.0, wall_s=600.0
    )
    assert high["mean_queue_wait_s"] < low["mean_queue_wait_s"]
