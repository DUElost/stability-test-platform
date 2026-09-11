"""#742: terminal outbox dead-letter after max attempts."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from requests import HTTPError

from backend.agent.outbox_drainer import OutboxDrainThread
from backend.agent.registry.local_db import LocalDB


@pytest.fixture
def db(tmp_path):
    db = LocalDB()
    db.initialize(str(tmp_path / "agent.db"))
    yield db
    db.close()


def test_terminal_outbox_schema_includes_dead_letter(db):
    cols = {
        row["name"]
        for row in db._conn.execute(
            "PRAGMA table_info(job_terminal_outbox)"
        ).fetchall()
    }
    assert "dead_letter" in cols


def test_legacy_terminal_outbox_gets_dead_letter_column(tmp_path):
    """Idempotent ALTER for agents that already have the table without dead_letter."""
    import sqlite3

    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE job_terminal_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL UNIQUE,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            acked INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.close()

    db = LocalDB()
    db.initialize(path)
    cols = {
        row["name"]
        for row in db._conn.execute(
            "PRAGMA table_info(job_terminal_outbox)"
        ).fetchall()
    }
    assert "dead_letter" in cols
    db.close()


def test_mark_terminal_dead_letter_excludes_from_pending(db):
    db.enqueue_terminal(7, {"status": "FAILED"})
    assert len(db.get_pending_terminals()) == 1
    db.mark_terminal_dead_letter(7, "permanent")
    assert db.get_pending_terminals() == []
    assert db.count_pending_terminals() == 0
    assert db.count_terminal_dead_letters() == 1
    dl = db.get_terminal_dead_letters()
    assert len(dl) == 1
    assert dl[0]["job_id"] == 7


def test_drain_500_retries_indefinitely_no_dead_letter(db):
    """5xx (transient server error) must never dead-letter — infinite retry (#762).

    PR #762 intentionally separates 4xx (permanent rejection → dead-letter) from
    5xx/network (transient → bump attempts, stay pending forever).
    """
    db.enqueue_terminal(42, {"status": "FAILED"})
    drainer = OutboxDrainThread("http://127.0.0.1:8000", db, interval=15.0)
    drainer._MAX_TERMINAL_ATTEMPTS = 3

    resp = MagicMock(status_code=500)
    resp.raise_for_status.side_effect = HTTPError("HTTP 500", response=resp)

    with patch("backend.agent.outbox_drainer.requests.post", return_value=resp):
        for _ in range(3):
            assert drainer._drain_once() == 0

    pending = db.get_pending_terminals()
    assert len(pending) == 1, "5xx must stay pending — not dead-lettered"
    assert pending[0]["attempts"] == 3
    assert db.count_terminal_dead_letters() == 0
    assert drainer.snapshot_metrics()["dead_letter_total"] == 0


def test_drain_below_max_attempts_stays_pending(db):
    db.enqueue_terminal(43, {"status": "FAILED"})
    drainer = OutboxDrainThread("http://127.0.0.1:8000", db, interval=15.0)
    drainer._MAX_TERMINAL_ATTEMPTS = 5

    resp = MagicMock(status_code=500)
    resp.raise_for_status.side_effect = HTTPError("HTTP 500", response=resp)

    with patch("backend.agent.outbox_drainer.requests.post", return_value=resp):
        for _ in range(3):
            drainer._drain_once()

    pending = db.get_pending_terminals()
    assert len(pending) == 1
    assert pending[0]["attempts"] == 3
    assert db.count_terminal_dead_letters() == 0
    assert drainer.snapshot_metrics()["dead_letter_total"] == 0


def test_dead_letter_does_not_block_newer_terminal(db):
    """A dead-lettered entry (via 4xx) must not starve subsequent queue entries.

    5xx is NOT used here: per #762, 5xx retries indefinitely and never dead-letters.
    A 400 Bad Request (permanent 4xx) triggers the dead-letter path after 1 attempt.
    """
    db.enqueue_terminal(1, {"status": "FAILED"})
    drainer = OutboxDrainThread("http://127.0.0.1:8000", db, interval=15.0)
    drainer._MAX_TERMINAL_ATTEMPTS = 1
    resp = MagicMock(status_code=400)
    resp.raise_for_status.side_effect = HTTPError("HTTP 400", response=resp)
    with patch("backend.agent.outbox_drainer.requests.post", return_value=resp):
        drainer._drain_once()
    assert db.count_terminal_dead_letters() == 1

    db.enqueue_terminal(2, {"status": "COMPLETED"})
    pending = db.get_pending_terminals()
    assert len(pending) == 1
    assert pending[0]["job_id"] == 2


def test_prune_acked_terminals_skips_dead_letter(db):
    db.enqueue_terminal(10, {"status": "FAILED"})
    db.ack_terminal(10)
    db.mark_terminal_dead_letter(10, "kept")
    for job_id in range(11, 16):
        db.enqueue_terminal(job_id, {"status": "FAILED"})
        db.ack_terminal(job_id)
    db.prune_acked_terminals(keep_recent=2)
    assert db.count_terminal_dead_letters() == 1
