"""Console run 归属注册表（ADR-0027 P3-4 / #1737 P1，方向 A）。

`RunConsole` 是进程级单例：`_runs` / `_inflight_keys` 只覆盖本进程。多实例
形态下需要两样跨进程事实：

1. **全局 ``run_key`` 互斥**——同 key 全局至多一个 RUNNING（dedup Jira
   「同厂商串行」在多副本下不失效）；
2. **owner 登记**——``run_id`` → 本实例，为后续 P2（status/订阅走共享状态）
   与 P3（cancel 转发）提供落点。

语义（裁决草案 §4，方向 A；已随 ADR-0027 v1.4 定稿）：

- **fail-closed**：获取互斥时 Redis 不可达 → ``ConsoleRegistryUnavailable``，
  调用方**拒绝启动**（宁拒绝不重复执行）；
- **续期**：严格 CAS（payload 指纹比对）。返回 ``lost`` 表示**确认已不属于
  本实例**（键被外部持有或已丢失）——由调用方语义化（P1 = 止损取消），
  纯瞬态 Redis 异常返回 ``unavailable`` 且**不**触发止损；
- **owner 键**：renew-or-rebuild（键丢失且我方 run 仍存活 → 重建；外部持有 →
  ``foreign`` 仅告警）——与 ``agent_sid_registry`` #1113 同型；
- **释放**：CAS delete；失败仅告警（TTL 兜底）。

实现形态与 ``agent_sid_registry``（P3-3/#887/#1113）同族，但有两点刻意不同：

- **同步 Redis 客户端**：`RunConsole` 是同步线程模型且会被事件循环线程直接调用，
  用 ``run_coroutine_threadsafe`` 桥接会在循环线程内死锁——因此本模块自建
  ``redis.Redis``（同步）连接，`socket_timeout` 有界；
- **续期不重建互斥键**：互斥键丢失即「已失去互斥」，不得自行复活（owner 键则
  可重建，见上）。

门控：`STP_CONSOLE_REGISTRY`（默认跟随 `STP_SOCKETIO_REDIS_ADAPTER`；
`TESTING=1` 恒关），与 P3-2/P3-3 同款——单实例默认零变化。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_FALSEY = frozenset({"0", "false", "False", "no", "NO", "off", "OFF"})
_RUN_KEY_PREFIX = "stp:console:key:"
_OWNER_KEY_PREFIX = "stp:console:owner:"
_STATUS_KEY_PREFIX = "stp:console:status:"
_CANCEL_REQ_PREFIX = "stp:console:cancelreq:"
_CANCEL_ACK_PREFIX = "stp:console:cancelack:"
_DEFAULT_TTL_SECONDS = 120
#: 单条命令的 socket 超时（秒）——有界，避免 Redis 故障拖死调用方线程。
_SOCKET_TIMEOUT_SECONDS = 2.0

_client: Any = None


class ConsoleRegistryError(Exception):
    """注册表错误基类。"""


class ConsoleRunKeyBusy(ConsoleRegistryError):
    """``run_key`` 已被（本实例之外）持有。"""


class ConsoleRegistryUnavailable(ConsoleRegistryError):
    """注册表不可用（未配置 / Redis 不可达）——调用方须 fail-closed。"""


def console_registry_enabled() -> bool:
    """Opt-in. Default follows the SocketIO Redis adapter; TESTING=1 恒关。"""
    if os.getenv("TESTING") == "1":
        return False
    explicit = os.getenv("STP_CONSOLE_REGISTRY", "").strip()
    if explicit:
        return explicit not in _FALSEY
    from backend.realtime.socketio_redis import socketio_redis_adapter_enabled

    return socketio_redis_adapter_enabled()


def console_registry_ttl_seconds() -> int:
    raw = os.getenv("STP_CONSOLE_REGISTRY_TTL_SECONDS", str(_DEFAULT_TTL_SECONDS))
    try:
        return max(30, int(raw))
    except ValueError:
        return _DEFAULT_TTL_SECONDS


def configure_console_registry(redis_url: str) -> None:
    """绑定同步 Redis 客户端（FastAPI lifespan 调用）。

    URL 为空或未启用时保持未配置——后续获取互斥将 fail-closed 报
    ``ConsoleRegistryUnavailable``（不静默降级为本地互斥）。
    """
    global _client
    if not console_registry_enabled():
        _client = None
        return
    url = (redis_url or "").strip()
    if not url:
        _client = None
        logger.warning("console_registry_configured_without_url")
        return
    import redis  # 同步客户端（redis 包同时提供 sync/async）

    _client = redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=_SOCKET_TIMEOUT_SECONDS,
        socket_connect_timeout=_SOCKET_TIMEOUT_SECONDS,
    )
    logger.info(
        "console_registry_configured instance_id=%s ttl=%ss",
        control_plane_instance_id(),
        console_registry_ttl_seconds(),
    )


def reset_console_registry_for_tests() -> None:
    """测试专用：清掉绑定的客户端（生产路径不调用）。"""
    global _client
    _client = None


def shutdown_console_registry() -> None:
    """lifespan 收尾：尽力关闭自有连接（失败仅告警）。"""
    global _client
    client = _client
    _client = None
    if client is None:
        return
    try:
        client.close()
    except Exception:
        logger.debug("console_registry_close_failed", exc_info=True)


def control_plane_instance_id() -> str:
    """复用 P3-3 的实例标识（同一进程两个注册表共享身份）。"""
    from backend.realtime.agent_sid_registry import control_plane_instance_id as _cp_id

    return _cp_id()


def _require_client() -> Any:
    if not console_registry_enabled():
        raise ConsoleRegistryUnavailable("console registry disabled")
    if _client is None:
        raise ConsoleRegistryUnavailable("console registry not configured")
    return _client


def run_key_key(run_key: str) -> str:
    return f"{_RUN_KEY_PREFIX}{run_key}"


def owner_key(run_id: str) -> str:
    return f"{_OWNER_KEY_PREFIX}{run_id}"


def status_key(run_id: str) -> str:
    return f"{_STATUS_KEY_PREFIX}{run_id}"


def cancel_request_key(run_id: str) -> str:
    return f"{_CANCEL_REQ_PREFIX}{run_id}"


def cancel_ack_key(run_id: str) -> str:
    return f"{_CANCEL_ACK_PREFIX}{run_id}"


def _run_key_payload(run_key: str, run_id: str) -> str:
    """确定性 payload：键序固定，CAS 比对可逐字节复现（同 #887）。"""
    return json.dumps(
        {
            "instance_id": control_plane_instance_id(),
            "run_id": str(run_id),
            "run_key": str(run_key),
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _owner_payload(run_id: str, run_key: str) -> str:
    return json.dumps(
        {
            "instance_id": control_plane_instance_id(),
            "run_id": str(run_id),
            "run_key": str(run_key),
        },
        separators=(",", ":"),
        sort_keys=True,
    )


# ── Lua：Redis 单线程内原子执行「比对 + 写」，杜绝评估期的让出窗口 ──────────

# 严格 CAS 续期：值匹配才 EXPIRE；缺失/外部持有 → 0（互斥不复活）。
_CAS_RENEW_LUA = """-- CONSOLE_CAS_RENEW
local current = redis.call("GET", KEYS[1])
if current == ARGV[1] then
  return redis.call("EXPIRE", KEYS[1], ARGV[2])
end
return 0
"""

# owner 键 renew-or-rebuild：缺失 → SET（我方 run 存活）；匹配 → EXPIRE；
# 外部持有 → 0（绝不覆盖他人登记）。
_CAS_RENEW_OR_REBUILD_LUA = """-- CONSOLE_RENEW_OR_REBUILD
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

_CAS_DELETE_LUA = """-- CONSOLE_CAS_DELETE
local current = redis.call("GET", KEYS[1])
if current == ARGV[1] then
  return redis.call("DEL", KEYS[1])
end
return 0
"""

RENEW_OK = "ok"
RENEW_LOST = "lost"
RENEW_UNAVAILABLE = "unavailable"


def acquire_run_key(run_key: str, *, run_id: str) -> None:
    """全局获取 ``run_key`` 互斥（``SET NX PX``）。

    Raises:
        ConsoleRunKeyBusy: 键已被持有（跨实例并发）。
        ConsoleRegistryUnavailable: 注册表不可用（fail-closed，调用方须拒绝启动）。
    """
    client = _require_client()
    try:
        acquired = client.set(
            run_key_key(run_key),
            _run_key_payload(run_key, run_id),
            nx=True,
            px=console_registry_ttl_seconds() * 1000,
        )
    except Exception as exc:
        raise ConsoleRegistryUnavailable(f"console registry unavailable: {exc}") from exc
    if not acquired:
        raise ConsoleRunKeyBusy(f"run_key busy (cross-instance): {run_key}")


def renew_run_key(run_key: str, *, run_id: str) -> str:
    """续期互斥键（严格 CAS）。``ok`` / ``lost`` / ``unavailable``。"""
    try:
        client = _require_client()
    except ConsoleRegistryUnavailable:
        return RENEW_UNAVAILABLE
    try:
        result = client.eval(
            _CAS_RENEW_LUA,
            1,
            run_key_key(run_key),
            _run_key_payload(run_key, run_id),
            console_registry_ttl_seconds(),
        )
        return RENEW_OK if int(result) == 1 else RENEW_LOST
    except Exception:
        logger.warning(
            "console_registry_renew_error run_key=%s run_id=%s", run_key, run_id,
            exc_info=True,
        )
        return RENEW_UNAVAILABLE


def release_run_key(run_key: str, *, run_id: str) -> None:
    """CAS 释放互斥键（尽力而为；失败仅告警，TTL 兜底）。"""
    try:
        client = _require_client()
    except ConsoleRegistryUnavailable:
        return
    try:
        client.eval(
            _CAS_DELETE_LUA, 1, run_key_key(run_key), _run_key_payload(run_key, run_id)
        )
    except Exception:
        logger.warning(
            "console_registry_release_failed run_key=%s run_id=%s", run_key, run_id,
            exc_info=True,
        )


def register_owner(run_id: str, *, run_key: str) -> None:
    """登记 owner（``SET EX``；run_id 唯一，后写覆盖即最新）。失败 fail-closed。"""
    client = _require_client()
    try:
        client.set(
            owner_key(run_id),
            _owner_payload(run_id, run_key),
            ex=console_registry_ttl_seconds(),
        )
    except Exception as exc:
        raise ConsoleRegistryUnavailable(f"console registry unavailable: {exc}") from exc


def renew_owner(run_id: str, *, run_key: str) -> str:
    """续期/重建 owner 键。``ok`` / ``foreign`` / ``unavailable``。"""
    try:
        client = _require_client()
    except ConsoleRegistryUnavailable:
        return RENEW_UNAVAILABLE
    try:
        result = client.eval(
            _CAS_RENEW_OR_REBUILD_LUA,
            1,
            owner_key(run_id),
            _owner_payload(run_id, run_key),
            console_registry_ttl_seconds(),
        )
        return RENEW_OK if int(result) == 1 else "foreign"
    except Exception:
        logger.warning("console_registry_owner_renew_error run_id=%s", run_id, exc_info=True)
        return RENEW_UNAVAILABLE


def release_owner(run_id: str, *, run_key: str) -> None:
    """CAS 释放 owner 键（尽力而为）。"""
    try:
        client = _require_client()
    except ConsoleRegistryUnavailable:
        return
    try:
        client.eval(
            _CAS_DELETE_LUA, 1, owner_key(run_id), _owner_payload(run_id, run_key)
        )
    except Exception:
        logger.warning("console_registry_owner_release_failed run_id=%s", run_id, exc_info=True)


# ── 状态快照（P2：跨实例 status / 订阅校验走共享状态）──────────────────────
#
# 与 owner 身份键的关键差别：``run_id`` 全局唯一，不存在「外部合法持有者」，
# 因此快照用普通 ``SET ... EX``（后写覆盖即最新）+ ``EXPIRE`` 续期，无需
# CAS 指纹比对（P1 的互斥键才需要）。快照失败**不得影响 run 本身**——调用方
# （RunConsole）按 best-effort 处理，仅告警。


def publish_status_snapshot(
    run_id: str, snapshot: dict[str, Any], *, ttl_seconds: int
) -> None:
    """发布/覆盖状态快照（``SET ... EX``）。

    Raises:
        ConsoleRegistryUnavailable: 注册表不可用（调用方 best-effort 处理）。
    """
    client = _require_client()
    payload = dict(snapshot)
    payload.setdefault("instance_id", control_plane_instance_id())
    try:
        client.set(
            status_key(run_id),
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ex=max(30, int(ttl_seconds)),
        )
    except Exception as exc:
        raise ConsoleRegistryUnavailable(f"console registry unavailable: {exc}") from exc


def refresh_status_ttl(run_id: str, *, ttl_seconds: int) -> bool:
    """刷新快照 TTL；键不在（被淘汰/丢失）→ ``False``，调用方应重发全文。"""
    try:
        client = _require_client()
    except ConsoleRegistryUnavailable:
        return False
    try:
        return bool(client.expire(status_key(run_id), max(30, int(ttl_seconds))))
    except Exception:
        logger.warning(
            "console_registry_status_refresh_failed run_id=%s", run_id, exc_info=True
        )
        return False


def read_status_snapshot(run_id: str) -> Optional[dict[str, Any]]:
    """读取快照；不可用 / 不存在 / 损坏 → ``None``（调用方按未知处理）。"""
    try:
        client = _require_client()
    except ConsoleRegistryUnavailable:
        return None
    try:
        raw = client.get(status_key(run_id))
    except Exception:
        logger.warning(
            "console_registry_status_read_failed run_id=%s", run_id, exc_info=True
        )
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def delete_status_snapshot(run_id: str) -> None:
    """删除快照（本地淘汰终态 run 时清理；失败仅告警，TTL 兜底）。"""
    try:
        client = _require_client()
    except ConsoleRegistryUnavailable:
        return
    try:
        client.delete(status_key(run_id))
    except Exception:
        logger.warning(
            "console_registry_status_delete_failed run_id=%s", run_id, exc_info=True
        )


# ── 取消转发（P3：跨实例 cancel 请求位 + ack）──────────────────────────────
#
# 请求与 ack 都是 run_id 维度、无外部写者 → 普通 ``SET ... EX``（同快照语义）。
# 请求带 ``requested_at`` 指纹：owner 的 ack 回带同一指纹，请求方**只接受自己
# 那次请求**的结果（重试/并发不串线）。

_DEFAULT_CANCEL_TTL_SECONDS = 60


def cancel_ttl_seconds() -> int:
    raw = os.getenv("STP_CONSOLE_CANCEL_TTL_SECONDS", str(_DEFAULT_CANCEL_TTL_SECONDS))
    try:
        return max(10, int(raw))
    except ValueError:
        return _DEFAULT_CANCEL_TTL_SECONDS


def _read_json_key(key: str, *, what: str) -> Optional[dict[str, Any]]:
    try:
        client = _require_client()
    except ConsoleRegistryUnavailable:
        return None
    try:
        raw = client.get(key)
    except Exception:
        logger.warning("console_registry_%s_read_failed key=%s", what, key, exc_info=True)
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def request_cancel(run_id: str, *, requested_at: str) -> None:
    """投递跨实例取消请求（owner 在下个 control tick 消费）。

    Raises:
        ConsoleRegistryUnavailable: 注册表不可用（调用方 fail-closed：不假装已取消）。
    """
    client = _require_client()
    payload = json.dumps(
        {"instance_id": control_plane_instance_id(), "requested_at": requested_at},
        separators=(",", ":"),
        sort_keys=True,
    )
    try:
        client.set(cancel_request_key(run_id), payload, ex=cancel_ttl_seconds())
    except Exception as exc:
        raise ConsoleRegistryUnavailable(f"console registry unavailable: {exc}") from exc


def read_cancel_request(run_id: str) -> Optional[dict[str, Any]]:
    """owner 侧读取取消请求（无 → None）。"""
    return _read_json_key(cancel_request_key(run_id), what="cancel_request")


def clear_cancel_request(run_id: str) -> None:
    """owner 消费后清请求位（失败仅告警，TTL 兜底）。"""
    try:
        client = _require_client()
    except ConsoleRegistryUnavailable:
        return
    try:
        client.delete(cancel_request_key(run_id))
    except Exception:
        logger.warning(
            "console_registry_cancel_req_clear_failed run_id=%s", run_id, exc_info=True
        )


def publish_cancel_ack(run_id: str, *, requested_at: str, canceled: bool) -> None:
    """owner 侧回写取消结果（best-effort：失败仅告警，请求方超时 fail-closed）。"""
    try:
        client = _require_client()
    except ConsoleRegistryUnavailable:
        return
    payload = json.dumps(
        {
            "requested_at": requested_at,
            "canceled": bool(canceled),
            "by": control_plane_instance_id(),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    try:
        client.set(cancel_ack_key(run_id), payload, ex=cancel_ttl_seconds())
    except Exception:
        logger.warning(
            "console_registry_cancel_ack_failed run_id=%s", run_id, exc_info=True
        )


def read_cancel_ack(run_id: str, *, requested_at: str) -> Optional[dict[str, Any]]:
    """读取 ack；**指纹不匹配视为未到**（只接受自己那次请求的结果）。"""
    data = _read_json_key(cancel_ack_key(run_id), what="cancel_ack")
    if data is None:
        return None
    if str(data.get("requested_at") or "") != requested_at:
        return None
    return data
