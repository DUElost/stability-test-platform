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


# ── #1551：408/429 属瞬时故障，不得进死信 ─────────────────────────────────────


def _status_response(status_code: int, *, retry_after: str | None = None) -> Response:
    response = Response()
    response.status_code = status_code
    response._content = b'{"detail": "transient"}'
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    response.url = "http://127.0.0.1:8000/api/v1/agent/jobs/1/complete"
    return response


@pytest.mark.parametrize("status_code", [408, 429])
def test_transient_http_status_never_dead_letters(db, monkeypatch, status_code):
    """#1551：408/429 反复失败也不得转死信。

    job_terminal_outbox 是同族三表（terminal / log_signal / dle_register）里
    **唯一没有** replay_*_dead_letter 出口的，转死信即永久丢失终态事实；而 429
    在本平台确实可达——RateLimitMiddleware 已把 /api/v1/agent/jobs/ 移出豁免
    清单（300 req/min/IP），终态上送端点正在该前缀下。原实现把它们归进
    「非 409/404 的 4xx = 中心永久拒绝」，约 150s（10 × 15s）即可打掉一条终态。
    """
    monkeypatch.setattr(OutboxDrainThread, "_MAX_TERMINAL_ATTEMPTS", 3)
    db.enqueue_terminal(1, {"update": {"status": "FAILED"}})
    drainer = OutboxDrainThread("http://127.0.0.1:8000", db, interval=15.0)

    response = _status_response(status_code, retry_after=None)
    for _ in range(10):
        with patch("backend.agent.outbox_drainer.requests.post", return_value=response):
            drainer._drain_once()

    assert db.count_terminal_dead_letters() == 0, "瞬时故障不得进死信"
    assert [row["job_id"] for row in db.get_pending_terminals(limit=20)] == [1]


def test_permanent_4xx_still_dead_letters(db, monkeypatch):
    """对照：#762 的语义不变——真·永久拒绝（如 403）仍按上限转死信。"""
    monkeypatch.setattr(OutboxDrainThread, "_MAX_TERMINAL_ATTEMPTS", 3)
    db.enqueue_terminal(1, {"update": {"status": "FAILED"}})
    drainer = OutboxDrainThread("http://127.0.0.1:8000", db, interval=15.0)

    response = _status_response(403)
    for _ in range(3):
        with patch("backend.agent.outbox_drainer.requests.post", return_value=response):
            drainer._drain_once()

    assert db.count_terminal_dead_letters() == 1


def test_retry_after_window_suppresses_retry_then_resumes(db):
    """#1551：429 带 Retry-After 时按它退避，窗口内不再打中心。"""
    import time

    db.enqueue_terminal(1, {"update": {"status": "FAILED"}})
    drainer = OutboxDrainThread("http://127.0.0.1:8000", db, interval=15.0)
    response = _status_response(429, retry_after="120")

    with patch(
        "backend.agent.outbox_drainer.requests.post", return_value=response,
    ) as post:
        drainer._drain_once()
        assert post.call_count == 1
        assert drainer._defer_until[1] > time.monotonic(), "未记录退避时刻"

        drainer._drain_once()
        assert post.call_count == 1, "Retry-After 窗口内不得重试"

        drainer._defer_until[1] = time.monotonic() - 1   # 窗口过去
        drainer._drain_once()
        assert post.call_count == 2, "窗口过后必须恢复重试"


def test_deferral_pruned_when_row_consumed_elsewhere(db):
    """#2036：延期中的行被其它路径 ack 后，条目必须在下一轮 drain 出表。

    反例（旧行为）：条目只在「延期到期且再次被扫到」时才 pop，被其它路径消费
    的行其条目永久残留 → 随被限流过的 job_id 单调增长。
    """
    db.enqueue_terminal(1, {"update": {"status": "FAILED"}})
    db.enqueue_terminal(2, {"update": {"status": "FAILED"}})
    drainer = OutboxDrainThread("http://127.0.0.1:8000", db, interval=15.0)
    response = _status_response(429, retry_after="120")

    with patch("backend.agent.outbox_drainer.requests.post", return_value=response):
        drainer._drain_once()
    assert set(drainer._defer_until) == {1, 2}

    db.ack_terminal(1)  # 其它路径消费了 job 1（recovery/sync 等）

    with patch("backend.agent.outbox_drainer.requests.post", return_value=response):
        drainer._drain_once()

    assert 1 not in drainer._defer_until, "行已不在待办集合 → 条目必须出表"
    assert 2 in drainer._defer_until, "仍在待办集合的行不得被误裁"


def test_deferral_not_pruned_when_page_is_truncated(db):
    """守卫：裁剪必须用**不分页**的全量 id 集合。

    `get_pending_terminals` 只取前 20 行——若拿这一页做差集，落在第 20 行之后
    的延期条目会被误判为「已消失」而删除，退避失效（Retry-After 被自己的 15s
    节奏续上）。本用例把 job_id=25 的条目放在 20 行窗口之外。
    """
    import time

    for job_id in range(1, 26):
        db.enqueue_terminal(job_id, {"update": {"status": "FAILED"}})
    drainer = OutboxDrainThread("http://127.0.0.1:8000", db, interval=15.0)
    drainer._defer_until[25] = time.monotonic() + 120

    with patch(
        "backend.agent.outbox_drainer.requests.post",
        return_value=_status_response(429, retry_after="1"),
    ):
        drainer._drain_once()

    assert 25 in drainer._defer_until, "窗口外的仍待办行不得被分页差集误裁"


