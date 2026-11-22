"""B10 U3 — 控制面 Redis 传输并发许可（服务层 + 反例）。

覆盖 #4086 §4 U3：并发上限、release 放行、错误 token 拒释、TTL 回收、renew 延期。
使用内存 FakeRedis，不连生产 Redis / PG。
"""

from __future__ import annotations

import time

import pytest

from backend.core.settings.transfer import reset_transfer_permit_settings_cache
from backend.services import transfer_permit as tp


class _FakeRedis:
    """同步参考语义 fake：实现本模块 Lua 脚本的单线程原子语义。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.expiry: dict[str, float] = {}
        self.fail = False
        self.now = time.monotonic()

    def _purge_if_expired(self, key: str) -> None:
        exp = self.expiry.get(key)
        if exp is not None and self.now >= exp:
            self.store.pop(key, None)
            self.expiry.pop(key, None)

    def _exists(self, key: str) -> int:
        self._purge_if_expired(key)
        return 1 if key in self.store else 0

    def close(self) -> None:
        return None

    def eval(self, script: str, numkeys: int, *args):  # noqa: ANN001
        if self.fail:
            raise RuntimeError("redis down")
        keys = list(args[:numkeys])
        argv = list(args[numkeys:])

        if "TRANSFER_PERMIT_ACQUIRE" in script:
            holders = keys[0]
            max_hosts = int(argv[0])
            token = str(argv[1])
            host_id = str(argv[2])
            ttl = int(argv[3])
            slot_prefix = str(argv[4])
            members = set(self.sets.get(holders, set()))
            for m in list(members):
                if self._exists(slot_prefix + m) == 0:
                    members.discard(m)
            self.sets[holders] = members
            slot = slot_prefix + token
            current = None
            if self._exists(slot):
                current = self.store.get(slot)
            if current is not None:
                if current != host_id:
                    return [0, -1]
                self.expiry[slot] = self.now + ttl
                members.add(token)
                self.sets[holders] = members
                return [1, len(members)]
            count = len(members)
            if count >= max_hosts:
                return [0, count]
            self.store[slot] = host_id
            self.expiry[slot] = self.now + ttl
            members.add(token)
            self.sets[holders] = members
            return [1, count + 1]

        if "TRANSFER_PERMIT_RENEW" in script:
            slot, holders = keys[0], keys[1]
            expected_host, ttl, token = str(argv[0]), int(argv[1]), str(argv[2])
            if self._exists(slot) and self.store.get(slot) == expected_host:
                self.expiry[slot] = self.now + ttl
                self.sets.setdefault(holders, set()).add(token)
                return 1
            return 0

        if "TRANSFER_PERMIT_RELEASE" in script:
            slot, holders = keys[0], keys[1]
            expected_host, token = str(argv[0]), str(argv[1])
            if self._exists(slot) and self.store.get(slot) == expected_host:
                self.store.pop(slot, None)
                self.expiry.pop(slot, None)
                self.sets.setdefault(holders, set()).discard(token)
                return 1
            return 0

        raise AssertionError(f"unexpected script: {script[:40]!r}")


@pytest.fixture()
def fake_redis(monkeypatch):
    monkeypatch.setenv("STP_FIRMWARE_TRANSFER_MAX_HOSTS", "2")
    monkeypatch.setenv("STP_FIRMWARE_TRANSFER_PERMIT_TTL_SECONDS", "60")
    reset_transfer_permit_settings_cache()
    client = _FakeRedis()
    tp.configure_transfer_permit_client(client)
    yield client
    tp.reset_transfer_permit_for_tests()
    reset_transfer_permit_settings_cache()


def test_acquire_allows_two_rejects_third(fake_redis):
    a = tp.acquire_permit(host_id="h1", lease_token="t1")
    b = tp.acquire_permit(host_id="h2", lease_token="t2")
    c = tp.acquire_permit(host_id="h3", lease_token="t3")
    assert a.acquired and b.acquired
    assert not c.acquired
    assert c.active_count == 2
    assert c.max_hosts == 2


def test_release_frees_slot_for_waiter(fake_redis):
    tp.acquire_permit(host_id="h1", lease_token="t1")
    tp.acquire_permit(host_id="h2", lease_token="t2")
    blocked = tp.acquire_permit(host_id="h3", lease_token="t3")
    assert not blocked.acquired
    tp.release_permit(host_id="h1", lease_token="t1")
    granted = tp.acquire_permit(host_id="h3", lease_token="t3")
    assert granted.acquired


def test_wrong_token_renew_and_release_rejected(fake_redis):
    tp.acquire_permit(host_id="h1", lease_token="t1")
    tp.acquire_permit(host_id="h2", lease_token="t2")
    with pytest.raises(tp.TransferPermitError) as renew_exc:
        tp.renew_permit(host_id="h1", lease_token="stale-token")
    assert renew_exc.value.code == "LEASE_TOKEN_MISMATCH"
    with pytest.raises(tp.TransferPermitError) as release_exc:
        tp.release_permit(host_id="h1", lease_token="stale-token")
    assert release_exc.value.code == "LEASE_TOKEN_MISMATCH"
    # 现役槽位仍在：第三台仍应排队
    blocked = tp.acquire_permit(host_id="h3", lease_token="t3")
    assert not blocked.acquired
    # 正确 release 仍可用
    tp.release_permit(host_id="h1", lease_token="t1")
    assert tp.acquire_permit(host_id="h3", lease_token="t3").acquired


def test_ttl_expiry_auto_reclaims_slot(fake_redis):
    monkey_ttl = fake_redis
    r1 = tp.acquire_permit(host_id="h1", lease_token="t1", ttl_seconds=5)
    r2 = tp.acquire_permit(host_id="h2", lease_token="t2", ttl_seconds=5)
    assert r1.acquired and r2.acquired
    assert not tp.acquire_permit(host_id="h3", lease_token="t3").acquired
    # 模拟持有者崩溃：不 release，推进时钟越过 TTL
    monkey_ttl.now += 6
    reclaimed = tp.acquire_permit(host_id="h3", lease_token="t3", ttl_seconds=5)
    assert reclaimed.acquired


def test_renew_extends_ttl(fake_redis):
    tp.acquire_permit(host_id="h1", lease_token="t1", ttl_seconds=10)
    slot = tp.slot_key("firmware_nfs", "t1")
    first_expiry = fake_redis.expiry[slot]
    fake_redis.now += 4
    ttl = tp.renew_permit(host_id="h1", lease_token="t1", ttl_seconds=10)
    assert ttl == 10
    assert fake_redis.expiry[slot] == pytest.approx(fake_redis.now + 10)
    assert fake_redis.expiry[slot] > first_expiry


def test_wrong_host_id_on_matching_token_rejected(fake_redis):
    tp.acquire_permit(host_id="h1", lease_token="t1")
    with pytest.raises(tp.TransferPermitError) as exc:
        tp.release_permit(host_id="other", lease_token="t1")
    assert exc.value.code == "LEASE_TOKEN_MISMATCH"
    # 原持有者仍可释放
    tp.release_permit(host_id="h1", lease_token="t1")


def test_unavailable_when_redis_down(fake_redis):
    fake_redis.fail = True
    with pytest.raises(tp.TransferPermitUnavailable):
        tp.acquire_permit(host_id="h1", lease_token="t1")
