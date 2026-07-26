"""Rate limiting middleware for API protection.

客户端 IP 的取法(#81):
  只有当**直连对端**在可信代理白名单里时才采信 `X-Forwarded-For`,并从
  **右往左**取第一个非可信条目。nginx 用 `$proxy_add_x_forwarded_for`
  (= 已有 XFF + `$remote_addr`),所以最右侧才是它实际观察到的客户端;
  取最左侧仍可被伪造 —— 客户端自带 `X-Forwarded-For: fake`,经 nginx 后
  变成 `fake, <真实IP>`,最左侧就是攻击者写的值。

  默认白名单 = loopback,只覆盖 **systemd 部署**:那里 uvicorn 绑
  127.0.0.1,nginx 同机反代,对端就是 127.0.0.1。

  **其它拓扑必须显式配置 STP_TRUSTED_PROXIES**,默认值不够:
  - docker-compose:容器内 uvicorn 绑 0.0.0.0,nginx 走 Docker 网络访问
    `server:8000`,对端是 172.x 容器地址 → 不配的话 XFF 全被忽略,所有用户
    挤进同一个桶、互相把对方限流掉。compose 文件里已设好。
  - 独立 LB / 多层代理:把每一层的网段都列进来。
  故意不把 172.16.0.0/12 塞进全局默认 —— 那会让任何部署在私有网段的
  非代理主机都获得伪造 XFF 的能力。

已知限制:本限流器是**进程内**的。多 worker / 多副本下实际限额 = 配置值 ×
副本数。当前生产是 systemd 单进程 uvicorn(无 --workers),所以是准确的;
一旦加 worker 会静默放宽 N 倍且没有告警。要精确需挪到 Redis,见 #91。
"""
import ipaddress
import logging
import os
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from backend.core.metrics import rate_limiter_evicted_total

logger = logging.getLogger(__name__)

# Rate limit configuration
RATE_LIMIT_REQUESTS = 300  # requests
RATE_LIMIT_WINDOW = 60  # seconds

# 同时跟踪的 IP 上限。超出后按 LRU 淘汰 —— 防止(伪造或真实的)海量来源把
# 字典撑爆;此前 _clean_old_requests 只清空列表、从不删 key。
MAX_TRACKED_IPS = 20_000

# 淘汰日志的最小间隔(秒)。满容量时每个新来源都会触发淘汰,逐条打日志等于
# 把内存 DoS 换成日志 I/O DoS —— 精确计数交给
# stability_rate_limiter_evicted_total 指标,日志只做低频提示。
_EVICTION_LOG_INTERVAL_SECONDS = 60.0

DEFAULT_TRUSTED_PROXIES = "127.0.0.1/32,::1/128"

UNKNOWN_CLIENT = "unknown"


def _parse_networks(raw: str) -> Tuple[ipaddress._BaseNetwork, ...]:
    nets = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            nets.append(ipaddress.ip_network(token, strict=False))
        except ValueError:
            logger.warning("invalid_trusted_proxy_entry: %r (ignored)", token)
    return tuple(nets)


def get_trusted_proxies() -> Tuple[ipaddress._BaseNetwork, ...]:
    """可信代理网段。默认仅 loopback。

    设为空字符串 = 完全不信任 XFF(直接暴露在公网、没有反代时的正确选择)。
    """
    raw = os.getenv("STP_TRUSTED_PROXIES")
    if raw is None:
        raw = DEFAULT_TRUSTED_PROXIES
    return _parse_networks(raw)


def _is_trusted(ip_str: str, trusted: Tuple[ipaddress._BaseNetwork, ...]) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(addr in net for net in trusted)


def resolve_client_ip(
    peer: Optional[str],
    forwarded_for: Optional[str],
    trusted: Optional[Tuple[ipaddress._BaseNetwork, ...]] = None,
) -> str:
    """定位真实客户端 IP。

    对端不可信 → 直接用对端,**完全忽略** XFF(否则任何人都能改限流桶)。
    对端可信   → 从右往左找第一个非可信条目;全是可信代理时退回对端。
    """
    if trusted is None:
        trusted = get_trusted_proxies()

    if not peer:
        return UNKNOWN_CLIENT
    if not _is_trusted(peer, trusted):
        return peer

    for hop in reversed([h.strip() for h in (forwarded_for or "").split(",")]):
        if not hop:
            continue
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            # 非法条目(伪造/畸形)不能当作客户端标识
            continue
        if not _is_trusted(hop, trusted):
            return hop
    return peer


