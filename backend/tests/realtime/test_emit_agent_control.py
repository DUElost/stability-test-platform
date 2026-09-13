from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.realtime import socketio_server
from backend.realtime.socketio_server import emit_agent_control


@pytest.mark.asyncio
async def test_emit_agent_control_uses_agent_namespace_and_room(monkeypatch):
    fake_sio = MagicMock()
    fake_sio.emit = AsyncMock()
    monkeypatch.setattr(socketio_server, "_sio", fake_sio)

    await emit_agent_control("host-101", "archive_now", payload={"plan_run_id": 42})

    fake_sio.emit.assert_awaited_once_with(
        "control",
        {"command": "archive_now", "payload": {"plan_run_id": 42}},
        namespace="/agent",
        room="agent:host-101",
    )


@pytest.mark.asyncio
async def test_emit_agent_control_defaults_payload_to_empty(monkeypatch):
    fake_sio = MagicMock()
    fake_sio.emit = AsyncMock()
    monkeypatch.setattr(socketio_server, "_sio", fake_sio)

    await emit_agent_control("h-2", "abort")

    fake_sio.emit.assert_awaited_once_with(
        "control",
        {"command": "abort", "payload": {}},
        namespace="/agent",
        room="agent:h-2",
    )


@pytest.mark.asyncio
async def test_emit_agent_control_explicit_none_payload_becomes_empty(monkeypatch):
    fake_sio = MagicMock()
    fake_sio.emit = AsyncMock()
    monkeypatch.setattr(socketio_server, "_sio", fake_sio)

    await emit_agent_control("h-3", "backpressure", payload=None)

    fake_sio.emit.assert_awaited_once_with(
        "control",
        {"command": "backpressure", "payload": {}},
        namespace="/agent",
        room="agent:h-3",
    )


def test_schedule_agent_control_fanout_single_threadsafe_submit(monkeypatch):
    """#703：N 个 host 只向主循环提交 1 次 run_coroutine_threadsafe。"""
    from unittest.mock import AsyncMock, MagicMock

    fake_sio = MagicMock()
    fake_sio.emit = AsyncMock()
    monkeypatch.setattr(socketio_server, "_sio", fake_sio)

    loop = MagicMock()
    loop.is_closed.return_value = False
    submitted: list = []

    def _submit(coro, _loop):
        submitted.append(coro)
        coro.close()  # 本用例只断言提交次数，不跑协程
        fut = MagicMock()
        return fut

    monkeypatch.setattr(socketio_server, "_main_loop", loop)
    monkeypatch.setattr(
        socketio_server.asyncio, "run_coroutine_threadsafe", _submit,
    )

    items = [
        (f"h-{i}", {"command": "abort", "payload": {"job_ids": [i]}})
        for i in range(24)
    ]
    socketio_server.schedule_agent_control_fanout(items, yield_every=8)

    assert len(submitted) == 1


@pytest.mark.asyncio
async def test_schedule_agent_control_fanout_emits_each_room(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    fake_sio = MagicMock()
    fake_sio.emit = AsyncMock()
    monkeypatch.setattr(socketio_server, "_sio", fake_sio)

    loop = MagicMock()
    loop.is_closed.return_value = False
    monkeypatch.setattr(socketio_server, "_main_loop", loop)

    ran: list = []

    def _submit(coro, _loop):
        ran.append(coro)
        fut = MagicMock()
        return fut

    monkeypatch.setattr(
        socketio_server.asyncio, "run_coroutine_threadsafe", _submit,
    )

    items = [
        ("ha", {"command": "abort", "payload": {"job_ids": [1]}}),
        ("hb", {"command": "abort", "payload": {"job_ids": [2]}}),
    ]
    socketio_server.schedule_agent_control_fanout(items, yield_every=1)
    assert len(ran) == 1
    await ran[0]

    assert fake_sio.emit.await_count == 2
    rooms = [c.kwargs["room"] for c in fake_sio.emit.await_args_list]
    assert rooms == ["agent:ha", "agent:hb"]


def test_schedule_agent_control_fanout_empty_is_noop(monkeypatch):
    from unittest.mock import MagicMock

    loop = MagicMock()
    loop.is_closed.return_value = False
    monkeypatch.setattr(socketio_server, "_main_loop", loop)
    calls: list = []
    monkeypatch.setattr(
        socketio_server.asyncio,
        "run_coroutine_threadsafe",
        lambda *a, **k: calls.append(a) or MagicMock(),
    )
    socketio_server.schedule_agent_control_fanout([])
    assert calls == []
