"""LocalDB log_signal_outbox + watcher_state 单元测试。

覆盖：
  - log_signal_outbox：enqueue 幂等、seq_no 恢复、pending/ack/bump/prune
  - watcher_state：upsert + 增量 update + list_active + bump_last_seq
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from backend.agent.registry.local_db import LocalDB


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    db = LocalDB()
    db.initialize(str(tmp_path / "agent.db"))
    yield db
    db.close()


def _make_envelope(job_id: int, seq_no: int, **overrides):
    base = {
        "job_id":         job_id,
        "seq_no":         seq_no,
        "host_id":        "host-test",
        "device_serial":  "SERIAL-1",
        "category":       "ANR",
        "source":         "inotifyd",
        "path_on_device": f"/data/anr/trace_{seq_no:03d}.txt",
        "detected_at":    datetime.now(timezone.utc).isoformat(),
    }
    base.update(overrides)
    return base


# ----------------------------------------------------------------------
# log_signal_outbox
# ----------------------------------------------------------------------

def test_next_seq_empty_starts_at_one(db):
    assert db.next_log_signal_seq_no(job_id=1) == 1


def test_enqueue_creates_row_returns_id(db):
    rid = db.enqueue_log_signal(1, 1, _make_envelope(1, 1))
    assert rid is not None and rid > 0


def test_enqueue_idempotent_on_conflict(db):
    first = db.enqueue_log_signal(1, 1, _make_envelope(1, 1))
    second = db.enqueue_log_signal(1, 1, _make_envelope(1, 1))
    assert first is not None
    assert second is None, "冲突的 (job_id, seq_no) 应返回 None"


def test_next_seq_after_enqueue_returns_max_plus_one(db):
    db.enqueue_log_signal(1, 1, _make_envelope(1, 1))
    db.enqueue_log_signal(1, 5, _make_envelope(1, 5))
    assert db.next_log_signal_seq_no(1) == 6
    # 跨 job 独立计数
    assert db.next_log_signal_seq_no(2) == 1


def test_get_pending_returns_all_unacked_in_order(db):
    db.enqueue_log_signal(1, 1, _make_envelope(1, 1))
    db.enqueue_log_signal(1, 2, _make_envelope(1, 2))
    db.enqueue_log_signal(2, 1, _make_envelope(2, 1, device_serial="SERIAL-2"))

    rows = db.get_pending_log_signals(limit=10)
    assert len(rows) == 3
    assert [r["seq_no"] for r in rows[:2]] == [1, 2]
    assert rows[0]["envelope"]["device_serial"] == "SERIAL-1"
    assert rows[2]["envelope"]["device_serial"] == "SERIAL-2"


def test_get_pending_respects_limit(db):
    for i in range(1, 6):
        db.enqueue_log_signal(1, i, _make_envelope(1, i))
    rows = db.get_pending_log_signals(limit=2)
    assert len(rows) == 2


def test_ack_hides_from_pending(db):
    rid = db.enqueue_log_signal(1, 1, _make_envelope(1, 1))
    db.enqueue_log_signal(1, 2, _make_envelope(1, 2))
    db.ack_log_signal(rid)
    rows = db.get_pending_log_signals()
    assert len(rows) == 1
    assert rows[0]["seq_no"] == 2


def test_bump_attempt_increments_and_sets_error(db):
    rid = db.enqueue_log_signal(1, 1, _make_envelope(1, 1))
    db.bump_log_signal_attempt(rid, "connection refused")
    db.bump_log_signal_attempt(rid, "timeout")
    rows = db.get_pending_log_signals()
    assert rows[0]["attempts"] == 2


def test_prune_keeps_recent_acked(db):
    ids = []
    for i in range(1, 6):
        rid = db.enqueue_log_signal(1, i, _make_envelope(1, i))
        db.ack_log_signal(rid)
        ids.append(rid)
    # 保留最近 2 条，删除 3 条
    deleted = db.prune_acked_log_signals(keep_recent=2)
    assert deleted == 3


# ----------------------------------------------------------------------
# watcher_state
# ----------------------------------------------------------------------

def test_upsert_watcher_state_insert(db):
    db.upsert_watcher_state(
        watcher_id="wch-001",
        job_id=101,
        serial="SERIAL-1",
        host_id="host-test",
        state="active",
        capability="inotifyd_root",
    )
    row = db.get_watcher_state("wch-001")
    assert row is not None
    assert row["state"] == "active"
    assert row["capability"] == "inotifyd_root"
    assert row["last_seq_no"] == 0


def test_upsert_watcher_state_update_on_conflict(db):
    db.upsert_watcher_state(
        watcher_id="wch-001", job_id=101, serial="S1", host_id="h",
        state="active", capability="stub",
    )
    db.upsert_watcher_state(
        watcher_id="wch-001", job_id=101, serial="S1", host_id="h",
        state="stopped", capability="inotifyd_shell",
    )
    row = db.get_watcher_state("wch-001")
    assert row["state"] == "stopped"
    assert row["capability"] == "inotifyd_shell"


def test_update_watcher_state_partial(db):
    db.upsert_watcher_state(
        watcher_id="wch-001", job_id=1, serial="S", host_id="h",
        state="active", capability="stub",
    )
    db.update_watcher_state("wch-001", state="stopped", last_error="adb disconnect")
    row = db.get_watcher_state("wch-001")
    assert row["state"] == "stopped"
    assert row["capability"] == "stub", "未指定字段应保持不变"
    assert row["last_error"] == "adb disconnect"


def test_bump_watcher_last_seq_monotonic(db):
    db.upsert_watcher_state(
        watcher_id="wch-001", job_id=1, serial="S", host_id="h", state="active",
    )
    db.bump_watcher_last_seq("wch-001", 5)
    assert db.get_watcher_state("wch-001")["last_seq_no"] == 5
    db.bump_watcher_last_seq("wch-001", 3)  # 倒退不应生效
    assert db.get_watcher_state("wch-001")["last_seq_no"] == 5
    db.bump_watcher_last_seq("wch-001", 10)
    assert db.get_watcher_state("wch-001")["last_seq_no"] == 10


def test_list_active_watcher_states(db):
    db.upsert_watcher_state(
        watcher_id="wch-a", job_id=1, serial="S1", host_id="h", state="active",
    )
    db.upsert_watcher_state(
        watcher_id="wch-b", job_id=2, serial="S2", host_id="h", state="stopped",
    )
    db.upsert_watcher_state(
        watcher_id="wch-c", job_id=3, serial="S3", host_id="h", state="active",
    )
    active = db.list_active_watcher_states()
    ids = {w["watcher_id"] for w in active}
    assert ids == {"wch-a", "wch-c"}
