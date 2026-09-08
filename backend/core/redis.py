"""Redis URL helpers shared by control-plane components."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def redact_redis_url(url: str) -> str:
    """Strip password from a Redis URL before it is written to logs."""
    try:
        parts = urlsplit(url)
        if parts.password is None:
            return url
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        netloc = host
        if parts.username:
            netloc = f"{parts.username}:***@{host}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        return "<unparseable>"
