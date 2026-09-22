"""ADR-0027 P3-3 — singleton scheduler wrap + agent sid registry tests."""

from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.realtime import agent_sid_registry as reg
from backend.scheduler import app_scheduler
from backend.scheduler.app_scheduler import (
    SINGLETON_SCHEDULE_IDS,
    _instrumented,
    _with_leadership,
)


#: ADR-0027 的**政策**清单（不是实现的副本）：以下作业自带 *internal* leadership，
#: 再包一层就是不同 DB session 上的嵌套 advisory lock —— 会把 tick 打死。
#: 这份留在测试里手写是对的：它表达「不许重复包」这个决定，不跟注册代码同变。
INTERNAL_LEADERSHIP_IDS = frozenset(
    {
        "admission_pump",
        "counter_reconcile",
        "signal_link_reconcile",
        "saq_queue_depth_poll",
    }
)


def _singleton_call_site_ids() -> set[str]:
    """真实来源 = `app_scheduler` 里 `_instrumented(name, fn, singleton=True)` 的 name。

    判据为什么改成读 AST（#3060：main 全量 CI 确定性红）：本用例原先自带一份手抄的
    `expected` 字面量，于是同一清单存在**三处**（prod 常量 / 注册站点 / 测试字面量）。
    #2958 加 `script_presence_sweep` 时同步了前两处、漏了第三处 —— 确定性红，且红在
    「只有第三处会报」的位置上，排查成本被这份副本放大。测试这份副本被消灭后，前两处
    谁漏改都会在这里红，报错还直接点名是哪一侧、缺哪几项。
    """
    tree = ast.parse(Path(app_scheduler.__file__).read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "_instrumented" or not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        for kw in node.keywords:
            if kw.arg == "singleton" and isinstance(kw.value, ast.Constant):
                if kw.value.value is True:
                    found.add(first.value)
    return found


def test_singleton_schedule_ids_cover_p3_3_jobs():
    call_sites = _singleton_call_site_ids()
    # 取数失效不得伪装成"清单一致"：AST 形状变了要先修本用例，不许放宽断言。
    assert call_sites, "_instrumented(singleton=True) 站点数为 0：取数失效，先修本用例"

    declared = set(SINGLETON_SCHEDULE_IDS)
    not_declared = sorted(call_sites - declared)
    not_wrapped = sorted(declared - call_sites)
    assert not not_declared and not not_wrapped, (
        "单例清单与注册站点不一致（两处都在 app_scheduler.py，改一处即可，"
        "不要再往测试里抄第三份）: "
        f"站点 singleton=True 但常量未登记 {not_declared}; "
        f"常量登记但站点未包 leadership {not_wrapped}"
    )

    # Internal leadership — must NOT double-wrap.
    double = sorted(declared & INTERNAL_LEADERSHIP_IDS)
    assert not double, f"内部 leadership 作业被重复包了一层 leadership：{double}"


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
        # #887 / #1113: compare-and-delete / renew-or-rebuild（含虚拟时钟过期）。
        item = self.store.get(key)
        if item is not None:
            value, expiry = item
            if expiry is not None and self.now > expiry:
                del self.store[key]
                item = None
        if "DEL" in script:
            if item is None or item[0] != args[0]:
                return 0
            del self.store[key]
            return 1
        if "RENEW_OR_REBUILD" in script:
            if item is None:
                self.store[key] = (args[0], self.now + int(args[1]))
                return 1
            if item[0] != args[0]:
                return 0
            self.store[key] = (item[0], self.now + int(args[1]))
            return 1
        if "EXPIRE" in script:
            if item is None or item[0] != args[0]:
                return 0
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


def test_renew_does_not_overwrite_foreign_keys(monkeypatch):
    """续租不覆盖他人 owner；本进程过期 key 在心跳续租时可重建（#1113）。"""
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setenv("STP_AGENT_SID_REGISTRY", "1")
    fake = _TtlFakeRedis()
    reg.configure_agent_sid_registry(fake)
    ttl = reg.owner_ttl_seconds()

    import asyncio

    async def _run():
        # 他人 instance 注册的 key：renew 不得续/覆盖
        await fake.set(reg.owner_key("9"),
                       '{"instance_id":"other","sid":"remote","host_id":"9"}',
                       ex=ttl)
        assert await reg.renew_agent_owner("9", "remote") is False
        fake.advance(ttl + 1)
        assert await reg.lookup_agent_owner("9") is None  # 未被复活

        # 本进程 key 过期后：存活心跳可重建（#1113；连接仍在才有心跳）
        await reg.register_agent_owner("8", "sid-8")
        fake.advance(ttl + 1)
        assert await reg.lookup_agent_owner("8") is None  # 已过期
        assert await reg.renew_agent_owner("8", "sid-8") is True
        owner = await reg.lookup_agent_owner("8")
        assert owner is not None and owner["sid"] == "sid-8"

        # sid 不匹配（host 被新连接接管）：本进程旧 sid 不得续
        await reg.register_agent_owner("6", "sid-old")
        await fake.set(reg.owner_key("6"),
                       '{"instance_id":"%s","sid":"sid-new","host_id":"6"}'
                       % reg.control_plane_instance_id(), ex=ttl)
        assert await reg.renew_agent_owner("6", "sid-old") is False

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_run())
