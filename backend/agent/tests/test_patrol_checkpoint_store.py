"""Tests for patrol cycle checkpoint persistence."""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from backend.agent.registry.patrol_checkpoint_store import (
    PatrolCycleCheckpointStore,
    PatrolCycleCheckpointStoreRecoverableError,
)


@pytest.fixture()
def store(tmp_path) -> PatrolCycleCheckpointStore:
    s = PatrolCycleCheckpointStore(tmp_path / "checkpoint.db")
    s.initialize()
    return s


def test_checkpoint_roundtrip_and_list_for_recovery(store: PatrolCycleCheckpointStore):
    store.save("job-1", {"cycle": 7, "last_failed_step_id": "patrol:foo"})
    row = store.get_for_recovery("job-1")

    assert row is not None
    assert row.job_id == "job-1"
    assert row.checkpoint["cycle"] == 7
    assert row.checkpoint["last_failed_step_id"] == "patrol:foo"

    recovered = store.list_for_recovery()
    assert len(recovered) == 1


def test_drop_removes_checkpoint(store: PatrolCycleCheckpointStore):
    store.save("job-2", {"cycle": 3})
    store.drop("job-2")

    assert store.list_for_recovery() == []


def test_corrupted_json_row_is_ignored_for_recovery(
    store: PatrolCycleCheckpointStore,
):
    with sqlite3.connect(store._db_path) as conn:
        conn.execute(
            "INSERT INTO patrol_cycle_checkpoint (job_id, checkpoint_json, updated_at) "
            "VALUES (?, ?, ?)",
            ("bad-json", "{not-json", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()

    assert store.list_for_recovery() == []


def test_initialize_creates_table_and_wal_mode(store: PatrolCycleCheckpointStore):
    with sqlite3.connect(store._db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert "patrol_cycle_checkpoint" in tables
    assert journal_mode.lower() == "wal"


def test_initialize_is_idempotent(store: PatrolCycleCheckpointStore):
    store.initialize()
    store.initialize()

    assert store.get_for_recovery("missing") is None


def test_save_drops_checkpoint_when_payload_is_none(
    store: PatrolCycleCheckpointStore,
):
    store.save("job-3", {"cycle": 4})
    store.save("job-3", None)

    assert store.get_for_recovery("job-3") is None
    assert store.list_for_recovery() == []


def test_save_retries_transient_locked_errors(monkeypatch, tmp_path):
    store = PatrolCycleCheckpointStore(tmp_path / "retry.db")
    store.initialize()
    real_connect = sqlite3.connect
    attempts = {"count": 0}

    def flaky_connect(path: str, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_connect(path, **kwargs)

    monkeypatch.setattr(
        "backend.agent.registry.patrol_checkpoint_store.sqlite3.connect",
        flaky_connect,
    )
    monkeypatch.setattr(
        "backend.agent.registry.patrol_checkpoint_store._sleep",
        lambda _seconds: None,
    )

    store.save("job-4", {"cycle": 9})

    row = store.get_for_recovery("job-4")
    assert row is not None
    assert row.checkpoint["cycle"] == 9
    assert attempts["count"] >= 2


def test_save_raises_recoverable_error_after_retry_exhaustion(
    monkeypatch, tmp_path
):
    store = PatrolCycleCheckpointStore(tmp_path / "locked.db")
    store.initialize()
    store._initialized = True

    def locked_connect(_path: str, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        "backend.agent.registry.patrol_checkpoint_store.sqlite3.connect",
        locked_connect,
    )
    monkeypatch.setattr(
        "backend.agent.registry.patrol_checkpoint_store._sleep",
        lambda _seconds: None,
    )

    with pytest.raises(PatrolCycleCheckpointStoreRecoverableError):
        store.save("job-5", {"cycle": 1})


def test_claim_pending_batch_returns_unclaimed_rows_in_order(
    store: PatrolCycleCheckpointStore,
):
    store.save("job-a", {"cycle": 1})
    store.save("job-b", {"cycle": 2})

    first_batch = store.claim_pending_batch(batch_size=2)
    second_batch = store.claim_pending_batch(batch_size=2)

    assert [row.job_id for row in first_batch] == ["job-a", "job-b"]
    assert second_batch == []


def test_claim_pending_batch_respects_batch_limit(
    store: PatrolCycleCheckpointStore,
):
    store.save("job-a", {"cycle": 1})
    store.save("job-b", {"cycle": 2})
    store.save("job-c", {"cycle": 3})

    batch = store.claim_pending_batch(batch_size=2)

    assert len(batch) == 2
    assert [row.job_id for row in batch] == ["job-a", "job-b"]


def test_claim_pending_batch_leaves_already_claimed_rows_untouchable(
    tmp_path, monkeypatch
):
    store = PatrolCycleCheckpointStore(tmp_path / "claim.db")
    store.save("job-a", {"cycle": 1})
    with sqlite3.connect(store._db_path) as conn:
        conn.execute(
            "UPDATE patrol_cycle_checkpoint SET claimed_at = ? WHERE job_id = ?",
            (time.time(), "job-a"),
        )
        conn.commit()
    store.save("job-b", {"cycle": 2})

    claimed: list[str] = []

    def claim_once(_conn, job_id: str, _claimed_at: float):
        claimed.append(job_id)
        return 1 if job_id == "job-b" else 0

    monkeypatch.setattr(store, "_claim_one", claim_once)

    pending = store.claim_pending_batch(batch_size=2)

    assert claimed == ["job-b"]
    assert [row.job_id for row in pending] == ["job-b"]


def test_claim_pending_batch_retries_and_raises_on_lock(
    store: PatrolCycleCheckpointStore, monkeypatch
):
    store.save("job-a", {"cycle": 1})

    def locked_batch(_batch_size: int):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_claim_batch_once", locked_batch)
    monkeypatch.setattr(
        "backend.agent.registry.patrol_checkpoint_store._sleep",
        lambda _seconds: None,
    )

    with pytest.raises(PatrolCycleCheckpointStoreRecoverableError):
        store.claim_pending_batch(batch_size=1)


def test_concurrent_claim_from_separate_stores_returns_disjoint_job_ids(tmp_path):
    """Two stores sharing one DB file must not return the same job in one batch each."""
    db_path = tmp_path / "shared.db"
    store_a = PatrolCycleCheckpointStore(db_path)
    store_b = PatrolCycleCheckpointStore(db_path)
    for i in range(8):
        store_a.save(f"job-{i}", {"cycle": i})

    barrier = threading.Barrier(2)
    results: list[list[str]] = []
    errors: list[BaseException] = []

    def worker(store: PatrolCycleCheckpointStore) -> None:
        try:
            barrier.wait(timeout=5)
            rows = store.claim_pending_batch(batch_size=4)
            results.append([row.job_id for row in rows])
        except BaseException as exc:
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=(store_a,))
    t2 = threading.Thread(target=worker, args=(store_b,))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors
    assert len(results) == 2
    all_claimed = [jid for batch in results for jid in batch]
    assert len(all_claimed) == len(set(all_claimed))
    assert len(all_claimed) <= 8