def test_retry_after_capped(db):
    """异常巨大的 Retry-After 不得把某行钉死（退避有上限）。"""
    import time

    db.enqueue_terminal(1, {"update": {"status": "FAILED"}})
    drainer = OutboxDrainThread("http://127.0.0.1:8000", db, interval=15.0)
    response = _status_response(429, retry_after="999999")

    with patch("backend.agent.outbox_drainer.requests.post", return_value=response):
        drainer._drain_once()

    assert drainer._defer_until[1] <= (
        time.monotonic() + OutboxDrainThread._MAX_RETRY_AFTER_SECONDS
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 0.0),            # 头缺失
        ("", 0.0),
        ("abc", 0.0),           # 非数字
        ("0", 0.0),             # 无意义
        ("-5", 0.0),
        ("30", 30.0),
        (" 12.5 ", 12.5),
        ("Wed, 21 Oct 2026 07:28:00 GMT", 0.0),   # HTTP-date 形态刻意忽略
    ],
)
def test_parse_retry_after(raw, expected):
    response = Response()
    response.status_code = 429
    if raw is not None:
        response.headers["Retry-After"] = raw
    assert OutboxDrainThread._parse_retry_after(response) == expected


# ── #764：404 的两种语义必须区分（job not found vs 未知路由/部署错位）──────────


def _not_found_response(*, body: bytes | None = None) -> Response:
    response = Response()
    response.status_code = 404
    response._content = body if body is not None else json.dumps(
        {"detail": "job not found"}
    ).encode("utf-8")
    response.url = "http://127.0.0.1:8000/api/v1/agent/jobs/1/complete"
    return response


def test_404_job_not_found_still_acks(db):
    """中心明确 job not found（/complete 的 detail 字符串）→ ack，语义不变。"""
    db.enqueue_terminal(1, {"update": {"status": "FAILED"}})
    drainer = OutboxDrainThread("http://127.0.0.1:8000", db, interval=15.0)

    with patch(
        "backend.agent.outbox_drainer.requests.post",
        return_value=_not_found_response(),
    ):
        # sent 只计真实上送成功；job-gone 的 ack 走既有语义（不计 sent）
        assert drainer._drain_once() == 0

    assert db.get_pending_terminals(limit=20) == []
    assert db.count_terminal_dead_letters() == 0


def test_404_unknown_route_is_retained_not_acked(db):
    """#764：未知路由 404（部署错位）不得 ack——终态事实留在 outbox。

    旧行为对任何 404 无条件 ack_terminal → 静默丢弃终态事实（仅一条 WARNING）。
    """
    db.enqueue_terminal(1, {"update": {"status": "FAILED"}})
    drainer = OutboxDrainThread("http://127.0.0.1:8000", db, interval=15.0)
    body = json.dumps({"detail": "Not Found"}).encode("utf-8")

    with patch(
        "backend.agent.outbox_drainer.requests.post",
        return_value=_not_found_response(body=body),
    ):
        assert drainer._drain_once() == 0

    assert [row["job_id"] for row in db.get_pending_terminals(limit=20)] == [1]
    assert drainer.snapshot_metrics()["conflicts_retained_total"] == 1


def test_404_unstructured_follows_dead_letter_cap(db, monkeypatch):
    """未知路由 404 持续存在 → 与 409 unstructured 同策略：达上限转死信（不静默丢失）。"""
    monkeypatch.setattr(OutboxDrainThread, "_MAX_TERMINAL_ATTEMPTS", 3)
    db.enqueue_terminal(1, {"update": {"status": "FAILED"}})
    drainer = OutboxDrainThread("http://127.0.0.1:8000", db, interval=15.0)
    body = json.dumps({"detail": "Not Found"}).encode("utf-8")

    for _ in range(3):
        with patch(
            "backend.agent.outbox_drainer.requests.post",
            return_value=_not_found_response(body=body),
        ):
            drainer._drain_once()

    assert db.count_terminal_dead_letters() == 1
    assert db.get_pending_terminals(limit=20) == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b'{"detail": "job not found"}', True),
        (b'{"detail": "Job Not Found"}', True),      # 大小写/空白容忍
        (b'{"detail": {"code": "JOB_NOT_FOUND"}}', True),
        (b'{"detail": {"code": "JOB_GONE", "message": "x"}}', True),
        (b'{"detail": {"message": "job not found"}}', True),
        (b'{"error": {"code": "JOB_NOT_FOUND"}}', True),
        (b'{"detail": "Not Found"}', False),         # FastAPI 未知路由
        (b'{"detail": {"code": "TERMINAL_PAYLOAD_CONFLICT"}}', False),
        (b'{"detail": "something else"}', False),
        (b'{"unexpected": 1}', False),
        (b"<html>proxy error</html>", False),        # 非 JSON body
        (b'["not", "a", "dict"]', False),
    ],
)
def test_is_job_not_found_body_matrix(raw, expected):
    response = Response()
    response.status_code = 404
    response._content = raw
    assert OutboxDrainThread._is_job_not_found(response) is expected


def test_is_job_not_found_none_response():
    assert OutboxDrainThread._is_job_not_found(None) is False
