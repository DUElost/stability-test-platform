"""LocalDB active_job_registry + get_pending_outbox unit tests (ADR-0019 Phase 3a)."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

import pytest

from backend.agent.registry.local_db import LocalDB


@pytest.fixture
def db(tmp_path):
    db = LocalDB()
    db.initialize(str(tmp_path / "agent.db"))
    yield db
    db.close()


class TestActiveJobRegistry:
    def test_save_and_get_active_jobs(self, db):
        """save → get round-trip 正常."""
        db.save_active_job(1, 10, "token-1", "SERIAL-1")
        db.save_active_job(2, 20, "token-2", "SERIAL-2")

        jobs = db.get_active_jobs()
        assert len(jobs) == 2
        assert jobs[0]["job_id"] in (1, 2)
        assert jobs[0]["device_id"] in (10, 20)
        assert jobs[0]["device_serial"] in ("SERIAL-1", "SERIAL-2")
        assert jobs[0]["fencing_token"] in ("token-1", "token-2")

    def test_save_replace_updates(self, db):
        """同一 job_id 再次 save → 覆盖（INSERT OR REPLACE）."""
        db.save_active_job(1, 10, "old-token", "SERIAL-OLD")
        db.save_active_job(1, 20, "new-token", "SERIAL-NEW")

        jobs = db.get_active_jobs()
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == 1
        assert jobs[0]["device_id"] == 20
        assert jobs[0]["device_serial"] == "SERIAL-NEW"
        assert jobs[0]["fencing_token"] == "new-token"
    def test_delete_active_job(self, db):
        """delete 后 get 不再返回该 job."""
        db.save_active_job(1, 10, "token-1", "SERIAL-1")
        db.save_active_job(2, 20, "token-2", "SERIAL-2")
        db.delete_active_job(1)

        jobs = db.get_active_jobs()
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == 2

    def test_delete_nonexistent_noop(self, db):
        """delete 不存在的 job 不抛异常."""
        db.delete_active_job(999)
        assert db.get_active_jobs() == []

    def test_connections_are_thread_local(self, db):
        """不同线程应拿到不同 SQLite connection，避免跨线程共享单连接。"""
        main_conn = db._conn
        worker_conn = []

        def worker():
            db.save_active_job(3, 30, "token-3", "SERIAL-3")
            worker_conn.append(db._conn)

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        assert worker_conn
        assert worker_conn[0] is not main_conn
        jobs = db.get_active_jobs()
        assert any(job["job_id"] == 3 for job in jobs)


class TestTerminalOutboxImmutability:
    def test_same_payload_is_idempotent(self, db):
        payload = {
            "update": {"status": "COMPLETED", "exit_code": 0},
            "fencing_token": "10:1",
        }
        first_id = db.enqueue_terminal(1, payload)
        assert db.enqueue_terminal(1, dict(payload)) == first_id

    def test_conflicting_payload_cannot_replace_first_fact(self, db):
        first = {
            "update": {"status": "COMPLETED", "exit_code": 0},
            "fencing_token": "10:1",
        }
        db.enqueue_terminal(1, first)

        with pytest.raises(ValueError, match="conflicting terminal payload"):
            db.enqueue_terminal(
                1,
                {
                    "update": {"status": "FAILED", "exit_code": 1},
                    "fencing_token": "10:1",
                },
            )

        assert db.get_pending_terminals()[0]["payload"] == first


class TestPendingOutbox:
    def test_get_pending_outbox_returns_unacked(self, db):
        """get_pending_outbox 返回 acked=0 的 terminal outbox 条目."""
        now = datetime.now(timezone.utc).isoformat()
        # Seed terminal outbox entries via enqueue (using internal table)
        db._conn.execute(
            "INSERT INTO job_terminal_outbox (job_id, payload, created_at, acked) VALUES (?, ?, ?, ?)",
            (1, json.dumps({"update": {"status": "FAILED"}}), now, 0),
        )
        db._conn.execute(
            "INSERT INTO job_terminal_outbox (job_id, payload, created_at, acked) VALUES (?, ?, ?, ?)",
            (2, json.dumps({"update": {"status": "COMPLETED"}}), now, 1),
        )
        db._conn.commit()

        outbox = db.get_pending_outbox()
        assert len(outbox) == 1  # only acked=0
        assert outbox[0]["job_id"] == 1
        assert outbox[0]["event_type"] == "FAILED"

    def test_get_pending_outbox_empty(self, db):
        """无未确认 outbox → 返回空列表."""
        assert db.get_pending_outbox() == []
