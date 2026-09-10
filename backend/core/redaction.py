# -*- coding: utf-8 -*-
"""Secret redaction for text that may cross a trust boundary.

R13-F02 (#1214): external-service failures (notably ``requests`` errors) can
embed the full request URL — including query credentials such as
``access_token`` / ``sign`` — into exception text. That text is stored in
action summaries, chat messages, and ultimately the LLM context, so it must be
sanitized before it leaves the failing call site.

This module is intentionally dependency-free and side-effect-free so both the
notification service and the AI assistant can share it.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit, urlunsplit

# Query keys whose value must never appear in logs/messages/LLM context.
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "access_token",
        "token",
        "authorization",
        "auth",
        "secret",
        "client_secret",
        "password",
        "passwd",
        "pwd",
        "key",
        "api_key",
        "apikey",
        "sign",
        "signature",
    }
)

_URL_RE = re.compile(r"https?://[^\s'\"<>()\]]+", re.IGNORECASE)
# Authorization: Bearer <x> / Authorization: <x>
_AUTH_HEADER_RE = re.compile(
    r"(authorization\s*[:=]\s*)(bearer\s+)?[^\s,;'\"]+", re.IGNORECASE
)

_REDACTED = "***"


def _redact_url(url: str) -> str:
    """Strip userinfo and sensitive query values, preserving the rest."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    netloc = parts.netloc
    if "@" in netloc:
        host = netloc.rsplit("@", 1)[1]
        netloc = f"{_REDACTED}@{host}" if host else _REDACTED
    if parts.query:
        redacted_pairs = [
            f"{k}={_REDACTED if k.lower() in _SENSITIVE_QUERY_KEYS else v}"
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
        ]
        query = "&".join(redacted_pairs)
    else:
        query = parts.query
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def redact_secrets(text: str) -> str:
    """Return ``text`` with URLs and auth headers stripped of credentials."""
    if not text:
        return text
    redacted = _URL_RE.sub(lambda m: _redact_url(m.group(0)), text)
    redacted = _AUTH_HEADER_RE.sub(lambda m: f"{m.group(1)}{m.group(2) or ''}{_REDACTED}", redacted)
    return redacted
