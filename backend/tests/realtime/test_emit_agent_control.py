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