class RateLimiter:
    """Simple in-memory rate limiter."""

    def __init__(
        self,
        max_requests: int = RATE_LIMIT_REQUESTS,
        window_seconds: int = RATE_LIMIT_WINDOW,
        max_tracked_ips: int = MAX_TRACKED_IPS,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.max_tracked_ips = max_tracked_ips
        # LRU 序:最近活动的排在末尾,淘汰从头部 popitem —— O(1)。
        # 用普通 dict + 每次扫描/排序会让「满容量」本身变成攻击面:
        # 攻击者持续轮换真实源地址,每个请求都触发一次 O(n log n)。
        self._storage: "OrderedDict[str, List[float]]" = OrderedDict()
        self._last_eviction_log = 0.0
        self._evicted_since_log = 0

    def _clean_old_requests(self, ip: str, now: float) -> None:
        """Remove requests outside the time window.

        空列表会**连 key 一起删** —— 否则每个出现过的 IP 都永久占一条,
        配合 XFF 伪造就是无界增长。
        """
        stamps = self._storage.get(ip)
        if stamps is None:
            return
        cutoff = now - self.window_seconds
        kept = [ts for ts in stamps if ts > cutoff]
        if kept:
            self._storage[ip] = kept
        else:
            del self._storage[ip]

    def _evict_if_needed(self, now: float) -> None:
        """腾出一个槽位。O(1):直接弹出 LRU 头部。

        不在这里做「全表扫过期项」—— 那是 O(n),而满容量时每个新来源都会
        走到这里。过期项由 `_clean_old_requests` 在各自被访问时回收,
        或在此被 LRU 顺带淘汰(最久未活动的必然也是最可能过期的)。
        """
        evicted = 0
        while len(self._storage) >= self.max_tracked_ips:
            self._storage.popitem(last=False)
            evicted += 1
        if not evicted:
            return

        rate_limiter_evicted_total.inc(evicted)
        self._evicted_since_log += evicted
        # 日志限频:否则满容量下逐条打印,等于把内存 DoS 换成日志 I/O DoS
        if now - self._last_eviction_log >= _EVICTION_LOG_INTERVAL_SECONDS:
            logger.warning(
                "rate_limiter_evicting: tracked_ips=%d evicted_since_last_log=%d "
                "(高基数来源;若持续出现请检查是否遭遇伪造源地址攻击)",
                len(self._storage), self._evicted_since_log,
            )
            self._last_eviction_log = now
            self._evicted_since_log = 0

    def is_allowed(self, ip: str) -> Tuple[bool, int, int]:
        """Check if request is allowed.

        Returns:
            Tuple of (allowed, remaining_requests, reset_time)
        """
        now = time.time()
        self._clean_old_requests(ip, now)

        if ip not in self._storage:
            self._evict_if_needed(now)
            self._storage[ip] = []
        else:
            # 移到末尾 = 标记为最近活动,淘汰才是 LRU 而非 FIFO
            self._storage.move_to_end(ip)

        if len(self._storage[ip]) >= self.max_requests:
            reset_time = int(self._storage[ip][0] + self.window_seconds - now) if self._storage[ip] else 0
            return False, 0, max(0, reset_time)

        self._storage[ip].append(now)
        remaining = self.max_requests - len(self._storage[ip])
        reset_time = self.window_seconds
        return True, remaining, reset_time

    def get_limit_info(self, ip: str) -> Tuple[int, int, int]:
        """Get current limit info for an IP.

        Returns:
            Tuple of (current_requests, max_requests, reset_time)
        """
        now = time.time()
        self._clean_old_requests(ip, now)
        current = len(self._storage.get(ip, []))
        reset_time = int(self.window_seconds - (now % self.window_seconds))
        return current, self.max_requests, reset_time

    @property
    def tracked_ip_count(self) -> int:
        """当前跟踪的来源数 —— 供测试与可观测使用。"""
        return len(self._storage)


# Global rate limiter instance
rate_limiter = RateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for rate limiting."""

    _SKIP_EXACT = frozenset({"/", "/docs", "/openapi.json", "/health", "/redoc", "/metrics", "/metrics/health"})
    _SKIP_PREFIXES = (
        "/api/v1/heartbeat",
        # /api/v1/agent/jobs/ 已移出豁免清单：依赖 _verify_agent + lifespan fail-fast
        # 提供认证保护，限流作为第二道防线（300 req/min/IP）。
        "/ws/",
        "/ws",
    )

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for certain paths
        path = request.url.path
        if path in self._SKIP_EXACT or any(path.startswith(p) for p in self._SKIP_PREFIXES):
            return await call_next(request)

        ip = get_client_ip(request)

        # Check rate limit BEFORE processing the request
        allowed, remaining, reset_time = rate_limiter.is_allowed(ip)

        if not allowed:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": f"Rate limit exceeded. Try again in {reset_time} seconds."},
                headers={
                    "Retry-After": str(reset_time),
                    "X-RateLimit-Limit": str(RATE_LIMIT_REQUESTS),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_time),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_REQUESTS)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_time)
        return response


def get_client_ip(request: Request) -> str:
    """限流分桶用的客户端标识。见模块 docstring 的取法说明。"""
    return resolve_client_ip(
        request.client.host if request.client else None,
        request.headers.get("X-Forwarded-For"),
    )


def get_rate_limit_info(request: Request) -> Dict[str, int]:
    """Get rate limit info for the current request."""
    ip = get_client_ip(request)
    current, max_requests, reset_time = rate_limiter.get_limit_info(ip)
    return {
        "limit": max_requests,
        "remaining": max(0, max_requests - current),
        "reset": reset_time,
        "window": RATE_LIMIT_WINDOW,
    }
