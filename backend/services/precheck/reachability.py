"""Agent 可达性的双通道冲突诊断（#491）。

控制面用**两条独立通道**判断 Agent 是否在线：

1. **HTTP 心跳通道** —— Agent 定期 POST，`host.last_heartbeat` / `host.status`；
2. **SocketIO RPC 通道** —— `precheck.verify` 走 `call_agent_rpc` 判可达，
   room 键为 ``agent:{HOST_ID}``。

两条通道失配时（心跳新鲜却 RPC 报 ``agent_offline``），旧实现**静默 requeue**：
PlanRun 卡 QUEUED、`queue_blockers` 恒为 ``host_unreachable``，日志里一条告警
都没有。真实事故见 PlanRun #247（host.id 迁移后 Agent 侧 ``.env`` 的
``HOST_ID`` 未同步 → room 键失配 → RPC 恒 ``AgentNotConnectedError``，而心跳
4–23s 全新鲜，把失配完全掩盖）。

本模块**不改判定结果**（判定仍由各调用方决定），只负责在冲突时产出诊断并
打 ERROR 告警，把「静默循环」变成「一眼可见」。

判定口径：
- ``heartbeat_fresh`` —— `host.last_heartbeat` 在 `HOST_HEARTBEAT_TIMEOUT_SECONDS`
  内（与 `api/routes/devices.py` 同一环境变量）；
- ``sid_registered`` —— **本进程**是否持有该 host 的 SocketIO sid。开启 Redis
  adapter 时 Agent 可能连在别的实例上，此时该字段为假**不代表离线**，故同时
  给出 ``confidence``。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from backend.models.host import Host

logger = logging.getLogger(__name__)

# 与 api/routes/devices.py 保持一致：心跳超过该秒数视为过期
HOST_HEARTBEAT_TIMEOUT_SECONDS = int(os.getenv("HOST_HEARTBEAT_TIMEOUT_SECONDS", "300"))


def _socketio_redis_adapter_enabled() -> bool:
    """Best-effort 探测 Redis adapter；探测失败按「未启用」处理（不影响判定）。"""
    try:
        from backend.realtime.socketio_redis import socketio_redis_adapter_enabled

        return bool(socketio_redis_adapter_enabled())
    except Exception:  # pragma: no cover - 探测失败不是缺陷
        return False


def _process_local_sid(host_id: str) -> Optional[str]:
    """本进程持有的 SocketIO sid；SocketIO 未初始化或异常时返回 None。"""
    try:
        from backend.realtime.socketio_server import get_agent_namespace

        return get_agent_namespace().get_sid(str(host_id))
    except Exception:  # pragma: no cover - 未初始化 / 未注册
        return None


def diagnose_unreachable_hosts(
    db: Session,
    host_ids: list[str],
    *,
    timeout_seconds: Optional[int] = None,
) -> dict[str, dict]:
    """对判定不可达的 host 产出「心跳 vs SocketIO」诊断。

    返回 ``{host_id: {...}}``，仅覆盖库中能查到的 host（不存在的 id 不出现）。
    纯读、无副作用，可放心在判定路径上调用。
    """
    if not host_ids:
        return {}

    timeout = (
        HOST_HEARTBEAT_TIMEOUT_SECONDS
        if timeout_seconds is None
        else int(timeout_seconds)
    )
    wanted = [str(h) for h in host_ids]
    hosts = db.query(Host).filter(Host.id.in_(wanted)).all()

    adapter_enabled = _socketio_redis_adapter_enabled()
    deadline = datetime.now(timezone.utc) - timedelta(seconds=timeout)

    out: dict[str, dict] = {}
    for host in hosts:
        last_seen = host.last_heartbeat
        if last_seen is not None and last_seen.tzinfo is None:
            # 兼容历史/测试数据中的 naive 时间
            last_seen = last_seen.replace(tzinfo=timezone.utc)

        heartbeat_fresh = last_seen is not None and last_seen >= deadline
        sid = _process_local_sid(host.id)
        sid_registered = sid is not None
        status_online = getattr(host, "status", None) == "ONLINE"

        # 心跳新鲜 + 控制面认为 ONLINE，却在本进程查不到 sid —— 典型失配信号。
        # 开了 Redis adapter 时「本进程无 sid」可能是正常的多实例分布，降为 low。
        conflict = heartbeat_fresh and status_online and not sid_registered

        out[str(host.id)] = {
            "host_status": getattr(host, "status", None),
            "last_heartbeat": last_seen.isoformat() if last_seen else None,
            "heartbeat_fresh": heartbeat_fresh,
            "sid_registered": sid_registered,
            "sid_scope": "process-local",
            "redis_adapter_enabled": adapter_enabled,
            "conflict": conflict,
            "confidence": "low" if adapter_enabled else "high",
        }
    return out


def log_unreachable_conflicts(
    diagnostics: dict[str, dict],
    *,
    plan_run_id: Optional[int] = None,
) -> list[str]:
    """对冲突 host 打 ERROR 告警，返回冲突 host_id 列表（按传入顺序稳定）。

    只做日志与返回值，**不改变任何判定**。调用方负责把诊断并入
    ``run_context.precheck`` 或 ``queue_blockers``，使其可被事后追溯。
    """
    conflicts: list[str] = []
    for host_id, diag in (diagnostics or {}).items():
        if not diag.get("conflict"):
            continue
        conflicts.append(host_id)
        logger.error(
            "agent_sid_mismatch host_id=%s plan_run=%s confidence=%s: "
            "SocketIO 判定离线（本进程 sid=%s）但 HTTP 心跳新鲜"
            "（last_heartbeat=%s，host.status=%s，redis_adapter=%s）。"
            "最常见成因是 host.id 变更后 Agent 侧 .env 的 HOST_ID 未同步刷新 ——"
            " SocketIO room 键为 agent:{HOST_ID}，键失配会让 RPC 恒报"
            " AgentNotConnectedError。请核对 Agent 的 HOST_ID 配置。",
            host_id,
            plan_run_id,
            diag.get("confidence"),
            "已注册" if diag.get("sid_registered") else "未注册",
            diag.get("last_heartbeat"),
            diag.get("host_status"),
            diag.get("redis_adapter_enabled"),
        )
    return conflicts


__all__ = [
    "HOST_HEARTBEAT_TIMEOUT_SECONDS",
    "diagnose_unreachable_hosts",
    "log_unreachable_conflicts",
]
