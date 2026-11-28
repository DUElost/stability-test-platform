"""ADR-0065 D4.9 — control-plane D_atomic = min(D_steps, D_cap, D_run)."""
from __future__ import annotations

from backend.agent.contracts.finishing_guard import ATOMIC_HARD_CAP_SECONDS
from backend.services.finishing_guard import (
    _normalize_frozen_members,
    _plan_init_wall_cap_seconds,
    compute_d_atomic,
)


def test_compute_d_atomic_takes_min_of_three():
    t_arm = 1_000_000.0
    t_job = t_arm - 100.0
    d = compute_d_atomic(
        t_arm=t_arm,
        e_curr_rem=100.0,
        subsequent_timeouts=[200, 300],
        t_job_start=t_job,
        plan_init_wall_cap=500,
    )
    d_steps = t_arm + 100 + 200 + 300
    d_cap = t_arm + ATOMIC_HARD_CAP_SECONDS
    d_run = t_job + 500
    assert d == min(d_steps, d_cap, d_run)
    assert d == d_run  # tightest in this fixture


def test_compute_d_atomic_hard_cap_binds():
    t_arm = 0.0
    d = compute_d_atomic(
        t_arm=t_arm,
        e_curr_rem=10_000.0,
        subsequent_timeouts=[10_000],
        t_job_start=None,
        plan_init_wall_cap=99_999,
    )
    assert d == t_arm + ATOMIC_HARD_CAP_SECONDS


def test_frozen_members_normalize_timeouts_and_vbus_expansion():
    snapshot = [
        {
            "stage": "init",
            "step_key": "flash",
            "timeout_seconds": 0,  # normalize → 1800
            "atomic_group": "g1",
            "sort_order": 0,
            "enabled": True,
        },
        {
            "stage": "init",
            "step_key": "oobe",
            "timeout_seconds": 120,
            "atomic_group": "g1",
            "sort_order": 1,
            "enabled": True,
        },
    ]
    frozen = _normalize_frozen_members(
        [
            "flash",
            "oobe",
            {"step_key": "return_check", "timeout_seconds": 90, "source": "vbus"},
        ],
        snapshot_steps=snapshot,
    )
    assert [m["step_key"] for m in frozen] == ["flash", "oobe", "return_check"]
    assert frozen[0]["timeout_seconds"] == 1800
    assert frozen[1]["timeout_seconds"] == 120
    assert frozen[2]["timeout_seconds"] == 90
    wall = _plan_init_wall_cap_seconds(snapshot, frozen)
    # init flash(1800)+oobe(120) + second-layer return_check(90) + reserve(60)
    assert wall == 1800 + 120 + 90 + 60
