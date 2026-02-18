"""Rate limiting middleware for API protection."""
import time
from typing import Dict, Optional, Tuple

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# Rate limit configuration
RATE_LIMIT_REQUESTS = 100  # requests
RATE_LIMIT_WINDOW = 60  # seconds


class RateLimiter:
    """Simple in-memory rate limiter."""

    def __init__(self, max_requests: int = RATE_LIMIT_REQUESTS, window_seconds: int = RATE_LIMIT_WINDOW):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._storage: Dict[str, list] = {}  # ip -> list of timestamps

    def _clean_old_requests(self, ip: str, now: float) -> None:
        """Remove requests outside the time window."""
        if ip not in self._storage:
            return
        cutoff = now - self.window_seconds
        self._storage[ip] = [ts for ts in self._storage[ip] if ts > cutoff]

    def is_allowed(self, ip: str) -> Tuple[bool, int, int]:
        """Check if request is allowed.

        Returns:
            Tuple of (allowed, remaining_requests, reset_time)
        """
        now = time.time()
        self._clean_old_requests(ip, now)

        if ip not in self._storage:
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


# Global rate limiter instance
rate_limiter = RateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for rate limiting."""

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for certain paths
        path = request.url.path
        _SKIP_EXACT = {"/", "/docs", "/openapi.json", "/health", "/redoc"}
        _SKIP_PREFIXES = ("/api/v1/heartbeat", "/api/v1/agent/", "/ws/", "/ws")
        if path in _SKIP_EXACT or any(path.startswith(p) for p in _SKIP_PREFIXES):
            return await call_next(request)

        # Get client IP
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else "unknown"

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


def get_rate_limit_info(request: Request) -> Dict[str, int]:
    """Get rate limit info for the current request."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    else:
        ip = request.client.host if request.client else "unknown"

    current, max_requests, reset_time = rate_limiter.get_limit_info(ip)
    return {
        "limit": max_requests,
        "remaining": max(0, max_requests - current),
        "reset": reset_time,
        "window": RATE_LIMIT_WINDOW,
    }
