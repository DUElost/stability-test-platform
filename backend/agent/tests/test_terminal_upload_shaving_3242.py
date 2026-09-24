# -*- coding: utf-8 -*-
"""#3242：Agent 终态上报削峰——首发单发 / 503·429 交 outbox / jitter / 每机并发上限。

现场（R523，2026-09-23）：490 个 RUNNING 同时回传终态，`/complete` 窗口内 **1644 次**
请求 = 490 个事实 × **3.35**——首发的线程内重试（1s/2s）把中心舱壁削掉的峰又打了回去。
本文件钉五条不变式：

1. 事实已落 outbox ⇒ **首发只打一次**（不线程内重试）；
2. outbox 入队失败（没有补送落点）⇒ 回退 `AGENT_POST_RETRIES`（此时多试两次比丢事实便宜）；
3. 同刻在飞的终态 POST ≤ `AGENT_TERMINAL_UPLOAD_CONCURRENCY`（默认 2）；
4. abort 族（ABORTED → 归一 CANCELED）首发前随机等 0–`AGENT_TERMINAL_ABORT_JITTER_SECONDS`；
5. drainer：**503 吃 `Retry-After`**、无头时用指数 + full jitter，且退避永不突破硬上限。
"""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock

import pytest
from requests import Response

from backend.agent import api_client
from backend.agent.outbox_drainer import OutboxDrainThread
from backend.agent.registry.local_db import LocalDB


@pytest.fixture
def db(tmp_path):
    local = LocalDB()
    local.initialize(str(tmp_path / "agent.db"))
    yield local
    local.close()


def _status_response(status: int, retry_after: str | None = None) -> Response:
    response = Response()
    response.status_code = status
    response._content = json.dumps({"detail": "x"}).encode("utf-8")
    response.url = "http://127.0.0.1:8000/api/v1/agent/jobs/1/complete"
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    response.raise_for_status = MagicMock(
        side_effect=__import__("requests").HTTPError("HTTP %d" % status, response=response)
    )
    return response


# ── ① 首发单发 vs ② 无落点回退 ─────────────────────────────────────────────


def test_first_attempt_is_single_when_outbox_is_durable(monkeypatch):
    """事实已落 outbox ⇒ 首发只一次：重试只会把舱壁削掉的峰打回去。"""
    local_db = MagicMock()  # enqueue_terminal 成功
    attempts_seen: list[int] = []

    def _fake_post(url, payload, context=None, attempts=None):
        attempts_seen.append(attempts)
        raise RuntimeError("network down")

    monkeypatch.setattr(api_client, "_post_with_retry", _fake_post)

    api_client.complete_job(
        "http://x", 7, {"status": "COMPLETED"}, fencing_token="t7", local_db=local_db,
    )

    assert attempts_seen == [1], "首发必须只尝试一次"
    local_db.enqueue_terminal.assert_called_once()
    local_db.ack_terminal.assert_not_called()


def test_retries_kept_when_outbox_enqueue_failed(monkeypatch):
    """没有补送落点时保留线程内重试——此时丢事实才是更坏的失败。"""
    monkeypatch.setenv("AGENT_POST_RETRIES", "3")
    local_db = MagicMock()
    local_db.enqueue_terminal.side_effect = RuntimeError("sqlite locked")
    attempts_seen: list[int] = []

    def _fake_post(url, payload, context=None, attempts=None):
        attempts_seen.append(attempts)
        raise RuntimeError("network down")

    monkeypatch.setattr(api_client, "_post_with_retry", _fake_post)

    with pytest.raises(api_client.TerminalReportLostError):
        api_client.complete_job(
            "http://x", 8, {"status": "FAILED"}, fencing_token="t8", local_db=local_db,
        )

    assert attempts_seen == [3], "无 outbox 落点必须回退到 AGENT_POST_RETRIES"


def test_no_local_db_keeps_legacy_retries(monkeypatch):
    """`local_db=None`（真实调用点不出现）保持旧语义：不改变重试口径。"""
    monkeypatch.setenv("AGENT_POST_RETRIES", "2")
    attempts_seen: list[int] = []

    def _fake_post(url, payload, context=None, attempts=None):
        attempts_seen.append(attempts)
        raise RuntimeError("network down")

    monkeypatch.setattr(api_client, "_post_with_retry", _fake_post)

    with pytest.raises(RuntimeError):
        api_client.complete_job("http://x", 9, {"status": "FAILED"}, fencing_token="t9")

    assert attempts_seen == [2]


# ── ③ 每机并发上限 ─────────────────────────────────────────────────────────


