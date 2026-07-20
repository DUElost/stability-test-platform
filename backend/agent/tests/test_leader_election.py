"""ADR-0027 P3-1 — scheduler leader election unit tests (no Postgres required)."""

from __future__ import annotations

from contextlib import contextmanager

from backend.core.leader_election import (
    advisory_lock_key,
    hold_scheduler_leadership,
    leader_election_enabled,
)


def test_advisory_lock_key_stable_and_positive():
    a = advisory_lock_key("admission_pump")
    b = advisory_lock_key("admission_pump")
    c = advisory_lock_key("counter_reconcile")
    assert a == b
    assert a != c
    assert 0 < a < 2**63
    assert 0 < c < 2**63


def test_election_disabled_always_leader(monkeypatch):
    monkeypatch.setenv("STP_SCHEDULER_LEADER_ELECTION", "0")
    assert leader_election_enabled() is False
    with hold_scheduler_leadership("admission_pump") as ok:
        assert ok is True


def test_election_enabled_sqlite_url_skips_lock(monkeypatch):
    """Non-Postgres DATABASE_URL must not open a live connection."""
    monkeypatch.setenv("STP_SCHEDULER_LEADER_ELECTION", "1")
    monkeypatch.delenv("TESTING", raising=False)
    import backend.core.database as db_mod

    monkeypatch.setattr(db_mod, "DATABASE_URL", "sqlite:///tmp/stp-leader-test.db")
    assert leader_election_enabled() is True
    with hold_scheduler_leadership("admission_pump") as ok:
        assert ok is True


def test_pump_skips_when_not_leader(monkeypatch):
    from backend.services import admission_pump as pump_mod

    @contextmanager
    def _never(_name):
        yield False

    monkeypatch.setattr(
        "backend.core.leader_election.hold_scheduler_leadership",
        _never,
    )
    summary = pump_mod.pump_admission_tick()
    assert summary["skipped_not_leader"] == 1
    assert summary["claimed"] == 0


def test_reconcile_skips_when_not_leader(monkeypatch):
    from backend.scheduler import counter_reconciler as cr

    @contextmanager
    def _never(_name):
        yield False

    monkeypatch.setattr(
        "backend.core.leader_election.hold_scheduler_leadership",
        _never,
    )
    summary = cr.reconcile_plan_run_counters_once()
    assert summary["skipped_not_leader"] == 1
    assert summary["scanned"] == 0
