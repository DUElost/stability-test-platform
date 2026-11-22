"""控制面 Redis 全局传输并发许可（ADR-0064 D3 / B10 U3）。

瞬时跨进程协调态：槽位以 ``lease_token`` 为键、``host_id`` 为值，TTL 到期自动
回收；``renew`` / ``release`` 必须 CAS 校验 ``lease_token`` + ``host_id``，避免
旧持有者或并行调用误释他人槽位。不建 PG 表。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

import redis

from backend.core.settings.transfer import get_transfer_permit_settings

logger = logging.getLogger(__name__)

DEFAULT_POOL = "firmware_nfs"
_HOLDERS_PREFIX = "stp:transfer-permit:"
_SLOT_PREFIX = "stp:transfer-permit:slot:"
_SOCKET_TIMEOUT_SECONDS = 2.0

_client: Any = None

# 原子 acquire：先修剪已过期槽位，再在容量内 SET NX 等价写入。
_ACQUIRE_LUA = """-- TRANSFER_PERMIT_ACQUIRE
local holders = KEYS[1]
local max_hosts = tonumber(ARGV[1])
local token = ARGV[2]
local host_id = ARGV[3]
local ttl = tonumber(ARGV[4])
local slot_prefix = ARGV[5]

local members = redis.call('SMEMBERS', holders)
for _, m in ipairs(members) do
  if redis.call('EXISTS', slot_prefix .. m) == 0 then
    redis.call('SREM', holders, m)
  end
end

local slot = slot_prefix .. token
local current = redis.call('GET', slot)
if current then
  if current ~= host_id then
    return {0, -1}
  end
  redis.call('EXPIRE', slot, ttl)
  redis.call('SADD', holders, token)
  return {1, redis.call('SCARD', holders)}
end

local count = redis.call('SCARD', holders)
if count >= max_hosts then
  return {0, count}
end

redis.call('SET', slot, host_id, 'EX', ttl)
redis.call('SADD', holders, token)
return {1, count + 1}
"""

_RENEW_LUA = """-- TRANSFER_PERMIT_RENEW
local slot = KEYS[1]
local holders = KEYS[2]
local expected_host = ARGV[1]
local ttl = tonumber(ARGV[2])
local token = ARGV[3]
local current = redis.call('GET', slot)
if current == expected_host then
  redis.call('EXPIRE', slot, ttl)
  redis.call('SADD', holders, token)
  return 1
end
return 0
"""

_RELEASE_LUA = """-- TRANSFER_PERMIT_RELEASE
local slot = KEYS[1]
local holders = KEYS[2]
local expected_host = ARGV[1]
local token = ARGV[2]
local current = redis.call('GET', slot)
if current == expected_host then
  redis.call('DEL', slot)
  redis.call('SREM', holders, token)
  return 1
