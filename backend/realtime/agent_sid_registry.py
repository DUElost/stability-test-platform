"""Shared Agent ``host_id → owner`` registry (ADR-0027 P3-3).

When multiple control-plane processes accept Agent WebSockets, each process
only has a local ``_host_to_sid`` map. This module stores a short-lived Redis
pointer so other instances can:

1. Decide whether *any* process currently owns the Agent (avoid hanging RPC).
2. Route ``call_agent_rpc`` via the ``agent:{host_id}`` room + Redis adapter
   instead of requiring LB sticky sessions.

Gating:
- ``STP_AGENT_SID_REGISTRY=1`` → on (requires reachable Redis).
- Default: follow ``STP_SOCKETIO_REDIS_ADAPTER`` (registry on iff adapter on).
- ``TESTING=1`` → always off.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

_FALSEY = frozenset({"0", "false", "False", "no", "NO", "off", "OFF"})
_OWNER_KEY_PREFIX = "stp:agent:owner:"
_DEFAULT_TTL_SECONDS = 120

_redis: Any = None
_INSTANCE_ID = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"


def control_plane_instance_id() -> str:
    return _INSTANCE_ID


def agent_sid_registry_enabled() -> bool:
    if os.getenv("TESTING") == "1":
        return False
    explicit = os.getenv("STP_AGENT_SID_REGISTRY", "").strip()
    if explicit:
        return explicit not in _FALSEY
    from backend.realtime.socketio_redis import socketio_redis_adapter_enabled

    return socketio_redis_adapter_enabled()


def owner_key(host_id: str) -> str:
    return f"{_OWNER_KEY_PREFIX}{host_id}"


def _owner_payload(host_id: str, sid: str) -> str:
    """确定性 owner payload：键序固定，整串比对可逐字节复现（#887）。"""
    return json.dumps(
        {
            "instance_id": _INSTANCE_ID,
            "sid": sid,
            "host_id": str(host_id),
        },
        separators=(",", ":"),
    )


# 原子 compare-and-delete / compare-and-expire（#887）：此前 GET→比较→写之间
# 存在异步让出点，期间新连接 register 覆盖 key 后，旧连接的无条件 DELETE 会
# 丢掉新登记、renew 会把旧 payload 续期写回同样覆盖新登记。Lua 在 Redis 单线
# 程内原子执行比较与写，杜绝让出窗口。
_CAS_DELETE_LUA = """
local current = redis.call("GET", KEYS[1])
if current == ARGV[1] then
  return redis.call("DEL", KEYS[1])
end
return 0
"""
# #1113: missing key + live renew → rebuild; matching → expire; foreign → 0.
# Marker ``RENEW_OR_REBUILD`` keeps fakes from confusing this with plain EXPIRE.
_CAS_RENEW_OR_REBUILD_LUA = """
-- RENEW_OR_REBUILD
local current = redis.call("GET", KEYS[1])
if (not current) then
  redis.call("SET", KEYS[1], ARGV[1], "EX", tonumber(ARGV[2]))
  return 1
end
if current == ARGV[1] then
  return redis.call("EXPIRE", KEYS[1], ARGV[2])
end
return 0
"""


def owner_ttl_seconds() -> int:
    raw = os.getenv("STP_AGENT_SID_REGISTRY_TTL_SECONDS", str(_DEFAULT_TTL_SECONDS))
    try:
        return max(30, int(raw))
    except ValueError:
        return _DEFAULT_TTL_SECONDS


def configure_agent_sid_registry(redis_client: Any) -> None:
    """Bind the process Redis client (called from FastAPI lifespan)."""
    global _redis
    _redis = redis_client
    if agent_sid_registry_enabled():
        logger.info(
            "agent_sid_registry_configured instance_id=%s ttl=%ss",
            _INSTANCE_ID,
            owner_ttl_seconds(),
        )


def _client() -> Any:
    return _redis


async def register_agent_owner(host_id: str, sid: str) -> None:
    """Record that this process owns ``host_id``'s Agent socket."""
    if not agent_sid_registry_enabled():
        return
    client = _client()
    if client is None:
        return
    payload = _owner_payload(str(host_id), sid)
    try:
        await client.set(owner_key(str(host_id)), payload, ex=owner_ttl_seconds())
    except Exception:
        logger.debug(
            "agent_sid_registry_register_failed host_id=%s",
            host_id,
            exc_info=True,
        )


async def renew_agent_owner(host_id: str, sid: str) -> bool:
    """Renew TTL for an active local connection, or rebuild if the key is gone.

    Called from the owner process on live-connection signals (Agent heartbeat).

    - Matching payload → ``EXPIRE`` (#881 / #887 CAS).
    - Missing key → ``SET`` this process's payload (#1113: Redis loss / TTL
      expiry while the Socket.IO connection is still alive).
    - Foreign payload → no-op (never overwrite another owner).

    Returns ``True`` when a renewal or rebuild happened.
    """
    if not agent_sid_registry_enabled():
        return False
    client = _client()
    if client is None:
        return False
    key = owner_key(str(host_id))
    try:
        result = await client.eval(
            _CAS_RENEW_OR_REBUILD_LUA,
            1,
            key,
            _owner_payload(str(host_id), sid),
            owner_ttl_seconds(),
        )
        return int(result) == 1
    except Exception:
        logger.debug(
            "agent_sid_registry_renew_failed host_id=%s",
            host_id,
            exc_info=True,
        )
        return False


async def unregister_agent_owner(host_id: str, sid: str) -> None:
    """Clear ownership only if we still own the same sid (#887: atomically)."""
    if not agent_sid_registry_enabled():
        return
    client = _client()
    if client is None:
        return
    key = owner_key(str(host_id))
    try:
        await client.eval(
            _CAS_DELETE_LUA, 1, key, _owner_payload(str(host_id), sid)
        )
    except Exception:
        logger.debug(
            "agent_sid_registry_unregister_failed host_id=%s",
            host_id,
            exc_info=True,
        )


async def lookup_agent_owner(host_id: str) -> Optional[dict[str, Any]]:
    """Return ``{instance_id, sid, host_id}`` if an owner is registered."""
    if not agent_sid_registry_enabled():
        return None
    client = _client()
    if client is None:
        return None
    try:
        raw = await client.get(owner_key(str(host_id)))
        if not raw:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict) or not data.get("sid"):
            return None
        return data
    except Exception:
        logger.debug(
            "agent_sid_registry_lookup_failed host_id=%s",
            host_id,
            exc_info=True,
        )
        return None