def test_concurrent_terminal_uploads_are_capped(monkeypatch):
    """同刻在飞的终态 POST ≤ 上限（默认 2）：单机 13–23 个设备同时收尾时不许一起打。"""
    monkeypatch.setattr(api_client, "_TERMINAL_UPLOAD_SEMAPHORE", threading.BoundedSemaphore(2))
    inflight = 0
    peak = 0
    lock = threading.Lock()

    def _slow_post(url, payload, context=None, attempts=None):
        nonlocal inflight, peak
        with lock:
            inflight += 1
            peak = max(peak, inflight)
        time.sleep(0.05)
        with lock:
            inflight -= 1

    monkeypatch.setattr(api_client, "_post_with_retry", _slow_post)

    threads = [
        threading.Thread(
            target=api_client.complete_job,
            args=("http://x", job_id, {"status": "COMPLETED"}),
            kwargs={"fencing_token": "t", "local_db": MagicMock()},
        )
        for job_id in range(6)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert peak <= 2, f"同刻在飞 {peak} > 上限 2"
    assert peak >= 2, "上限内应能并发（否则测得的是串行化，不是削峰）"


# ── ④ abort 族抖动 ─────────────────────────────────────────────────────────


def test_abort_terminal_gets_jittered_initial_delay(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(api_client.time, "sleep", lambda seconds: slept.append(seconds))
    monkeypatch.setattr(api_client, "_post_with_retry", lambda *a, **k: None)

    api_client.complete_job(
        "http://x", 1, {"status": "ABORTED"}, fencing_token="t1", local_db=MagicMock(),
    )

    assert len(slept) == 1, "abort 族必须错峰一次"
    assert 0.0 <= slept[0] <= 5.0, f"抖动越界：{slept[0]}"


def test_abort_family_includes_normalized_canceled(monkeypatch):
    """`_build_complete_payload` 把 ABORTED 归一成 CANCELED——归一后的形状也要抖动。"""
    scripted: list[float] = []
    monkeypatch.setattr(api_client.time, "sleep", lambda seconds: scripted.append(seconds))
    monkeypatch.setattr(api_client, "_post_with_retry", lambda *a, **k: None)

    api_client.complete_job(
        "http://x", 2, {"status": "CANCELED"}, fencing_token="t2", local_db=MagicMock(),
    )
    assert len(scripted) == 1


def test_non_abort_terminal_is_not_delayed(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(api_client.time, "sleep", lambda seconds: slept.append(seconds))
    monkeypatch.setattr(api_client, "_post_with_retry", lambda *a, **k: None)

    api_client.complete_job(
        "http://x", 3, {"status": "COMPLETED"}, fencing_token="t3", local_db=MagicMock(),
    )
    assert slept == [], "非 abort 终态不需要错峰（它们本来就不会同时收尾）"


# ── ⑤ drainer：503 吃 Retry-After / 无头指数+jitter / 硬上限 ─────────────────


def test_outbox_honors_retry_after_on_503(db, monkeypatch):
    """503（舱壁/过载）必须按 Retry-After 退避——原来 5xx 只有 15s 固定节奏。"""
    db.enqueue_terminal(1, {"update": {"status": "FAILED"}})
    drainer = OutboxDrainThread("http://127.0.0.1:8000", db, interval=15.0)

    monkeypatch.setattr(
        "backend.agent.outbox_drainer.requests.post",
        lambda *a, **k: _status_response(503, retry_after="7"),
    )
    drainer._drain_once()

    now = time.monotonic()
    wait = drainer._defer_until[1] - now
    assert 7.0 <= wait <= 7.0 * 1.25 + 0.5, f"503 的 Retry-After 未被遵守：wait={wait:.1f}s"


def test_outbox_5xx_without_retry_after_uses_jittered_backoff(db, monkeypatch):
    """无 Retry-After 的 5xx：指数 + full jitter（0–15s 首轮），不再同相位重放。"""
    db.enqueue_terminal(1, {"update": {"status": "FAILED"}})
    drainer = OutboxDrainThread("http://127.0.0.1:8000", db, interval=15.0)

    monkeypatch.setattr(
        "backend.agent.outbox_drainer.requests.post",
        lambda *a, **k: _status_response(500),
    )
    drainer._drain_once()

    wait = drainer._defer_until[1] - time.monotonic()
    assert 0.0 <= wait <= drainer._BACKOFF_BASE_SECONDS, f"首轮退避越界：{wait:.1f}s"


def test_outbox_backoff_grows_with_attempts(db, monkeypatch):
    """attempts 变大 ⇒ 退避上限按 2^(n-1) 增长（跨周期指数退避），且仍受硬上限约束。"""
    db.enqueue_terminal(1, {"update": {"status": "FAILED"}})
    drainer = OutboxDrainThread("http://127.0.0.1:8000", db, interval=15.0)

    monkeypatch.setattr(
        "backend.agent.outbox_drainer.requests.post",
        lambda *a, **k: _status_response(500),
    )
    # 直接问策略函数：attempts=4 ⇒ ceiling = 15·2^3 = 120s
    assert drainer._next_deferral_seconds(retry_after=0.0, attempts=4) <= 120.0
    assert drainer._next_deferral_seconds(retry_after=0.0, attempts=1) <= 15.0
    # 巨大 Retry-After 仍不得突破硬上限（#1551 判据）
    assert drainer._next_deferral_seconds(
        retry_after=999999.0, attempts=1,
    ) <= OutboxDrainThread._MAX_RETRY_AFTER_SECONDS
