"""#887 — agent_sid_registry 原子 compare-and-delete/expire。

- 旧连接 unregister 不能删除不同 sid/instance 的新登记（交错重连）。
- renew（#881）同型竞态一并收口：值不匹配时不续期，杜绝旧 payload 写回覆盖。
- eval 参考语义 fake 固化 Lua 脚本行为（GET→值比对→DEL/EXPIRE，Redis 单线程
  原子）；脚本语义的真实原子性另由 docker redis 实测（Agent Note）。
"""
from __future__ import annotations

import pytest

from backend.realtime import agent_sid_registry as registry


class _FakeRedis:
    """``eval`` 的参考语义 fake：值相等才 DEL/EXPIRE，否则 no-op 返回 0。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expires: list[tuple[str, int]] = []

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def eval(self, script: str, numkeys: int, key: str, *args: object):
        assert numkeys == 1
        if "DEL" in script:
            if self.store.get(key) == args[0]:
                del self.store[key]
                return 1
            return 0
        if "EXPIRE" in script:
            if self.store.get(key) == args[0]:
                self.expires.append((key, int(args[1])))  # type: ignore[arg-type]
                return 1
            return 0
        raise AssertionError(f"unexpected script: {script!r}")


@pytest.fixture()
def fake_registry(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("STP_AGENT_SID_REGISTRY", "1")
    client = _FakeRedis()
    monkeypatch.setattr(registry, "_redis", client)
    return client


@pytest.mark.asyncio
async def test_unregister_removes_own_registration(fake_registry):
    await registry.register_agent_owner("h1", "sid-A")
    await registry.unregister_agent_owner("h1", "sid-A")
    assert registry.owner_key("h1") not in fake_registry.store


@pytest.mark.asyncio
async def test_unregister_spares_newer_registration_after_reconnect(fake_registry):
    """#887 核心：旧 disconnect 与新 connect 交错——A 的 unregister 不得删除
    B（新 sid）的登记；B 换进程 instance 时同样不删。"""
    await registry.register_agent_owner("h1", "sid-A")
    await registry.register_agent_owner("h1", "sid-B")  # 新连接覆盖（重连或他进程接管）
    await registry.unregister_agent_owner("h1", "sid-A")
    owner = await registry.lookup_agent_owner("h1")
    assert owner is not None and owner["sid"] == "sid-B"


@pytest.mark.asyncio
async def test_unregister_ignores_foreign_instance(fake_registry, monkeypatch):
    """同 sid 不同 instance（跨进程）不互删。"""
    await registry.register_agent_owner("h1", "sid-A")
    other = f"other-{registry.control_plane_instance_id()}"
    monkeypatch.setattr(registry, "_INSTANCE_ID", other)
    await registry.unregister_agent_owner("h1", "sid-A")
    owner = await registry.lookup_agent_owner("h1")
    assert owner is not None and owner["sid"] == "sid-A"


@pytest.mark.asyncio
async def test_unregister_missing_key_is_noop(fake_registry):
    await registry.unregister_agent_owner("h1", "sid-A")
    assert registry.owner_key("h1") not in fake_registry.store


@pytest.mark.asyncio
async def test_renew_extends_only_own_registration(fake_registry):
    await registry.register_agent_owner("h1", "sid-A")
    assert await registry.renew_agent_owner("h1", "sid-A") is True
    assert fake_registry.expires == [(registry.owner_key("h1"), registry.owner_ttl_seconds())]


@pytest.mark.asyncio
async def test_renew_never_resurrects_or_overwrites_newer_registration(fake_registry):
    """#881 同型竞态收口：新登记覆盖后，旧连接的 renew 返回 False 且不写回。"""
    await registry.register_agent_owner("h1", "sid-A")
    await registry.register_agent_owner("h1", "sid-B")
    assert await registry.renew_agent_owner("h1", "sid-A") is False
    assert fake_registry.expires == []
    owner = await registry.lookup_agent_owner("h1")
    assert owner is not None and owner["sid"] == "sid-B"


@pytest.mark.asyncio
async def test_renew_missing_key_returns_false(fake_registry):
    assert await registry.renew_agent_owner("h1", "sid-A") is False


@pytest.mark.asyncio
async def test_lookup_returns_none_when_key_missing(fake_registry):
    assert await registry.lookup_agent_owner("h1") is None
