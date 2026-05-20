"""Origin/Referer CSRF middleware for cookie-based browser sessions.

Why: 浏览器 cookie 会话切换后,所有 cookie-authed 写操作天然暴露在 CSRF 风险面。
     SameSite=lax 默认值是基础防御,但不阻止 same-site 跨子域 / 富文本编辑器 / 旧浏览器。
     用 Origin/Referer 同源白名单做第二层硬护栏,白名单复用 CORS_ORIGINS 不双维护。

判定顺序(任一命中放行,否则 403):
  1) method in {GET, HEAD, OPTIONS, TRACE}    → 只读 / preflight
  2) Authorization: Bearer ...                → 浏览器不会跨站自动重放 Bearer
  3) X-Agent-Secret header                    → Agent / server-to-server
  4) Origin header in allowed_origins         → 严格 string match
  5) Referer header → scheme://host[:port] in allowed_origins → 命中

降级:STP_CSRF_ENABLED=0 / false / no 关闭整体中间件,便于排障。
"""
from __future__ import annotations

import os
from typing import Iterable, Optional
from urllib.parse import urlparse

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
DEFAULT_PROTECTED_PREFIX = "/api/v1/"


def is_csrf_enabled() -> bool:
    raw = os.getenv("STP_CSRF_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _origin_from_url(url: str) -> Optional[str]:
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


class CSRFOriginMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_origins: Iterable[str],
        enabled: bool = True,
        protected_prefix: str = DEFAULT_PROTECTED_PREFIX,
    ) -> None:
        self.app = app
        self.allowed = frozenset(allowed_origins)
        self.enabled = enabled
        self.protected_prefix = protected_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.enabled:
            return await self.app(scope, receive, send)

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")

        if method in SAFE_METHODS or not path.startswith(self.protected_prefix):
            return await self.app(scope, receive, send)

        headers = Headers(scope=scope)

        # 非浏览器凭据:Bearer / Agent secret 一律放行
        auth = headers.get("authorization", "")
        if auth.startswith("Bearer "):
            return await self.app(scope, receive, send)
        if headers.get("x-agent-secret"):
            return await self.app(scope, receive, send)

        origin = headers.get("origin")
        if origin and origin != "null":
            if origin in self.allowed:
                return await self.app(scope, receive, send)
        else:
            referer = headers.get("referer")
            if referer:
                normalized = _origin_from_url(referer)
                if normalized and normalized in self.allowed:
                    return await self.app(scope, receive, send)

        response = JSONResponse(
            {"detail": "CSRF check failed"},
            status_code=403,
        )
        await response(scope, receive, send)
