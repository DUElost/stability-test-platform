"""ADR-0021 — Server-side Agent SocketIO RPC unit tests.

Covers ``AgentNamespace`` host_id ↔ sid mapping and ``call_agent_rpc``
error paths.  End-to-end RPC (real ack roundtrip) is left to integration
tests, since python-socketio's ack mechanism requires a live ASGI loop.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.realtime import socketio_server
from backend.realtime.socketio_server import (
    AgentNamespace,
    AgentNotConnectedError,
    AgentRpcError,
    call_agent_rpc,
    get_agent_namespace,
)


# ---------------------------------------------------------------------------
# AgentNamespace host_id ↔ sid tracking
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_namespace(monkeypatch):
    """Build a fresh ``AgentNamespace`` with parent SocketIO machinery
    stubbed out.  We exercise only the bookkeeping logic, not the
    AsyncNamespace's transport layer.
    """
    ns = AgentNamespace("/agent")

    fake_session: dict[str, dict] = {}

    class _SessionCtx:
        def __init__(self, sid: str):
            self._sid = sid

        async def __aenter__(self):
            return fake_session.setdefault(self._sid, {})

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def _enter_room(sid, room):
        return None

    monkeypatch.setattr(ns, "session", lambda sid: _SessionCtx(sid))
    monkeypatch.setattr(ns, "enter_room", _enter_room)
    return ns


@pytest.mark.asyncio
async def test_agent_namespace_tracks_sid_on_connect(patched_namespace, monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("AGENT_SECRET", "socket-secret-123456")

    await patched_namespace.on_connect(
        "sid-A", environ={}, auth={"agent_secret": "socket-secret-123456", "host_id": "host-101"}
    )
    assert patched_namespace.get_sid("host-101") == "sid-A"
    assert patched_namespace.connected_host_ids() == ["host-101"]


@pytest.mark.asyncio
async def test_agent_namespace_drops_sid_on_disconnect(patched_namespace, monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("AGENT_SECRET", "socket-secret-123456")

    await patched_namespace.on_connect(
        "sid-A", environ={}, auth={"agent_secret": "socket-secret-123456", "host_id": "host-101"}
    )
    await patched_namespace.on_disconnect("sid-A")
    assert patched_namespace.get_sid("host-101") is None
    assert patched_namespace.connected_host_ids() == []


@pytest.mark.asyncio
async def test_agent_namespace_disconnect_with_stale_sid_keeps_current_mapping(
    patched_namespace, monkeypatch
):
    """If host reconnects with a new sid, an older sid's disconnect must
    NOT erase the live mapping."""
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("AGENT_SECRET", "socket-secret-123456")

    await patched_namespace.on_connect(
        "sid-old", environ={}, auth={"agent_secret": "socket-secret-123456", "host_id": "host-X"}
    )
    await patched_namespace.on_connect(
        "sid-new", environ={}, auth={"agent_secret": "socket-secret-123456", "host_id": "host-X"}
    )
    assert patched_namespace.get_sid("host-X") == "sid-new"

    await patched_namespace.on_disconnect("sid-old")
    assert patched_namespace.get_sid("host-X") == "sid-new"


@pytest.mark.asyncio
async def test_agent_namespace_rejects_when_server_secret_missing(
    patched_namespace, monkeypatch
):
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.delenv("AGENT_SECRET", raising=False)

    with pytest.raises(Exception, match="AGENT_SECRET not configured"):
        await patched_namespace.on_connect(
            "sid-A", environ={}, auth={"agent_secret": "", "host_id": "host-101"}
        )


@pytest.mark.asyncio
async def test_agent_namespace_get_sid_returns_none_for_unknown_host():
    ns = AgentNamespace("/agent")
    assert ns.get_sid("nope") is None


# ---------------------------------------------------------------------------
# call_agent_rpc
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_sio_and_ns(monkeypatch):
    """Install a fake ``_sio`` and ``_agent_ns`` so ``call_agent_rpc`` works
    without booting a real SocketIO server."""
    fake_sio = MagicMock()
    fake_sio.call = AsyncMock()

    fake_ns = AgentNamespace("/agent")

    monkeypatch.setattr(socketio_server, "_sio", fake_sio)
    monkeypatch.setattr(socketio_server, "_agent_ns", fake_ns)
    return fake_sio, fake_ns


@pytest.mark.asyncio
async def test_call_agent_rpc_raises_when_host_offline(stub_sio_and_ns):
    fake_sio, fake_ns = stub_sio_and_ns
    with pytest.raises(AgentNotConnectedError) as excinfo:
        await call_agent_rpc("host-missing", "verify_scripts", {})
    assert excinfo.value.host_id == "host-missing"
    fake_sio.call.assert_not_called()


@pytest.mark.asyncio
async def test_call_agent_rpc_forwards_to_sio_call_with_correct_args(stub_sio_and_ns):
    fake_sio, fake_ns = stub_sio_and_ns
    fake_ns._host_to_sid["host-A"] = "sid-A"
    fake_sio.call.return_value = {"ok": True, "results": []}

    ack = await call_agent_rpc(
        "host-A", "verify_scripts", {"expected": []}, timeout=4.0
    )

    assert ack == {"ok": True, "results": []}
    fake_sio.call.assert_awaited_once_with(
        "verify_scripts",
        {"expected": []},
        to="sid-A",
        namespace="/agent",
        timeout=4.0,
    )


@pytest.mark.asyncio
async def test_call_agent_rpc_wraps_timeout(stub_sio_and_ns):
    fake_sio, fake_ns = stub_sio_and_ns
    fake_ns._host_to_sid["host-A"] = "sid-A"
    fake_sio.call.side_effect = asyncio.TimeoutError()

    with pytest.raises(AgentRpcError, match="timed out"):
        await call_agent_rpc("host-A", "verify_scripts", {}, timeout=0.5)


@pytest.mark.asyncio
async def test_call_agent_rpc_wraps_unexpected_exception(stub_sio_and_ns):
    fake_sio, fake_ns = stub_sio_and_ns
    fake_ns._host_to_sid["host-A"] = "sid-A"
    fake_sio.call.side_effect = ConnectionError("socket dead")

    with pytest.raises(AgentRpcError, match="failed"):
        await call_agent_rpc("host-A", "verify_scripts", {})


@pytest.mark.asyncio
async def test_call_agent_rpc_rejects_none_ack(stub_sio_and_ns):
    fake_sio, fake_ns = stub_sio_and_ns
    fake_ns._host_to_sid["host-A"] = "sid-A"
    fake_sio.call.return_value = None

    with pytest.raises(AgentRpcError, match="no ack"):
        await call_agent_rpc("host-A", "verify_scripts", {})


@pytest.mark.asyncio
async def test_call_agent_rpc_rejects_non_dict_ack(stub_sio_and_ns):
    fake_sio, fake_ns = stub_sio_and_ns
    fake_ns._host_to_sid["host-A"] = "sid-A"
    fake_sio.call.return_value = "string-ack"

    with pytest.raises(AgentRpcError, match="non-dict"):
        await call_agent_rpc("host-A", "verify_scripts", {})


# ---------------------------------------------------------------------------
# get_agent_namespace
# ---------------------------------------------------------------------------


def test_get_agent_namespace_raises_when_not_initialised(monkeypatch):
    monkeypatch.setattr(socketio_server, "_agent_ns", None)
    with pytest.raises(RuntimeError, match="not registered"):
        get_agent_namespace()


def test_get_agent_namespace_returns_singleton(monkeypatch):
    ns = AgentNamespace("/agent")
    monkeypatch.setattr(socketio_server, "_agent_ns", ns)
    assert get_agent_namespace() is ns
