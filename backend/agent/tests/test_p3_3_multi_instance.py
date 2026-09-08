"""ADR-0027 P3-3 — singleton scheduler wrap + agent sid registry tests."""

from __future__ import annotations

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
    assert "signal_link_reconcile" not in SINGLETON_SCHEDULE_IDS
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

        async def eval(self, script, numkeys, key, *args):
            # #887: compare-and-delete 参考语义（值相等才删）。
            if "DEL" in script:
                if store.get(key) == args[0]:
                    del store[key]
                    return 1
                return 0
            return 0

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


# ── #881：SID registry TTL 续期（R01-F01）──


class _TtlFakeRedis:
    """尊重 ex= 的最小 fake（原 FakeRedis 忽略 ex，无法覆盖过期行为）。"""

    def __init__(self):
        self.store: dict[str, tuple[str, float | None]] = {}
        self.now = 1000.0  # 测试控制的虚拟时钟

    async def set(self, key, value, ex=None):
        expiry = self.now + ex if ex else None
        self.store[key] = (value, expiry)

    async def get(self, key):
        item = self.store.get(key)
        if item is None:
            return None
        value, expiry = item
        if expiry is not None and self.now > expiry:
            del self.store[key]
        return self.store.get(key, (None,))[0]

    async def delete(self, key):
        self.store.pop(key, None)

    async def eval(self, script, numkeys, key, *args):
        # #887: compare-and-delete/expire 参考语义（含虚拟时钟过期模型）。
        item = self.store.get(key)
        if item is not None:
            value, expiry = item
            if expiry is not None and self.now > expiry:
                del self.store[key]
                item = None
        if item is None or item[0] != args[0]:
            return 0
        if "DEL" in script:
            del self.store[key]
            return 1
        if "EXPIRE" in script:
            self.store[key] = (item[0], self.now + int(args[1]))
            return 1
        return 0

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_renew_extends_ttl_beyond_initial_window(monkeypatch):
    """跨进程 RPC 在连接存活但超过初始 TTL 后仍可达（#881 验收 b）。"""
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setenv("STP_AGENT_SID_REGISTRY", "1")
    fake = _TtlFakeRedis()
    reg.configure_agent_sid_registry(fake)
    ttl = reg.owner_ttl_seconds()

    # Owner 进程注册（connect）
    import asyncio

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        reg.register_agent_owner("7", "sid-owner")
    )
    # 恰好在 TTL 边界前：另一进程 lookup 可达
    fake.advance(ttl - 5)
    assert reg.control_plane_instance_id()  # sanity
    # 未续租路径复现（旧行为）：推进超过初始 TTL → key 过期
    # （此处先验证续租修复；旧行为由下面的不续租用例覆盖）

    # Owner 心跳续租 → key 恢复完整 TTL
    renewed = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        reg.renew_agent_owner("7", "sid-owner")
    )
    assert renewed is True
    fake.advance(ttl - 5)  # 距上次续租接近新 TTL
    # 跨进程 lookup（另一 instance 视角）仍可达——即 #881 要保的语义
    # （lookup 读同一 fake store；跨进程差异仅在 instance_id 字段值）
    owner = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        reg.lookup_agent_owner("7")
    )
    assert owner is not None and owner["sid"] == "sid-owner"


def test_renew_does_not_resurrect_foreign_or_expired_keys(monkeypatch):
    """续租只作用于本进程 + 本 sid 的 key（不复活他人/已过期）。"""
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setenv("STP_AGENT_SID_REGISTRY", "1")
    fake = _TtlFakeRedis()
    reg.configure_agent_sid_registry(fake)
    ttl = reg.owner_ttl_seconds()

    import asyncio

    async def _run():
        # 他人 instance 注册的 key：renew 不得续
        await fake.set(reg.owner_key("9"),
                       '{"instance_id":"other","sid":"remote","host_id":"9"}',
                       ex=ttl)
        assert await reg.renew_agent_owner("9", "remote") is False
        fake.advance(ttl + 1)
        assert await reg.lookup_agent_owner("9") is None  # 未被复活

        # 本进程 key 过期后：renew 不得复活（连接断开即应消失）
        await reg.register_agent_owner("8", "sid-8")
        fake.advance(ttl + 1)
        assert await reg.lookup_agent_owner("8") is None  # 已过期
        assert await reg.renew_agent_owner("8", "sid-8") is False

        # sid 不匹配（host 被新连接接管）：本进程旧 sid 不得续
        await reg.register_agent_owner("6", "sid-old")
        await fake.set(reg.owner_key("6"),
                       '{"instance_id":"%s","sid":"sid-new","host_id":"6"}'
                       % reg.control_plane_instance_id(), ex=ttl)
        assert await reg.renew_agent_owner("6", "sid-old") is False

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_run())
