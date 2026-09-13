"""#1737 P1 — console 归属注册表：全局 run_key 互斥 + owner 登记（同步语义）。

覆盖：门控 / fail-closed 获取 / 严格 CAS 续期（lost 判定）/ CAS 释放 /
owner renew-or-rebuild / TTL 与复位。Lua 语义由参考 fake 固化（比对→写，
Redis 单线程原子），真实原子性归 docker redis 实测（同 #887 口径）。
"""
from __future__ import annotations

import pytest

from backend.realtime import console_registry as registry


class _FakeRedis:
    """同步参考语义 fake：CAS 比对通过才写；``fail=True`` 模拟 Redis 不可达。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expires: list[tuple[str, int]] = []
        self.fail = False
        self.closed = False

    def set(self, key, value, nx=False, px=None, ex=None):  # noqa: ANN001
        if self.fail:
            raise RuntimeError("redis down")
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.expires.append((key, int(ex)))
        if px is not None:
            self.expires.append((key, int(px) // 1000))
        return True

    def get(self, key):  # noqa: ANN001
        if self.fail:
            raise RuntimeError("redis down")
        return self.store.get(key)

    def eval(self, script: str, numkeys: int, key: str, *args):  # noqa: ANN001
        if self.fail:
            raise RuntimeError("redis down")
        assert numkeys == 1
        if "CONSOLE_CAS_DELETE" in script:
            if self.store.get(key) == args[0]:
                del self.store[key]
                return 1
            return 0
        if "CONSOLE_RENEW_OR_REBUILD" in script:
            current = self.store.get(key)
            if current is None:
                self.store[key] = str(args[0])
                self.expires.append((key, int(args[1])))
                return 1
            if current == args[0]:
                self.expires.append((key, int(args[1])))
                return 1
            return 0
        if "CONSOLE_CAS_RENEW" in script:
            if self.store.get(key) == args[0]:
                self.expires.append((key, int(args[1])))
                return 1
            return 0
        raise AssertionError(f"unexpected script: {script!r}")

    def close(self):  # noqa: ANN201
        self.closed = True

    def expire(self, key, seconds):  # noqa: ANN001
        if self.fail:
            raise RuntimeError("redis down")
        if key not in self.store:
            return False
        self.expires.append((key, int(seconds)))
        return True

    def delete(self, key):  # noqa: ANN001
        if self.fail:
            raise RuntimeError("redis down")
        existed = key in self.store
        self.store.pop(key, None)
        return 1 if existed else 0


@pytest.fixture()
def fake_client(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("STP_CONSOLE_REGISTRY", "1")
    monkeypatch.delenv("STP_CONSOLE_REGISTRY_TTL_SECONDS", raising=False)
    client = _FakeRedis()
    monkeypatch.setattr(registry, "_client", client)
    yield client
    registry.reset_console_registry_for_tests()


# ── 门控 ────────────────────────────────────────────────────────────────────


def test_disabled_by_default(monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    assert registry.console_registry_enabled() is False


def test_testing_forces_off(monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("STP_CONSOLE_REGISTRY", "1")
    assert registry.console_registry_enabled() is False


def test_explicit_flag_enables(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("STP_CONSOLE_REGISTRY", "1")
    assert registry.console_registry_enabled() is True


def test_explicit_flag_disables(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("STP_CONSOLE_REGISTRY", "0")
    assert registry.console_registry_enabled() is False


def test_ttl_default_and_floor(monkeypatch):
    monkeypatch.delenv("STP_CONSOLE_REGISTRY_TTL_SECONDS", raising=False)
    assert registry.console_registry_ttl_seconds() == 120
    monkeypatch.setenv("STP_CONSOLE_REGISTRY_TTL_SECONDS", "5")
    assert registry.console_registry_ttl_seconds() == 30  # 下限
    monkeypatch.setenv("STP_CONSOLE_REGISTRY_TTL_SECONDS", "abc")
    assert registry.console_registry_ttl_seconds() == 120


# ── run_key 互斥 ────────────────────────────────────────────────────────────


def test_acquire_then_second_is_busy(fake_client):
    registry.acquire_run_key("jira:transsion", run_id="run-A")
    assert registry.run_key_key("jira:transsion") in fake_client.store
    with pytest.raises(registry.ConsoleRunKeyBusy):
        registry.acquire_run_key("jira:transsion", run_id="run-B")


def test_acquire_fails_closed_on_redis_error(fake_client):
    fake_client.fail = True
    with pytest.raises(registry.ConsoleRegistryUnavailable):
        registry.acquire_run_key("k", run_id="r")


def test_acquire_unavailable_when_not_configured(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("STP_CONSOLE_REGISTRY", "1")
    monkeypatch.setattr(registry, "_client", None)
    with pytest.raises(registry.ConsoleRegistryUnavailable):
        registry.acquire_run_key("k", run_id="r")


def test_acquire_sets_px_ttl(fake_client):
    registry.acquire_run_key("k", run_id="r")
    assert fake_client.expires == [(registry.run_key_key("k"), 120)]


def test_renew_ok_then_lost_after_foreign_overwrite(fake_client):
    registry.acquire_run_key("k", run_id="r")
    assert registry.renew_run_key("k", run_id="r") == registry.RENEW_OK
    # 模拟互斥被外部实例接管（本实例续期失败窗口）
    fake_client.store[registry.run_key_key("k")] = '{"instance_id":"other"}'
    assert registry.renew_run_key("k", run_id="r") == registry.RENEW_LOST


def test_renew_lost_when_key_missing(fake_client):
    registry.acquire_run_key("k", run_id="r")
    del fake_client.store[registry.run_key_key("k")]
    assert registry.renew_run_key("k", run_id="r") == registry.RENEW_LOST


def test_renew_unavailable_on_redis_error(fake_client):
    registry.acquire_run_key("k", run_id="r")
    fake_client.fail = True
    assert registry.renew_run_key("k", run_id="r") == registry.RENEW_UNAVAILABLE


def test_release_only_own(fake_client):
    registry.acquire_run_key("k", run_id="r")
    registry.release_run_key("k", run_id="other-run")  # 非本 run 的 payload → no-op
    assert registry.run_key_key("k") in fake_client.store
    registry.release_run_key("k", run_id="r")
    assert registry.run_key_key("k") not in fake_client.store


# ── owner 登记 ──────────────────────────────────────────────────────────────


def test_register_and_renew_owner_rebuilds_when_missing(fake_client):
    registry.register_owner("run-A", run_key="k")
    assert registry.owner_key("run-A") in fake_client.store
    # 键被 TTL 清掉但 run 仍存活 → 重建（同 #1113 语义）
    del fake_client.store[registry.owner_key("run-A")]
    assert registry.renew_owner("run-A", run_key="k") == registry.RENEW_OK
    assert registry.owner_key("run-A") in fake_client.store


def test_renew_owner_never_overwrites_foreign(fake_client):
    foreign = '{"instance_id":"other-cp","run_id":"run-A","run_key":"k"}'
    fake_client.store[registry.owner_key("run-A")] = foreign
    assert registry.renew_owner("run-A", run_key="k") == "foreign"
    assert fake_client.store[registry.owner_key("run-A")] == foreign
    assert fake_client.expires == []


def test_release_owner_cas(fake_client):
    registry.register_owner("run-A", run_key="k")
    registry.release_owner("run-A", run_key="k")
    assert registry.owner_key("run-A") not in fake_client.store


# ── 配置/关闭 ───────────────────────────────────────────────────────────────


def test_configure_disabled_keeps_client_none(monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    registry.configure_console_registry("redis://localhost:6379/0")
    assert registry._client is None


def test_shutdown_closes_client(fake_client):
    registry.shutdown_console_registry()
    assert fake_client.closed is True
    assert registry._client is None


# ── 状态快照（P2）────────────────────────────────────────────────────────────


def test_publish_and_read_snapshot(fake_client):
    registry.publish_status_snapshot(
        "run-A", {"run_id": "run-A", "status": "RUNNING"}, ttl_seconds=45,
    )
    snap = registry.read_status_snapshot("run-A")
    assert snap is not None
    assert snap["status"] == "RUNNING"
    assert snap["instance_id"] == registry.control_plane_instance_id()
    assert (registry.status_key("run-A"), 45) in fake_client.expires


def test_read_snapshot_missing_or_corrupt(fake_client):
    assert registry.read_status_snapshot("nope") is None
    fake_client.store[registry.status_key("bad")] = "{not json"
    assert registry.read_status_snapshot("bad") is None
    fake_client.store[registry.status_key("list")] = "[1,2]"
    assert registry.read_status_snapshot("list") is None


def test_refresh_snapshot_ttl_true_then_false_when_missing(fake_client):
    registry.publish_status_snapshot("run-A", {"run_id": "run-A"}, ttl_seconds=45)
    assert registry.refresh_status_ttl("run-A", ttl_seconds=45) is True
    del fake_client.store[registry.status_key("run-A")]
    assert registry.refresh_status_ttl("run-A", ttl_seconds=45) is False


def test_delete_snapshot(fake_client):
    registry.publish_status_snapshot("run-A", {"run_id": "run-A"}, ttl_seconds=45)
    registry.delete_status_snapshot("run-A")
    assert registry.status_key("run-A") not in fake_client.store


def test_publish_snapshot_fails_closed_on_redis_error(fake_client):
    fake_client.fail = True
    with pytest.raises(registry.ConsoleRegistryUnavailable):
        registry.publish_status_snapshot("run-A", {"run_id": "run-A"}, ttl_seconds=45)


def test_snapshot_ops_tolerate_unavailable(fake_client):
    """读取/续期/删除在注册表不可用时不抛（best-effort 路径）。"""
    fake_client.fail = True
    assert registry.read_status_snapshot("run-A") is None
    assert registry.refresh_status_ttl("run-A", ttl_seconds=45) is False
    registry.delete_status_snapshot("run-A")  # 不抛
