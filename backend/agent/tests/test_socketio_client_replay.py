"""审计 Agent #5 — socketio_client `_replay_buffer` 顺序与丢弃指标的回归覆盖。

覆盖:
- `_replay_buffer` 三段式 drain → emit → 失败 prepend 仍然按 FIFO 重发
- 全部 emit 成功后 `_buffer` 为空
- 中途 emit 失败时未发送切片整体 prepend 回 head,原顺序保留
- `deque(maxlen)` 满载触发 `_dropped_count` 累计 + 周期 warning
- `dropped_count` property + `reset_dropped_count` 暴露给监控
"""

from __future__ import annotations

from collections import deque
from unittest.mock import MagicMock

import pytest

from backend.agent.socketio_client import AgentSocketIOClient


def _make_client(max_buffer: int = 1000) -> AgentSocketIOClient:
    client = AgentSocketIOClient(
        api_url="http://127.0.0.1:8000",
        host_id="host-1",
        agent_secret="",
    )
    # 测试时不发起真实连接,直接 mock _sio
    client._sio = MagicMock()
    client._connected = True
    # 收窄到测试 buffer 大小
    client._buffer = deque(maxlen=max_buffer)
    client.__class__.MAX_BUFFER = max_buffer  # type: ignore[misc]
    return client


def test_replay_buffer_drains_in_order_when_all_succeed():
    client = _make_client()
    client._connected = False  # 让 _emit 走入队路径
    for i in range(5):
        client._emit("step_log", {"seq": i})
    assert len(client._buffer) == 5

    # 重连后 _on_connect 调用 _replay_buffer
    client._connected = True
    client._sio.emit = MagicMock()
    client._replay_buffer()

    assert len(client._buffer) == 0
    assert client._sio.emit.call_count == 5
    seqs = [
        call.args[1]["seq"]
        for call in client._sio.emit.call_args_list
    ]
    assert seqs == [0, 1, 2, 3, 4]


def test_replay_buffer_prepends_remaining_in_order_on_failure():
    client = _make_client()
    client._connected = False
    for i in range(5):
        client._emit("step_log", {"seq": i})

    # 让第 3 次 emit 抛错(seq=2 处)
    call_count = {"n": 0}

    def fake_emit(event, data, namespace=None):
        if call_count["n"] == 2:
            call_count["n"] += 1
            raise RuntimeError("network blip")
        call_count["n"] += 1

    client._connected = True
    client._sio.emit = fake_emit
    client._replay_buffer()

    # seq 0, 1 已发送; seq 2 (失败), 3, 4 应原序 prepend 回 buffer
    remaining = list(client._buffer)
    assert [m["seq"] for m in remaining] == [2, 3, 4]
    # 失败回填的 msg 必须带回 _event 字段,否则下次 replay 丢类型信息
    assert all(m["_event"] == "step_log" for m in remaining)
    # 状态切换到 disconnected
    assert client._connected is False


def test_buffer_overflow_increments_dropped_count():
    client = _make_client(max_buffer=3)
    client._connected = False

    for i in range(5):
        client._emit("step_log", {"seq": i})

    # deque(maxlen=3) 满后 append 会丢左端(最旧),共丢 2 条
    assert len(client._buffer) == 3
    assert client._dropped_count == 2
    assert client.dropped_count == 2


def test_reset_dropped_count_zeroes_counter():
    client = _make_client(max_buffer=2)
    client._connected = False
    for i in range(5):
        client._emit("step_log", {"seq": i})
    assert client._dropped_count == 3

    snap = client.reset_dropped_count()
    assert snap == 3
    assert client.dropped_count == 0


def test_replay_prepend_drops_newest_on_overflow():
    """_prepend_locked 满载时丢 deque 右端(最新尾部消息)。

    场景:replay 失败回填时,buffer 内可能已经有并发 _emit 入队的新消息。
    prepend 顺序保证 drained 在前,新消息在后,溢出时丢 newest 是合理的(优先保 oldest 顺序)。
    """
    client = _make_client(max_buffer=3)
    client._connected = False
    # 模拟并发 _emit 已入队两条新消息
    client._emit("step_log", {"seq": 100})
    client._emit("step_log", {"seq": 101})
    assert client._dropped_count == 0

    # prepend 三条 drained items,buffer 已有 2 条,prepend 3 条会触发 1 次溢出
    items = [{"_event": "step_log", "seq": i} for i in [0, 1, 2]]
    with client._lock:
        client._prepend_locked(items)

    # 总容量 3,prepend 后顺序应为 [0, 1, 2, ...] 头部对齐
    remaining = list(client._buffer)
    assert remaining[0]["seq"] == 0
    assert remaining[1]["seq"] == 1
    assert remaining[2]["seq"] == 2
    assert len(remaining) == 3
    # 溢出 2 条(100 和 101 都被 deque 右端丢弃)
    assert client._dropped_count == 2
