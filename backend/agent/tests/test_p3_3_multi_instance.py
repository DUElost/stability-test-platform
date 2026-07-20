"""ADR-0027 P3-3 — singleton scheduler wrap + agent sid registry tests."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.realtime import agent_sid_registry as reg
from backend.scheduler.app_scheduler import (
    SINGLETON_SCHEDULE_IDS,
    _instrumented,
    _with_leadership,
)


def test_singleton_schedule_ids_cover_p3_3_jobs():
    expected = {
        "recycler",
        "session_watchdog",
        "device_lease_reconciler",
        "cron_check",
        "retention_cleanup",
        "precheck_reaper",
        "plan_chain_reconciler",
        "revoked_token_cleanup",
        "auto_archive_sweep",
    }
    assert SINGLETON_SCHEDULE_IDS == expected
    # Internal leadership — must NOT double-wrap.
    assert "admission_pump" not in SINGLETON_SCHEDULE_IDS
    assert "counter_reconcile" not in SINGLETON_SCHEDULE_IDS
    assert "saq_queue_depth_poll" not in SINGLETON_SCHEDULE_IDS


def test_with_leadership_skips_sync_when_not_leader(monkeypatch):
    calls = []

    def job():
        calls.append(1)
        return {"ok": 1}

    @contextmanager
    def _never(_name):
        yield False

    monkeypatch.setattr(
        "backend.core.leader_election.hold_scheduler_leadership",
        _never,
    )
    wrapped = _with_leadership("recycler", job)
    assert wrapped() == {"skipped_not_leader": 1}
    assert calls == []


def test_with_leadership_runs_sync_when_leader(monkeypatch):
    @contextmanager
    def _always(_name):
        yield True

    monkeypatch.setattr(
        "backend.core.leader_election.hold_scheduler_leadership",
        _always,
    )
    wrapped = _with_leadership("recycler", lambda: {"ok": 1})
    assert wrapped() == {"ok": 1}


@pytest.mark.asyncio
async def test_with_leadership_skips_async_when_not_leader(monkeypatch):
    calls = []

    async def job():
        calls.append(1)
        return {"ok": 1}

    @contextmanager
    def _never(_name):
        yield False

    monkeypatch.setattr(
        "backend.core.leader_election.hold_scheduler_leadership",
        _never,
    )
    wrapped = _with_leadership("session_watchdog", job)
    assert await wrapped() == {"skipped_not_leader": 1}
    assert calls == []


def test_instrumented_singleton_composes(monkeypatch):
    """singleton=True must consult leadership before invoking the job."""

    @contextmanager
    def _never(_name):
        yield False

    monkeypatch.setattr(
        "backend.core.leader_election.hold_scheduler_leadership",
        _never,
    )
    monkeypatch.setattr(
        "backend.scheduler.app_scheduler.record_apscheduler_job",
        lambda *a, **k: None,
    )
    ran = []

    def job():
        ran.append(1)

    wrapped = _instrumented("recycler", job, singleton=True)
    assert wrapped() == {"skipped_not_leader": 1}
    assert ran == []


def test_registry_disabled_under_testing(monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("STP_SOCKETIO_REDIS_ADAPTER", "1")
    monkeypatch.setenv("STP_AGENT_SID_REGISTRY", "1")
    assert reg.agent_sid_registry_enabled() is False


def test_registry_follows_adapter_by_default(monkeypatch):
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("STP_AGENT_SID_REGISTRY", raising=False)
    monkeypatch.setenv("STP_SOCKETIO_REDIS_ADAPTER", "0")
    assert reg.agent_sid_registry_enabled() is False
    monkeypatch.setenv("STP_SOCKETIO_REDIS_ADAPTER", "1")
    assert reg.agent_sid_registry_enabled() is True


def test_registry_explicit_override(monkeypatch):
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setenv("STP_SOCKETIO_REDIS_ADAPTER", "0")
    monkeypatch.setenv("STP_AGENT_SID_REGISTRY", "1")
    assert reg.agent_sid_registry_enabled() is True
    monkeypatch.setenv("STP_SOCKETIO_REDIS_ADAPTER", "1")
    monkeypatch.setenv("STP_AGENT_SID_REGISTRY", "0")
    assert reg.agent_sid_registry_enabled() is False


@pytest.mark.asyncio
async def test_register_lookup_unregister_roundtrip(monkeypatch):
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setenv("STP_AGENT_SID_REGISTRY", "1")
    store: dict[str, str] = {}

    class FakeRedis:
        async def set(self, key, value, ex=None):
            store[key] = value

        async def get(self, key):
            return store.get(key)

        async def delete(self, key):
            store.pop(key, None)

    reg.configure_agent_sid_registry(FakeRedis())
    await reg.register_agent_owner("42", "sid-abc")
    owner = await reg.lookup_agent_owner("42")
    assert owner is not None
    assert owner["sid"] == "sid-abc"
    assert owner["instance_id"] == reg.control_plane_instance_id()
    await reg.unregister_agent_owner("42", "sid-abc")
    assert await reg.lookup_agent_owner("42") is None


@pytest.mark.asyncio
async def test_call_agent_rpc_room_fallback(monkeypatch):
    """Local sid miss + Redis adapter → room-targeted call."""
    import backend.realtime.socketio_server as sio_mod

    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setenv("STP_SOCKETIO_REDIS_ADAPTER", "1")
    monkeypatch.setenv("STP_AGENT_SID_REGISTRY", "1")

    class FakeRedis:
        async def get(self, key):
            return (
                '{"instance_id":"other","sid":"remote-sid","host_id":"7"}'
            )

    reg.configure_agent_sid_registry(FakeRedis())

    ns = MagicMock()
    ns.get_sid.return_value = None
    sio = MagicMock()
    sio.call = AsyncMock(return_value={"ok": True})

    monkeypatch.setattr(sio_mod, "get_sio", lambda: sio)
    monkeypatch.setattr(sio_mod, "get_agent_namespace", lambda: ns)

    result = await sio_mod.call_agent_rpc("7", "ping", {"x": 1}, timeout=3.0)
    assert result == {"ok": True}
    sio.call.assert_awaited_once()
    kwargs = sio.call.await_args.kwargs
    assert kwargs["room"] == "agent:7"
    assert "to" not in kwargs


@pytest.mark.asyncio
async def test_call_agent_rpc_local_sid_preferred(monkeypatch):
    import backend.realtime.socketio_server as sio_mod

    ns = MagicMock()
    ns.get_sid.return_value = "local-sid"
    sio = MagicMock()
    sio.call = AsyncMock(return_value={"ok": True})

    monkeypatch.setattr(sio_mod, "get_sio", lambda: sio)
    monkeypatch.setattr(sio_mod, "get_agent_namespace", lambda: ns)

    await sio_mod.call_agent_rpc("7", "ping", {})
    kwargs = sio.call.await_args.kwargs
    assert kwargs["to"] == "local-sid"
    assert "room" not in kwargs


@pytest.mark.asyncio
async def test_call_agent_rpc_no_adapter_still_requires_local(monkeypatch):
    import backend.realtime.socketio_server as sio_mod

    monkeypatch.setenv("TESTING", "1")  # adapter forced off
    ns = MagicMock()
    ns.get_sid.return_value = None
    monkeypatch.setattr(sio_mod, "get_sio", lambda: MagicMock())
    monkeypatch.setattr(sio_mod, "get_agent_namespace", lambda: ns)

    with pytest.raises(sio_mod.AgentNotConnectedError):
        await sio_mod.call_agent_rpc("7", "ping", {})


@pytest.mark.asyncio
async def test_call_agent_rpc_registry_miss_fails_fast(monkeypatch):
    """Adapter + registry on, no owner → immediate AgentNotConnectedError."""
    import backend.realtime.socketio_server as sio_mod

    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setenv("STP_SOCKETIO_REDIS_ADAPTER", "1")
    monkeypatch.setenv("STP_AGENT_SID_REGISTRY", "1")

    class FakeRedis:
        async def get(self, key):
            return None

    reg.configure_agent_sid_registry(FakeRedis())
    ns = MagicMock()
    ns.get_sid.return_value = None
    sio = MagicMock()
    sio.call = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(sio_mod, "get_sio", lambda: sio)
    monkeypatch.setattr(sio_mod, "get_agent_namespace", lambda: ns)

    with pytest.raises(sio_mod.AgentNotConnectedError):
        await sio_mod.call_agent_rpc("7", "ping", {})
    sio.call.assert_not_awaited()
