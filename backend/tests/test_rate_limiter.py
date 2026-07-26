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


def test_eviction_is_scale_invariant():
    """满容量后的单请求开销必须与表大小无关。

    第一版用「全表扫过期 + 排序」淘汰,满容量时每个新来源都触发一次
    O(n log n) —— 实测 1.96ms/请求。这等于把内存 DoS 换成 CPU DoS,
    而且正好发生在内存防护要防的同一场景里(攻击者轮换源地址)。
    这里用两种表大小对比单请求耗时,比值失控即说明退化回了 O(n log n)。
    """
    import time as _time

    def per_request_cost(cap: int) -> float:
        rl = RateLimiter(max_requests=5, window_seconds=60, max_tracked_ips=cap)
        for i in range(cap):
            rl.is_allowed(f"10.{i // 65536 % 256}.{i // 256 % 256}.{i % 256}")
        probes = 300
        t0 = _time.perf_counter()
        for i in range(probes):
            rl.is_allowed(f"203.0.113.{i % 256}-{i}")
        return (_time.perf_counter() - t0) / probes

    small = per_request_cost(1_000)
    large = per_request_cost(16_000)
    # O(1) 时比值≈1;O(n log n) 时 16 倍表大小会带来十几倍差距。
    # 阈值放宽到 6 以吸收计时噪声,仍能抓住数量级退化。
    assert large < small * 6, (
        f"淘汰开销随表大小增长(small={small*1e6:.1f}µs large={large*1e6:.1f}µs),"
        "疑似退化为全表扫描/排序"
    )


def test_eviction_logging_is_throttled(caplog):
    """满容量下逐条打日志 = 把内存 DoS 换成日志 I/O DoS。"""
    import logging as _logging

    cap = 100
    rl = RateLimiter(max_requests=5, window_seconds=60, max_tracked_ips=cap)
    for i in range(cap):
        rl.is_allowed(f"10.0.{i // 256}.{i % 256}")

    with caplog.at_level(_logging.WARNING, logger="backend.core.limiter"):
        for i in range(500):
            rl.is_allowed(f"203.0.113.{i % 256}-{i}")

    # 统计该 logger 的全部 WARNING,不匹配具体字符串 —— 否则改了日志文案就会
    # 变成空匹配的假通过(第一版就踩了这个:旧代码写 evicted、断言写 evicting)
    evict_logs = [
        r for r in caplog.records
        if r.name == "backend.core.limiter" and r.levelno >= _logging.WARNING
    ]
    assert len(evict_logs) <= 2, f"500 次淘汰打了 {len(evict_logs)} 条日志,限频失效"


def test_lru_keeps_recently_active_ip():
    """淘汰顺序应是 LRU 而非 FIFO —— 活跃来源不该被新来源挤掉。"""
    cap = 10
    rl = RateLimiter(max_requests=100, window_seconds=60, max_tracked_ips=cap)
    for i in range(cap - 1):
        rl.is_allowed(f"10.0.0.{i}")

    # 持续访问 10.0.0.0,同时灌入新来源
    for i in range(50):
        rl.is_allowed("10.0.0.0")
        rl.is_allowed(f"203.0.113.{i}")

    assert "10.0.0.0" in rl._storage, "活跃 IP 被当作最旧条目淘汰(FIFO 而非 LRU)"


# ── 部署拓扑 ────────────────────────────────────────────────────────────

def _compose_trusted_proxies() -> str:
    """读出 docker-compose.yml 里 STP_TRUSTED_PROXIES 的**实际默认值**。

    必须解析 `${VAR:-default}` 取到 default —— 只断言 key 存在的话,
    把值改成 loopback-only 或垃圾串测试照样全绿(第一版就是这样)。
    """
    import pathlib
    import re

    import yaml

    compose = yaml.safe_load(
        (pathlib.Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text()
    )
    raw = compose["services"]["server"]["environment"]["STP_TRUSTED_PROXIES"]
    m = re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-(?P<default>.*)\}", str(raw).strip())
    return m.group("default") if m else str(raw).strip()


def test_docker_compose_sets_trusted_proxies():
    """Compose 拓扑与 systemd 不同,必须显式配置,否则所有用户共用一个桶。

    容器内 uvicorn 绑 0.0.0.0,nginx 走 Docker 网络访问 server:8000,
    对端是 172.x 而非 127.0.0.1 —— 代码默认的 loopback 白名单覆盖不到。
    """
    import pathlib

    import yaml

    compose = yaml.safe_load(
        (pathlib.Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text()
    )
    env = compose["services"]["server"]["environment"]
    assert "STP_TRUSTED_PROXIES" in env, (
        "docker-compose 未设 STP_TRUSTED_PROXIES;该拓扑下 XFF 会被一律忽略,"
        "所有用户挤进同一个限流桶"
    )


def test_compose_value_actually_covers_docker_bridge():
    """用 **compose 里的真实取值** 构造白名单,验证 172.x 对端能解析出客户端。

    这条才是真正的回归防线:把 compose 的值改成 loopback-only 或垃圾串,
    这里会立刻失败。上面那条只查 key 在不在,改坏值照样通过。
    """
    nets = _parse_networks(_compose_trusted_proxies())
    assert nets, f"compose 的 STP_TRUSTED_PROXIES 解析不出任何网段: {_compose_trusted_proxies()!r}"

    # Docker 默认 bridge 网段上的 nginx 容器
    assert resolve_client_ip("172.20.0.4", "203.0.113.7", nets) == "203.0.113.7"
    assert resolve_client_ip("172.20.0.4", "203.0.113.8", nets) == "203.0.113.8"
    # 同机 systemd 场景也仍要覆盖
    assert resolve_client_ip("127.0.0.1", "203.0.113.9", nets) == "203.0.113.9"
    # 但不该把 Docker 网段之外的私有地址也放进来
    assert resolve_client_ip("10.1.2.3", "203.0.113.7", nets) == "10.1.2.3"


def test_docker_network_peer_resolves_client_from_xff():
    """算法层面的对照:显式白名单下 172.x 对端解析正确(不依赖 compose 文件)。"""
    nets = _parse_networks("127.0.0.1/32,::1/128,172.16.0.0/12")
    assert resolve_client_ip("172.20.0.4", "203.0.113.7", nets) == "203.0.113.7"
    assert resolve_client_ip("172.20.0.4", "203.0.113.8", nets) == "203.0.113.8"
    # 未列入白名单的私有地址仍不被信任
    assert resolve_client_ip("10.1.2.3", "203.0.113.7", nets) == "10.1.2.3"


def test_default_whitelist_excludes_docker_range():
    """全局默认**不得**包含 172.16/12 —— 否则私有网段上任何主机都能伪造 XFF。"""
    assert resolve_client_ip("172.20.0.4", "1.2.3.4", _LOOPBACK) == "172.20.0.4"


def test_window_rollover_allows_again():
    rl = RateLimiter(max_requests=2, window_seconds=60)
    assert rl.is_allowed("1.1.1.1")[0] is True
    assert rl.is_allowed("1.1.1.1")[0] is True
    assert rl.is_allowed("1.1.1.1")[0] is False
    # 时间戳移出窗口后重新放行
    rl._storage["1.1.1.1"] = [t - 3600 for t in rl._storage["1.1.1.1"]]
    assert rl.is_allowed("1.1.1.1")[0] is True
