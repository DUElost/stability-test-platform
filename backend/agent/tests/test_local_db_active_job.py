"""LocalDB active_job_registry + get_pending_outbox unit tests (ADR-0019 Phase 3a)."""

from __future__ import annotations

import json
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
        db.save_active_job(1, 10, "token-1")
        db.save_active_job(2, 20, "token-2")

        jobs = db.get_active_jobs()
        assert len(jobs) == 2
        assert jobs[0]["job_id"] in (1, 2)
        assert jobs[0]["device_id"] in (10, 20)
        assert jobs[0]["fencing_token"] in ("token-1", "token-2")

    def test_save_replace_updates(self, db):
        """同一 job_id 再次 save → 覆盖（INSERT OR REPLACE）."""
        db.save_active_job(1, 10, "old-token")
        db.save_active_job(1, 20, "new-token")

        jobs = db.get_active_jobs()
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == 1
        assert jobs[0]["device_id"] == 20
        assert jobs[0]["fencing_token"] == "new-token"

    def test_delete_active_job(self, db):
        """delete 后 get 不再返回该 job."""
        db.save_active_job(1, 10, "token-1")
        db.save_active_job(2, 20, "token-2")
        db.delete_active_job(1)

        jobs = db.get_active_jobs()
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == 2

    def test_delete_nonexistent_noop(self, db):
        """delete 不存在的 job 不抛异常."""
        db.delete_active_job(999)
        assert db.get_active_jobs() == []


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
