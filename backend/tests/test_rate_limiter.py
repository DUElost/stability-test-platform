"""RateLimiter 回归测试(#81)。

此前该模块**零测试覆盖**,而它无条件挂在生产路径上(main.py 的
add_middleware)。两个缺陷因此长期没被发现:

  1. 无条件采信 X-Forwarded-For → 轮换该头即可完全绕过限流
  2. _clean_old_requests 只清空列表、从不删 key → 配合 1 就是无界内存增长
"""
from __future__ import annotations

import ipaddress

import pytest

from backend.core.limiter import (
    DEFAULT_TRUSTED_PROXIES,
    RateLimiter,
    _parse_networks,
    get_trusted_proxies,
    resolve_client_ip,
)

_LOOPBACK = _parse_networks(DEFAULT_TRUSTED_PROXIES)


# ── 客户端 IP 定位 ──────────────────────────────────────────────────────

def test_untrusted_peer_xff_is_ignored():
    """对端不可信时必须无视 XFF —— 否则任何人都能自选限流桶。"""
    assert resolve_client_ip("203.0.113.9", "1.2.3.4", _LOOPBACK) == "203.0.113.9"


def test_untrusted_peer_rotating_xff_maps_to_same_bucket():
    """绕过手法的直接回归:轮换 XFF 不应改变分桶。"""
    buckets = {
        resolve_client_ip("203.0.113.9", f"10.0.0.{i}", _LOOPBACK)
        for i in range(50)
    }
    assert buckets == {"203.0.113.9"}, "轮换 X-Forwarded-For 仍能绕过限流"


def test_trusted_peer_takes_rightmost_untrusted_hop():
    """nginx 用 $proxy_add_x_forwarded_for 追加 remote_addr → 最右侧才是真客户端。

    客户端自带 `X-Forwarded-For: 1.1.1.1`,经 nginx 后变成
    `1.1.1.1, <真实IP>`。取最左侧会拿到攻击者写的值。
    """
    assert resolve_client_ip("127.0.0.1", "1.1.1.1, 203.0.113.9", _LOOPBACK) == "203.0.113.9"


def test_trusted_peer_spoofed_leftmost_does_not_win():
    spoofed = {
        resolve_client_ip("127.0.0.1", f"{i}.{i}.{i}.{i}, 203.0.113.9", _LOOPBACK)
        for i in range(1, 40)
    }
    assert spoofed == {"203.0.113.9"}, "伪造的最左侧条目改变了分桶"


def test_trusted_peer_without_xff_uses_peer():
    assert resolve_client_ip("127.0.0.1", None, _LOOPBACK) == "127.0.0.1"


def test_all_hops_trusted_falls_back_to_peer():
    assert resolve_client_ip("127.0.0.1", "127.0.0.1", _LOOPBACK) == "127.0.0.1"


@pytest.mark.parametrize("garbage", ["not-an-ip", "', 'x", "999.999.999.999", ""])
def test_malformed_xff_entries_are_skipped(garbage: str):
    """畸形条目不能当作客户端标识(否则又是一个可控的分桶键)。"""
    assert resolve_client_ip("127.0.0.1", f"{garbage}, 203.0.113.9", _LOOPBACK) == "203.0.113.9"


def test_malformed_only_xff_falls_back_to_peer():
    assert resolve_client_ip("127.0.0.1", "not-an-ip", _LOOPBACK) == "127.0.0.1"


def test_missing_peer_is_labelled_unknown():
    assert resolve_client_ip(None, "1.2.3.4", _LOOPBACK) == "unknown"


def test_ipv6_loopback_is_trusted_by_default():
    assert resolve_client_ip("::1", "2001:db8::5", _LOOPBACK) == "2001:db8::5"


# ── 可信代理配置 ────────────────────────────────────────────────────────

def test_default_trusted_proxies_is_loopback_only(monkeypatch):
    monkeypatch.delenv("STP_TRUSTED_PROXIES", raising=False)
    nets = get_trusted_proxies()
    assert ipaddress.ip_address("127.0.0.1") in nets[0]
    assert not any(ipaddress.ip_address("10.0.0.1") in n for n in nets)


def test_empty_env_disables_xff_trust_entirely(monkeypatch):
    """没有反代、直接暴露时的正确配置:一律不信 XFF。"""
    monkeypatch.setenv("STP_TRUSTED_PROXIES", "")
    assert resolve_client_ip("127.0.0.1", "1.2.3.4", get_trusted_proxies()) == "127.0.0.1"


def test_cidr_and_invalid_entries(monkeypatch):
    monkeypatch.setenv("STP_TRUSTED_PROXIES", "10.0.0.0/8, garbage, 127.0.0.1/32")
    nets = get_trusted_proxies()
    # 非法条目被忽略,合法的仍生效
    assert resolve_client_ip("10.1.2.3", "203.0.113.9", nets) == "203.0.113.9"
    assert resolve_client_ip("192.0.2.1", "203.0.113.9", nets) == "192.0.2.1"


# ── 限流与内存 ──────────────────────────────────────────────────────────

def test_limit_enforced_per_ip():
    rl = RateLimiter(max_requests=3, window_seconds=60)
    assert [rl.is_allowed("1.1.1.1")[0] for _ in range(4)] == [True, True, True, False]
    # 另一个 IP 不受影响
    assert rl.is_allowed("2.2.2.2")[0] is True


def test_expired_entries_release_the_key():
    """过期后必须连 key 一起删 —— 否则每个出现过的 IP 永久占一条。"""
    rl = RateLimiter(max_requests=5, window_seconds=60)
    rl.is_allowed("1.1.1.1")
    assert rl.tracked_ip_count == 1

    # 把时间戳推到窗口之外
    rl._storage["1.1.1.1"] = [rl._storage["1.1.1.1"][0] - 3600]
    rl._clean_old_requests("1.1.1.1", __import__("time").time())
    assert rl.tracked_ip_count == 0, "过期条目残留,内存无法回收"


def test_tracked_ips_are_capped():
    """海量来源不应无界增长(伪造 XFF 或真实分布式流量)。"""
    cap = 50
    rl = RateLimiter(max_requests=5, window_seconds=60, max_tracked_ips=cap)
    for i in range(cap * 4):
        rl.is_allowed(f"10.{i // 256}.{i % 256}.1")
    assert rl.tracked_ip_count <= cap, f"跟踪数 {rl.tracked_ip_count} 超出上限 {cap}"


def test_window_rollover_allows_again():
    rl = RateLimiter(max_requests=2, window_seconds=60)
    assert rl.is_allowed("1.1.1.1")[0] is True
    assert rl.is_allowed("1.1.1.1")[0] is True
    assert rl.is_allowed("1.1.1.1")[0] is False
    # 时间戳移出窗口后重新放行
    rl._storage["1.1.1.1"] = [t - 3600 for t in rl._storage["1.1.1.1"]]
    assert rl.is_allowed("1.1.1.1")[0] is True
