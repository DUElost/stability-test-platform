# -*- coding: utf-8 -*-
"""#762：终态 outbox 死信上限 + 队头饿死修复（真实 LocalDB）。

覆盖：
- schema 增列 dead_letter 的 idempotent ALTER（旧库兼容）
- bump_terminal_attempt 返回新 attempts（死信判定口径）
- 死信行不再被 get_pending_terminals 取出（不占队头饿死新终态）
- 连续永久失败达 _MAX_TERMINAL_ATTEMPTS → 自动死信 + 指标
- 死信行仍保留（审计 + #1005 has_terminal_fact 终态证据）
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from requests import Response

from backend.agent.outbox_drainer import OutboxDrainThread
from backend.agent.registry.local_db import LocalDB


@pytest.fixture
def db(tmp_path):
    db = LocalDB()
    db.initialize(str(tmp_path / "agent.db"))
    yield db
    db.close()


def _conflict_response() -> Response:
    response = Response()
    response.status_code = 409
    response._content = json.dumps({
        "detail": {
            "code": "TERMINAL_PAYLOAD_CONFLICT",
            "current_status": "COMPLETED",
        }
    }).encode("utf-8")
    response.url = "http://127.0.0.1:8000/api/v1/agent/jobs/1/complete"
    return response


def test_terminal_outbox_schema_includes_dead_letter(db):
    columns = {
        row["name"]
        for row in db._conn.execute(
            "PRAGMA table_info(job_terminal_outbox)"
        ).fetchall()
    }
    assert "dead_letter" in columns


def test_ensure_terminal_outbox_schema_is_idempotent(db):
    db._ensure_terminal_outbox_schema()
    db._ensure_terminal_outbox_schema()  # 二次调用不得抛错


def test_bump_terminal_attempt_returns_new_count(db):
    db.enqueue_terminal(7, {"update": {"status": "FAILED"}})

    assert db.bump_terminal_attempt(7, "err-1") == 1
    assert db.bump_terminal_attempt(7, "err-2") == 2


def test_dead_letter_excludes_from_pending_and_does_not_starve(db):
    db.enqueue_terminal(1, {"update": {"status": "FAILED"}})   # 最旧（原会占队头）
    db.enqueue_terminal(2, {"update": {"status": "COMPLETED"}})

    db.mark_terminal_dead_letter(1, "permanent conflict")

    pending = db.get_pending_terminals(limit=20)
    assert [row["job_id"] for row in pending] == [2], "死信行不得占用 batch 窗口"
    assert db.count_terminal_dead_letters() == 1
    assert [row["job_id"] for row in db.get_terminal_dead_letters()] == [1]


def test_has_terminal_fact_survives_dead_letter(db):
    """#1005：死信行仍是「终态事实已持久化」的证据，不得被当作无事实。"""
    db.enqueue_terminal(9, {"update": {"status": "FAILED"}})
    db.mark_terminal_dead_letter(9, "permanent conflict")

    assert db.has_terminal_fact(9) is True


def test_prune_acked_terminals_keeps_dead_letters(db):
    db.enqueue_terminal(9, {"update": {"status": "FAILED"}})
    db.mark_terminal_dead_letter(9, "permanent conflict")

    db.prune_acked_terminals(keep_recent=0)

    assert db.count_terminal_dead_letters() == 1


def test_drainer_dead_letters_after_max_attempts_and_unblocks_queue(db, monkeypatch):
    """连续 TERMINAL_PAYLOAD_CONFLICT 达上限 → 死信；新终态行随后可正常 ack。"""
    monkeypatch.setattr(OutboxDrainThread, "_MAX_TERMINAL_ATTEMPTS", 3)
    db.enqueue_terminal(1, {"update": {"status": "FAILED"}})
    drainer = OutboxDrainThread("http://127.0.0.1:8000", db, interval=15.0)

    response = _conflict_response()
    for _ in range(3):
        with patch(
            "backend.agent.outbox_drainer.requests.post", return_value=response,
        ):
            assert drainer._drain_once() == 0

    assert db.count_terminal_dead_letters() == 1
    assert db.get_pending_terminals(limit=20) == []
    assert drainer.snapshot_metrics()["dead_letter_total"] == 1

    # 队头不再被卡死：新终态行可被正常取到并 ack
    db.enqueue_terminal(2, {"update": {"status": "COMPLETED"}})
    ok = Response()
    ok.status_code = 200
    ok._content = b'{"ok":true}'
    ok.url = "http://127.0.0.1:8000/api/v1/agent/jobs/2/complete"
    with patch("backend.agent.outbox_drainer.requests.post", return_value=ok):
        assert drainer._drain_once() == 1
    assert db.get_pending_terminals(limit=20) == []
