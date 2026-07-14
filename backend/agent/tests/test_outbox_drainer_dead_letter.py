"""#9 — log_signal_outbox 死信 + 健壮性测试.

覆盖:
- log_signal_outbox schema 增列 dead_letter 的 idempotent ALTER
- bump_log_signal_attempt 返回新值
- mark_log_signal_dead_letter 后 get_pending_log_signals 过滤
- get_log_signal_dead_letters 审计读取
- prune_acked_log_signals 排除 dead_letter
- OutboxDrainer 整批失败超 _MAX_ATTEMPTS 自动死信
- 死信行不阻塞新条目
- snapshot_metrics 形态(flushed_total / failed_total / dead_letter_total / pruned_total)
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from requests.exceptions import HTTPError

from backend.agent.registry.local_db import LocalDB
from backend.agent.watcher.emitter import OutboxDrainer, SignalEmitter


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    db = LocalDB()
    db.initialize(str(tmp_path / "agent.db"))
    yield db
    db.close()


@pytest.fixture
def emitter(db):
    return SignalEmitter(
        local_db=db,
        job_id=101,
        host_id="host-test",
        device_serial="SERIAL-1",
        fencing_token="42:1",
        agent_instance_id="agent-test",
    )


@pytest.fixture(autouse=True)
def reset_drainer():
    OutboxDrainer._reset_for_tests()
    yield
    OutboxDrainer._reset_for_tests()


def _make_500_session():
    resp = MagicMock()
    resp.status_code = 500
    resp.raise_for_status.side_effect = HTTPError("HTTP 500")
    session = MagicMock()
    session.post.return_value = resp
    return session


def _make_200_session():
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    session = MagicMock()
    session.post.return_value = resp
    return session


# ── LocalDB schema + helpers ────────────────────────────────────────────


def test_log_signal_outbox_schema_includes_dead_letter(db):
    cols = {
        row["name"]
        for row in db._conn.execute(
            "PRAGMA table_info(log_signal_outbox)"
        ).fetchall()
    }
    assert "dead_letter" in cols


def test_ensure_log_signal_outbox_schema_is_idempotent(db):
    db_path = db._db_path
    db.close()

    db2 = LocalDB()
    db2.initialize(db_path)
    try:
        cols = {
            row["name"]
            for row in db2._conn.execute(
                "PRAGMA table_info(log_signal_outbox)"
            ).fetchall()
        }
        assert "dead_letter" in cols
    finally:
        db2.close()


def test_bump_log_signal_attempt_returns_new_count(db, emitter):
    emitter.emit(category="ANR", source="inotifyd", path_on_device="/a")
    rid = db.get_pending_log_signals()[0]["id"]

    assert db.bump_log_signal_attempt(rid, "first") == 1
    assert db.bump_log_signal_attempt(rid, "second") == 2
    row = db._conn.execute(
        "SELECT attempts, last_error FROM log_signal_outbox WHERE id = ?",
        (rid,),
    ).fetchone()
    assert row["attempts"] == 2
    assert row["last_error"] == "second"


def test_mark_dead_letter_excludes_from_pending(db, emitter):
    emitter.emit(category="ANR", source="inotifyd", path_on_device="/a")
    emitter.emit(category="ANR", source="inotifyd", path_on_device="/b")

    rows = db.get_pending_log_signals()
    db.mark_log_signal_dead_letter(rows[0]["id"], "permanent")

    pending = db.get_pending_log_signals()
    assert [r["id"] for r in pending] == [rows[1]["id"]]

    dl = db.get_log_signal_dead_letters()
    assert [d["id"] for d in dl] == [rows[0]["id"]]
    assert dl[0]["last_error"] == "permanent"


def test_prune_acked_log_signals_skips_dead_letter(db, emitter):
    """prune 不删死信行；且 B3 seq_no guard 额外保留每个 job 的最高 acked-live 行。

    场景：同一 job 的 5 行全部 acked，最高行(rows[-1])再标 dead_letter。
      - 死信行(rows[-1])永不被 prune（dead_letter=1）→ 保留
      - B3 guard（commit 43665d9）保留 `acked=1 AND dead_letter=0` 中每 job 的
        MAX(id) 行，即 rows[-2] → 保留（防 prune 后 MAX(seq_no) 回退丢信号）
      - 其余 3 行(rows[0:3])被删
    故 deleted == 3（而非 B3 guard 引入前的朴素预期 4）。
    """
    # 5 个普通 acked，其中最高行再标 dead_letter
    for i in range(5):
        emitter.emit(category="ANR", source="inotifyd", path_on_device=f"/p{i}")
    rows = db.get_pending_log_signals()
    for r in rows:
        db.ack_log_signal(r["id"])
    # 把最高行同时标 dead_letter (人为构造边界条件)
    db.mark_log_signal_dead_letter(rows[-1]["id"], "perm")

    deleted = db.prune_acked_log_signals(keep_recent=0)
    assert deleted == 3, "3 行普通 acked 删除；死信行 + B3 max-seq guard 行各留 1"

    remaining = {row["id"] for row in db._conn.execute(
        "SELECT id FROM log_signal_outbox"
    ).fetchall()}
    # 死信行保留（核心：prune 跳过 dead_letter）
    assert rows[-1]["id"] in remaining
    # B3 guard 行保留（每 job 最高 acked-live 行，防 seq_no 回退）
    assert rows[-2]["id"] in remaining
    # 其余 3 行删除
    assert all(rows[i]["id"] not in remaining for i in range(3))


# ── OutboxDrainer 死信触发 ──────────────────────────────────────────────


def test_dead_letter_triggers_after_max_attempts(db, emitter):
    emitter.emit(category="ANR", source="inotifyd", path_on_device="/a")

    session = _make_500_session()
    drainer = OutboxDrainer.instance().configure(
        local_db=db, api_url="http://fake", agent_secret="", session=session,
    )
    drainer._MAX_ATTEMPTS = 3

    # 3 次失败 → 第 3 次后转死信
    for _ in range(3):
        drainer.tick_once()

    pending = db.get_pending_log_signals()
    assert pending == [], "死信后 pending 应空"
    dl = db.get_log_signal_dead_letters()
    assert len(dl) == 1
    assert dl[0]["attempts"] == 3

    metrics = drainer.snapshot_metrics()
    assert metrics["dead_letter_total"] == 1
    assert metrics["failed_total"] == 3


def test_dead_letter_does_not_block_new_signals(db, emitter):
    """已死信的 row 不再被取出,新 emit 的 signal 能正常 flush。"""
    emitter.emit(category="ANR", source="inotifyd", path_on_device="/a")

    session = _make_500_session()
    drainer = OutboxDrainer.instance().configure(
        local_db=db, api_url="http://fake", agent_secret="", session=session,
    )
    drainer._MAX_ATTEMPTS = 1
    drainer.tick_once()
    assert db.get_log_signal_dead_letters()  # 已进死信

    # 切到成功 session,再发新 signal
    drainer._session = _make_200_session()
    emitter.emit(category="AEE", source="inotifyd", path_on_device="/new")

    flushed = drainer.tick_once()
    assert flushed == 1, "新 signal 不该被死信阻塞"


def test_below_max_attempts_does_not_dead_letter(db, emitter):
    emitter.emit(category="ANR", source="inotifyd", path_on_device="/a")

    session = _make_500_session()
    drainer = OutboxDrainer.instance().configure(
        local_db=db, api_url="http://fake", agent_secret="", session=session,
    )
    drainer._MAX_ATTEMPTS = 5

    for _ in range(3):
        drainer.tick_once()

    pending = db.get_pending_log_signals()
    assert len(pending) == 1, "未到阈值不应死信,仍在 pending"
    assert pending[0]["attempts"] == 3
    assert drainer.snapshot_metrics()["dead_letter_total"] == 0


# ── snapshot_metrics ───────────────────────────────────────────────────


def test_snapshot_metrics_initial_state(db):
    drainer = OutboxDrainer.instance().configure(
        local_db=db, api_url="http://fake", agent_secret="",
    )
    assert drainer.snapshot_metrics() == {
        "flushed_total":     0,
        "failed_total":      0,
        "dead_letter_total": 0,
        "pruned_total":      0,
        "pending_backlog":   0,
    }


def test_snapshot_metrics_accumulates_flushed_and_pruned(db, emitter):
    for i in range(5):
        emitter.emit(category="ANR", source="inotifyd", path_on_device=f"/p{i}")

    session = _make_200_session()
    drainer = OutboxDrainer.instance().configure(
        local_db=db, api_url="http://fake", agent_secret="", session=session,
        prune_every_n_ticks=1, prune_keep_recent=2,
    )
    drainer.tick_once()

    metrics = drainer.snapshot_metrics()
    assert metrics["flushed_total"] == 5
    assert metrics["pruned_total"] == 3  # 5 ack - 2 keep = 3 删
    assert metrics["failed_total"] == 0
    assert metrics["dead_letter_total"] == 0
