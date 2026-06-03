"""AEE watcher state namespace migration tests."""

from __future__ import annotations

import json
import sqlite3

from backend.agent.aee.state_migration import migrate_legacy_aee_state_keys


def _init_agent_state(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE agent_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()
    return conn


def _get_value(conn, key: str) -> str:
    row = conn.execute("SELECT value FROM agent_state WHERE key=?", (key,)).fetchone()
    assert row is not None
    return str(row[0])


def test_migrate_legacy_aee_state_keys_merges_processed_and_pending(tmp_path):
    db_path = tmp_path / "agent_state.db"
    conn = _init_agent_state(db_path)
    conn.executemany(
        "INSERT INTO agent_state (key, value) VALUES (?, ?)",
        [
            (
                "scan_aee:SX:aee_exp:processed_entries",
                json.dumps(["legacy-line"]),
            ),
            (
                "watcher:aee:SX:aee_exp:processed_entries",
                json.dumps(["watcher-line"]),
            ),
            (
                "scan_aee:SX:aee_exp:pending_pull",
                json.dumps({"legacy-line": {"db_path": "/data/aee_exp/db.1"}}),
            ),
            (
                "watcher:aee:SX:aee_exp:pending_pull",
                json.dumps({"watcher-line": {"db_path": "/data/aee_exp/db.2"}}),
            ),
            ("unrelated:key", "value"),
        ],
    )
    conn.commit()

    summary = migrate_legacy_aee_state_keys(str(db_path))

    assert summary["processed_entries_migrated"] == 1
    assert summary["pending_pull_migrated"] == 1
    assert json.loads(_get_value(conn, "watcher:aee:SX:aee_exp:processed_entries")) == [
        "legacy-line",
        "watcher-line",
    ]
    assert json.loads(_get_value(conn, "watcher:aee:SX:aee_exp:pending_pull")) == {
        "legacy-line": {"db_path": "/data/aee_exp/db.1"},
        "watcher-line": {"db_path": "/data/aee_exp/db.2"},
    }
    assert _get_value(conn, "scan_aee:SX:aee_exp:processed_entries")


def test_migrate_legacy_aee_state_keys_dry_run_does_not_write(tmp_path):
    db_path = tmp_path / "agent_state.db"
    conn = _init_agent_state(db_path)
    conn.execute(
        "INSERT INTO agent_state (key, value) VALUES (?, ?)",
        (
            "scan_aee:SX:vendor_aee_exp:processed_entries",
            json.dumps(["legacy-line"]),
        ),
    )
    conn.commit()

    summary = migrate_legacy_aee_state_keys(str(db_path), dry_run=True)

    assert summary["dry_run"] is True
    assert summary["processed_entries_migrated"] == 1
    row = conn.execute(
        "SELECT value FROM agent_state WHERE key=?",
        ("watcher:aee:SX:vendor_aee_exp:processed_entries",),
    ).fetchone()
    assert row is None
