"""SignalEmitter + OutboxDrainer 单元测试。

覆盖：
  - SignalEmitter: seq_no 单调、跨实例恢复、envelope 校验、并发安全
  - OutboxDrainer: 空批次、成功批量 ack、失败批量 bump、后台线程启停
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from backend.agent.registry.local_db import LocalDB
from backend.agent.watcher.contracts import ContractViolation
from backend.agent.watcher.emitter import OutboxDrainer, SignalEmitter


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    db = LocalDB()
    db.initialize(str(tmp_path / "agent.db"))
    yield db
    db.close()


@pytest.fixture
def emitter(db):
    return SignalEmitter(
        local_db=db, job_id=101, host_id="host-test", device_serial="SERIAL-1",
    )


@pytest.fixture(autouse=True)
def reset_drainer():
    """每个测试前后重置单例，避免状态泄漏。"""
    OutboxDrainer._reset_for_tests()
    yield
    OutboxDrainer._reset_for_tests()


# ----------------------------------------------------------------------
# SignalEmitter
# ----------------------------------------------------------------------

def test_emit_assigns_monotonic_seq_no(emitter):
    s1 = emitter.emit(category="ANR", source="inotifyd", path_on_device="/data/anr/a")
    s2 = emitter.emit(category="ANR", source="inotifyd", path_on_device="/data/anr/b")
    s3 = emitter.emit(category="AEE", source="inotifyd", path_on_device="/data/aee_exp/x")
    assert (s1, s2, s3) == (1, 2, 3)


def test_emit_persists_to_outbox(db, emitter):
    emitter.emit(
        category="ANR",
        source="inotifyd",
        path_on_device="/data/anr/trace.txt",
        extra={"pid": 1234},
    )
    rows = db.get_pending_log_signals()
    assert len(rows) == 1
    env = rows[0]["envelope"]
    assert env["job_id"] == 101
    assert env["seq_no"] == 1
    assert env["category"] == "ANR"
    assert env["extra"] == {"pid": 1234}


def test_emit_resumes_seq_after_restart(db):
    """Agent 重启模拟：新建 emitter 必须从 LocalDB MAX(seq_no)+1 继续。"""
    e1 = SignalEmitter(local_db=db, job_id=101, host_id="h", device_serial="S")
    e1.emit(category="ANR", source="inotifyd", path_on_device="/a")
    e1.emit(category="ANR", source="inotifyd", path_on_device="/b")

    # 模拟 Agent 重启：创建第二个 emitter 实例
    e2 = SignalEmitter(local_db=db, job_id=101, host_id="h", device_serial="S")
    seq = e2.emit(category="ANR", source="inotifyd", path_on_device="/c")
    assert seq == 3, "恢复后应从 3 继续（不是 1，避免冲突）"


def test_emit_rejects_bad_category(emitter):
    with pytest.raises(ContractViolation):
        emitter.emit(category="INVALID_CAT", source="inotifyd", path_on_device="/x")


def test_emit_rejects_bad_source(emitter):
    with pytest.raises(ContractViolation):
        emitter.emit(category="ANR", source="some_bad_src", path_on_device="/x")


def test_emit_concurrent_unique_seq_no(db):
    """50 线程并发 emit，seq_no 必须全局唯一（无重复、无空洞）。"""
    emitter = SignalEmitter(local_db=db, job_id=200, host_id="h", device_serial="S")
    results: List[int] = []
    lock = threading.Lock()

    def worker():
        seq = emitter.emit(category="ANR", source="inotifyd", path_on_device="/x")
        with lock:
            results.append(seq)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert len(results) == 50
    assert len(set(results)) == 50, "seq_no 不允许重复"
    assert sorted(results) == list(range(1, 51)), "应为连续 [1..50]"


# ----------------------------------------------------------------------
# OutboxDrainer — tick_once
# ----------------------------------------------------------------------

def _make_mock_session(status_code: int = 200):
    """构造 mock requests.Session；返回指定 status_code 的 Response。"""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    if status_code >= 400:
        from requests.exceptions import HTTPError
        mock_resp.raise_for_status.side_effect = HTTPError(f"HTTP {status_code}")
    else:
        mock_resp.raise_for_status.return_value = None

    mock_session = MagicMock()
    mock_session.post.return_value = mock_resp
    return mock_session, mock_resp


def test_drainer_tick_empty_returns_zero(db):
    drainer = OutboxDrainer.instance().configure(
        local_db=db, api_url="http://fake", agent_secret="",
    )
    assert drainer.tick_once() == 0


def test_drainer_tick_success_acks_all(db, emitter):
    emitter.emit(category="ANR", source="inotifyd", path_on_device="/a")
    emitter.emit(category="AEE", source="inotifyd", path_on_device="/b")
    assert len(db.get_pending_log_signals()) == 2

    session, _ = _make_mock_session(status_code=200)
    drainer = OutboxDrainer.instance().configure(
        local_db=db, api_url="http://fake:8000",
        agent_secret="test-secret", session=session,
    )
    flushed = drainer.tick_once()

    assert flushed == 2
    assert len(db.get_pending_log_signals()) == 0, "成功后应全部 ack"
    # 校验 POST 请求 shape
    call = session.post.call_args
    assert call.args[0].endswith("/api/v1/agent/log-signals")
    assert "X-Agent-Secret" in call.kwargs["headers"]
    assert call.kwargs["headers"]["X-Agent-Secret"] == "test-secret"


def test_drainer_tick_post_failure_bumps_attempts(db, emitter):
    emitter.emit(category="ANR", source="inotifyd", path_on_device="/a")
    emitter.emit(category="ANR", source="inotifyd", path_on_device="/b")

    session, _ = _make_mock_session(status_code=500)
    drainer = OutboxDrainer.instance().configure(
        local_db=db, api_url="http://fake", agent_secret="", session=session,
    )
    flushed = drainer.tick_once()

    assert flushed == 0, "失败时不计入 flushed"
    pending = db.get_pending_log_signals()
    assert len(pending) == 2, "失败后条目仍 pending"
    assert all(row["attempts"] == 1 for row in pending)

    # 再次 tick 继续 bump
    drainer.tick_once()
    pending = db.get_pending_log_signals()
    assert all(row["attempts"] == 2 for row in pending)


def test_drainer_tick_network_exception_bumps_attempts(db, emitter):
    emitter.emit(category="ANR", source="inotifyd", path_on_device="/a")

    session = MagicMock()
    session.post.side_effect = ConnectionError("DNS failure")
    drainer = OutboxDrainer.instance().configure(
        local_db=db, api_url="http://fake", agent_secret="", session=session,
    )
    flushed = drainer.tick_once()

    assert flushed == 0
    pending = db.get_pending_log_signals()
    assert pending[0]["attempts"] == 1


def test_drainer_respects_batch_size(db, emitter):
    for _ in range(10):
        emitter.emit(category="ANR", source="inotifyd", path_on_device="/x")
    session, _ = _make_mock_session(status_code=200)
    drainer = OutboxDrainer.instance().configure(
        local_db=db, api_url="http://fake", agent_secret="",
        session=session, batch_size=3,
    )
    flushed = drainer.tick_once()
    assert flushed == 3, "单次 tick 只刷出 batch_size 条"
    assert len(db.get_pending_log_signals()) == 7


# ----------------------------------------------------------------------
# OutboxDrainer — 后台线程生命周期
# ----------------------------------------------------------------------

def test_drainer_start_stop_lifecycle(db, emitter):
    emitter.emit(category="ANR", source="inotifyd", path_on_device="/a")
    session, _ = _make_mock_session(status_code=200)
    drainer = OutboxDrainer.instance().configure(
        local_db=db, api_url="http://fake", agent_secret="",
        session=session, interval_seconds=0.1,
    )
    drainer.start()
    # 等后台线程至少跑一轮
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if len(db.get_pending_log_signals()) == 0:
            break
        time.sleep(0.05)
    drainer.stop(timeout=2.0)

    assert len(db.get_pending_log_signals()) == 0


def test_drainer_start_without_configure_raises():
    drainer = OutboxDrainer.instance()
    with pytest.raises(RuntimeError, match="not configured"):
        drainer.start()


def test_drainer_stop_idempotent(db):
    drainer = OutboxDrainer.instance().configure(
        local_db=db, api_url="http://fake", agent_secret="",
    )
    drainer.start()
    drainer.stop(timeout=1.0)
    drainer.stop(timeout=1.0)  # 重复 stop 不应抛


# ----------------------------------------------------------------------
# OutboxDrainer — prune 闭环（防止 SQLite 无限增长）
# ----------------------------------------------------------------------

def test_drainer_prunes_after_threshold_ticks(db, emitter):
    """成功 tick 达到 prune_every_n_ticks 后触发 prune_acked_log_signals。"""
    # emit 10 条；一次 tick 全部 ack；prune_every=1 立刻触发；keep_recent=3 → 剩 3 条
    for _ in range(10):
        emitter.emit(category="ANR", source="inotifyd", path_on_device="/a")

    session, _ = _make_mock_session(status_code=200)
    drainer = OutboxDrainer.instance().configure(
        local_db=db, api_url="http://fake", agent_secret="",
        session=session, batch_size=10,
        prune_every_n_ticks=1, prune_keep_recent=3,
    )
    flushed = drainer.tick_once()
    assert flushed == 10

    # pending 为 0（全部已 ack）
    assert len(db.get_pending_log_signals()) == 0
    # 表中应只保留最近 3 条已 acked 行，其余被 prune 删除
    with db._lock:
        cnt = db._conn.execute(
            "SELECT COUNT(*) AS c FROM log_signal_outbox"
        ).fetchone()["c"]
    assert cnt == 3, f"prune 后应只保留 3 条（keep_recent），实际 {cnt} 条"


def test_drainer_prune_skipped_on_failed_tick(db, emitter):
    """HTTP 失败时不应计入 ticks_since_prune（未成功 ack 的条目不该被 prune）。"""
    emitter.emit(category="ANR", source="inotifyd", path_on_device="/a")

    session, _ = _make_mock_session(status_code=500)
    drainer = OutboxDrainer.instance().configure(
        local_db=db, api_url="http://fake", agent_secret="",
        session=session, prune_every_n_ticks=1,
    )
    # 失败的 tick 不会进入 prune 分支（return 在 except 中）
    drainer.tick_once()
    # 条目仍 pending，显然不会被误删
    assert len(db.get_pending_log_signals()) == 1
