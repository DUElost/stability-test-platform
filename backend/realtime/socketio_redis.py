"""SocketIO Redis client manager (ADR-0027 P3-2).

When multiple control-plane processes share rooms (dashboard broadcasts,
``agent:{host_id}`` control emits), python-socketio needs a shared
``AsyncRedisManager``. Single-process deployments leave the flag off and
keep the in-memory manager (zero Redis pub/sub overhead).

Limits (honest):
- Room fan-out works across instances once the adapter is on.
- ``call_agent_rpc`` still resolves ``host_id → sid`` from the **local**
  ``AgentNamespace`` map; multi-instance Agent RPC requires sticky
  sessions (or a later shared sid registry). Control emits via
  ``room=agent:{host_id}`` are already cross-process safe.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

import socketio

logger = logging.getLogger(__name__)

_FALSEY = frozenset({"0", "false", "False", "no", "NO", "off", "OFF"})
_DEFAULT_CHANNEL = "stp-socketio"


def socketio_redis_adapter_enabled() -> bool:
    """Opt-in. Default off so single-instance prod stays unchanged."""
    if os.getenv("TESTING") == "1":
        return False
    return os.getenv("STP_SOCKETIO_REDIS_ADAPTER", "0").strip() not in _FALSEY


def socketio_redis_channel() -> str:
    return os.getenv("STP_SOCKETIO_REDIS_CHANNEL", _DEFAULT_CHANNEL).strip() or _DEFAULT_CHANNEL


def socketio_redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0").strip()


def _redact_redis_url(url: str) -> str:
    """Strip password from redis URL for logs."""
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


def build_socketio_client_manager() -> Optional[Any]:
    """Return ``AsyncRedisManager`` when enabled, else ``None`` (in-memory)."""
    if not socketio_redis_adapter_enabled():
        return None

    url = socketio_redis_url()
    channel = socketio_redis_channel()
    manager = socketio.AsyncRedisManager(url, channel=channel)
    logger.info(
        "socketio_redis_adapter_enabled url=%s channel=%s",
        _redact_redis_url(url),
        channel,
    )
    return manager
