"""限流中间件与 RateLimiter（#3297 自 core.limiter 迁出，C4 棘轮出口）。

本模块是 HTTP 面（fastapi/starlette 的 Request/JSONResponse/BaseHTTPMiddleware），
因此住 api 层；纯客户端 IP 解析（resolve_client_ip / get_trusted_proxies）留在
`backend/core/limiter.py`——审计写入（services 层）也要用它，core→api 是 C1 反向。

已知限制:本限流器是**进程内**的。多 worker / 多副本下实际限额 = 配置值 ×
副本数。当前生产是 systemd 单进程 uvicorn(无 --workers),所以是准确的;
一旦加 worker 会静默放宽 N 倍且没有告警。要精确需挪到 Redis,见 #91。

#2324：UI 与 Agent 分桶（``STP_UI_RATE_LIMIT_REQUESTS`` /
``STP_AGENT_RATE_LIMIT_REQUESTS``）。``/api/v1/agent/`` 走 Agent 桶，其余走 UI
桶；``/api/v1/heartbeat`` 仍豁免。响应头 ``X-RateLimit-Bucket`` 标明命中桶。
"""
import logging
import os
import time
from collections import OrderedDict
from typing import Dict, List, Tuple

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from backend.core.limiter import resolve_client_ip
from backend.core.metrics import rate_limiter_evicted_total

logger = logging.getLogger(__name__)

# Rate limit configuration (#2324 UI vs Agent buckets)
UI_RATE_LIMIT_REQUESTS = int(os.getenv("STP_UI_RATE_LIMIT_REQUESTS", "300"))
AGENT_RATE_LIMIT_REQUESTS = int(os.getenv("STP_AGENT_RATE_LIMIT_REQUESTS", "2000"))
RATE_LIMIT_REQUESTS = UI_RATE_LIMIT_REQUESTS  # backwards-compatible alias
RATE_LIMIT_WINDOW = 60  # seconds

# 同时跟踪的 IP 上限。超出后按 LRU 淘汰 —— 防止(伪造或真实的)海量来源把
# 字典撑爆;此前 _clean_old_requests 只清空列表、从不删 key。
MAX_TRACKED_IPS = 20_000

# 淘汰日志的最小间隔(秒)。满容量时每个新来源都会触发淘汰,逐条打日志等于
# 把内存 DoS 换成日志 I/O DoS —— 精确计数交给
# stability_rate_limiter_evicted_total 指标,日志只做低频提示。
_EVICTION_LOG_INTERVAL_SECONDS = 60.0


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


# Global rate limiter instances (#2324)
rate_limiter = RateLimiter(max_requests=UI_RATE_LIMIT_REQUESTS)
agent_rate_limiter = RateLimiter(max_requests=AGENT_RATE_LIMIT_REQUESTS)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for rate limiting."""

    _SKIP_EXACT = frozenset({"/", "/docs", "/openapi.json", "/health", "/redoc", "/metrics", "/metrics/health"})
    _SKIP_PREFIXES = (
        "/api/v1/heartbeat",
        # /api/v1/agent/jobs/ 已移出豁免清单：依赖 _verify_agent + lifespan fail-fast
        # 提供认证保护，限流作为第二道防线（Agent 桶，默认 2000 req/min/IP，#2324）。
        "/ws/",
        "/ws",
    )

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for certain paths
        path = request.url.path
        if path in self._SKIP_EXACT or any(path.startswith(p) for p in self._SKIP_PREFIXES):
            return await call_next(request)

        ip = get_client_ip(request)

        if path.startswith("/api/v1/agent/"):
            limiter = agent_rate_limiter
            limit = AGENT_RATE_LIMIT_REQUESTS
            bucket = "agent"
        else:
            limiter = rate_limiter
            limit = UI_RATE_LIMIT_REQUESTS
            bucket = "ui"

        # Check rate limit BEFORE processing the request
        allowed, remaining, reset_time = limiter.is_allowed(ip)

        if not allowed:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": f"Rate limit exceeded. Try again in {reset_time} seconds."},
                headers={
                    "Retry-After": str(reset_time),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_time),
                    "X-RateLimit-Bucket": bucket,
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_time)
        response.headers["X-RateLimit-Bucket"] = bucket
        return response


def get_client_ip(request: Request) -> str:
    """限流分桶用的客户端标识。取法说明见 backend/core/limiter.py 模块 docstring。"""
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
