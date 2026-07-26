"""Rate limiting middleware for API protection.

客户端 IP 的取法(#81):
  只有当**直连对端**在可信代理白名单里时才采信 `X-Forwarded-For`,并从
  **右往左**取第一个非可信条目。nginx 用 `$proxy_add_x_forwarded_for`
  (= 已有 XFF + `$remote_addr`),所以最右侧才是它实际观察到的客户端;
  取最左侧仍可被伪造 —— 客户端自带 `X-Forwarded-For: fake`,经 nginx 后
  变成 `fake, <真实IP>`,最左侧就是攻击者写的值。

  生产拓扑:uvicorn 只绑 127.0.0.1(systemd/docker-compose 均如此),
  全部流量经 nginx 抵达,所以默认白名单 = loopback 即可覆盖,不会误伤。
  如需在其它拓扑下部署(如独立 LB),用 STP_TRUSTED_PROXIES 覆盖。

已知限制:本限流器是**进程内**的。多 worker / 多副本下实际限额 = 配置值 ×
副本数。要精确需挪到 Redis(SAQ 已在用),见 #81 讨论。
"""
import ipaddress
import logging
import os
import time
from typing import Dict, List, Optional, Tuple

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# Rate limit configuration
RATE_LIMIT_REQUESTS = 300  # requests
RATE_LIMIT_WINDOW = 60  # seconds

# 同时跟踪的 IP 上限。超出后按最早活动时间淘汰 —— 防止(伪造或真实的)海量
# 来源把字典撑爆;此前 _clean_old_requests 只清空列表、从不删 key。
MAX_TRACKED_IPS = 20_000

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
        self._storage: Dict[str, List[float]] = {}  # ip -> list of timestamps

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
        if len(self._storage) < self.max_tracked_ips:
            return
        cutoff = now - self.window_seconds
        stale = [ip for ip, ts in self._storage.items() if not ts or ts[-1] <= cutoff]
        for ip in stale:
            self._storage.pop(ip, None)
        if len(self._storage) < self.max_tracked_ips:
            return
        # 仍然超限:按最近活动时间淘汰最旧的一批,给新来源腾位置
        overflow = len(self._storage) - self.max_tracked_ips + 1
        oldest = sorted(self._storage.items(), key=lambda kv: kv[1][-1])[:overflow]
        for ip, _ in oldest:
            self._storage.pop(ip, None)
        logger.warning(
            "rate_limiter_evicted: tracked_ips=%d evicted=%d",
            len(self._storage), len(oldest) + len(stale),
        )

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