end
return 0
"""


class TransferPermitError(Exception):
    """传输许可领域错误。"""

    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class TransferPermitUnavailable(TransferPermitError):
    def __init__(self, message: str = "transfer permit redis unavailable") -> None:
        super().__init__(
            "TRANSFER_PERMIT_UNAVAILABLE",
            message,
            status_code=503,
        )


@dataclass(frozen=True)
class AcquireResult:
    acquired: bool
    pool: str
    host_id: str
    lease_token: str
    active_count: int
    max_hosts: int
    ttl_seconds: int


def holders_key(pool: str) -> str:
    return f"{_HOLDERS_PREFIX}{pool}:holders"


def slot_key(pool: str, lease_token: str) -> str:
    return f"{_SLOT_PREFIX}{pool}:{lease_token}"


def slot_prefix(pool: str) -> str:
    return f"{_SLOT_PREFIX}{pool}:"


def configure_transfer_permit(redis_url: str) -> None:
    """绑定同步 Redis 客户端（FastAPI lifespan 调用）。"""
    global _client
    url = (redis_url or "").strip()
    if not url:
        _client = None
        logger.warning("transfer_permit_configured_without_url")
        return

    _client = redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=_SOCKET_TIMEOUT_SECONDS,
        socket_connect_timeout=_SOCKET_TIMEOUT_SECONDS,
    )
    logger.info("transfer_permit_configured")


def configure_transfer_permit_client(client: Any) -> None:
    """测试 / 注入：直接绑定已有客户端。"""
    global _client
    _client = client


def reset_transfer_permit_for_tests() -> None:
    global _client
    _client = None


def shutdown_transfer_permit() -> None:
    global _client
    client = _client
    _client = None
    if client is None:
        return
    try:
        client.close()
    except Exception:
        logger.debug("transfer_permit_close_failed", exc_info=True)


def _require_client() -> Any:
    if _client is None:
        raise TransferPermitUnavailable("transfer permit not configured")
    return _client


def _resolve_ttl(ttl_seconds: Optional[int]) -> int:
    if ttl_seconds is not None:
        return max(5, int(ttl_seconds))
    return get_transfer_permit_settings().stp_firmware_transfer_permit_ttl_seconds


def _resolve_max_hosts() -> int:
    return get_transfer_permit_settings().stp_firmware_transfer_max_hosts


def acquire_permit(
    *,
    host_id: str,
    lease_token: str,
    pool: str = DEFAULT_POOL,
    ttl_seconds: Optional[int] = None,
) -> AcquireResult:
    """尝试获取传输槽位。满员时 ``acquired=False``（不抛错）。"""
    client = _require_client()
    max_hosts = _resolve_max_hosts()
    ttl = _resolve_ttl(ttl_seconds)
    pool_name = (pool or DEFAULT_POOL).strip() or DEFAULT_POOL
    try:
        result = client.eval(
            _ACQUIRE_LUA,
            1,
            holders_key(pool_name),
            max_hosts,
            lease_token,
            host_id,
            ttl,
            slot_prefix(pool_name),
        )
    except TransferPermitError:
        raise
    except Exception as exc:
        raise TransferPermitUnavailable(f"transfer permit acquire failed: {exc}") from exc

    acquired_flag = int(result[0]) == 1
    active_count = int(result[1])
    if active_count < 0:
        raise TransferPermitError(
            "LEASE_TOKEN_CONFLICT",
            "lease_token already held by a different host_id",
            status_code=409,
        )
    return AcquireResult(
        acquired=acquired_flag,
        pool=pool_name,
        host_id=host_id,
        lease_token=lease_token,
        active_count=active_count if acquired_flag else max(0, active_count),
        max_hosts=max_hosts,
        ttl_seconds=ttl,
    )


def renew_permit(
    *,
    host_id: str,
    lease_token: str,
    pool: str = DEFAULT_POOL,
    ttl_seconds: Optional[int] = None,
) -> int:
    """续期；token/host 不匹配时抛 ``LEASE_TOKEN_MISMATCH``。返回 TTL。"""
    client = _require_client()
    ttl = _resolve_ttl(ttl_seconds)
    pool_name = (pool or DEFAULT_POOL).strip() or DEFAULT_POOL
    try:
        result = client.eval(
            _RENEW_LUA,
            2,
            slot_key(pool_name, lease_token),
            holders_key(pool_name),
            host_id,
            ttl,
            lease_token,
        )
    except TransferPermitError:
        raise
    except Exception as exc:
        raise TransferPermitUnavailable(f"transfer permit renew failed: {exc}") from exc
    if int(result) != 1:
        raise TransferPermitError(
            "LEASE_TOKEN_MISMATCH",
            "renew rejected: lease_token/host_id does not match active holder",
            status_code=409,
        )
    return ttl


def release_permit(
    *,
    host_id: str,
    lease_token: str,
    pool: str = DEFAULT_POOL,
) -> None:
    """释放；token/host 不匹配时抛 ``LEASE_TOKEN_MISMATCH``（绝不误释）。"""
    client = _require_client()
    pool_name = (pool or DEFAULT_POOL).strip() or DEFAULT_POOL
    try:
        result = client.eval(
            _RELEASE_LUA,
            2,
            slot_key(pool_name, lease_token),
            holders_key(pool_name),
            host_id,
            lease_token,
        )
    except TransferPermitError:
        raise
    except Exception as exc:
        raise TransferPermitUnavailable(f"transfer permit release failed: {exc}") from exc
    if int(result) != 1:
        raise TransferPermitError(
            "LEASE_TOKEN_MISMATCH",
            "release rejected: lease_token/host_id does not match active holder",
            status_code=409,
        )


def redis_url_from_env() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0").strip()
