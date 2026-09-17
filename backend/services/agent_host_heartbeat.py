"""Agent 轻量 host 心跳（#1520 垂直切片：agent_api /heartbeat）。

``POST /api/v1/agent/heartbeat``：host upsert → catalog 过期判定 → 退役告警 →
online_healthy 计数 → backpressure / min_version 建议。

``suggested_heartbeat_interval`` / ``suggested_log_rate_limit`` 亦供权威
``/api/v1/heartbeat`` 路由复用（避免 services→routes）。

路由退化为 ``ok(await record_agent_host_heartbeat(...))``。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.enums import HostStatus
from backend.models.host import Device, Host
from backend.services.host_retirement import (
    retired_heartbeat_context,
    should_alert_retired_heartbeat,
)
from backend.services.script_catalog_version import compute_script_catalog_version_async

# 建议 Agent 心跳周期（秒）；随在线设备数缓增，闭环 backpressure
HEARTBEAT_INTERVAL_MIN = int(os.getenv("STP_HEARTBEAT_INTERVAL_MIN", "15"))
HEARTBEAT_INTERVAL_MAX = int(os.getenv("STP_HEARTBEAT_INTERVAL_MAX", "60"))
HEARTBEAT_INTERVAL_BASE = int(os.getenv("STP_HEARTBEAT_INTERVAL_BASE", "20"))
# ADR-0026 P2-2: 建议每 host 日志行速率上限（lines/s）；随设备数收紧
LOG_RATE_LIMIT_BASE = int(os.getenv("STP_LOG_RATE_LIMIT_BASE", "200"))
LOG_RATE_LIMIT_MIN = int(os.getenv("STP_LOG_RATE_LIMIT_MIN", "20"))


def suggested_heartbeat_interval(online_healthy: int) -> int:
    """Scale poll interval with fleet size (ADR-0026 P0 heartbeat 减负)."""
    scaled = HEARTBEAT_INTERVAL_BASE + max(0, online_healthy) // 10
    return max(HEARTBEAT_INTERVAL_MIN, min(HEARTBEAT_INTERVAL_MAX, scaled))


def suggested_log_rate_limit(online_healthy: int) -> int:
    """Tighten per-host step_log rate as fleet grows (ADR-0026 P2-2)."""
    scaled = LOG_RATE_LIMIT_BASE - (max(0, online_healthy) // 10) * 10
    return max(LOG_RATE_LIMIT_MIN, scaled)


# 路由 / 既有测试用的私有名别名。
_suggested_heartbeat_interval = suggested_heartbeat_interval
_suggested_log_rate_limit = suggested_log_rate_limit


class HeartbeatRequest(BaseModel):
    host_id: str
    script_catalog_version: str = ""
    load: Dict[str, Any] = {}
    capacity: Optional[Dict[str, Any]] = None  # ADR-0019 Phase 1
    agent_instance_id: str = ""   # ADR-0019 Phase 3a
    boot_id: str = ""             # ADR-0019 Phase 3a


class BackpressureInfo(BaseModel):
    log_rate_limit: Optional[int] = None
    # ADR-0026 P0: suggested Agent poll interval (seconds)
    heartbeat_interval_seconds: Optional[int] = None


class HeartbeatResponse(BaseModel):
    script_catalog_outdated: bool = False
    backpressure: BackpressureInfo
    capacity: Optional[Dict[str, Any]] = None  # ADR-0019 Phase 1
    agent_min_version: str = ""  # SemVer floor; Agent refuses to run if below
    heartbeat_interval_seconds: Optional[int] = None


async def get_backpressure() -> Optional[int]:
    """Return current backpressure setting.

    Redis-based backpressure (stp:backpressure:*) removed in Phase 4.
    SocketIO has built-in TCP backpressure; this returns None (no limit).
    Can be extended later with SocketIO-based metrics if needed.
    """
    return None


_get_backpressure = get_backpressure


async def record_agent_host_heartbeat(
    db: AsyncSession,
    payload: HeartbeatRequest,
) -> HeartbeatResponse:
    """Update host last_heartbeat; return catalog + backpressure hints."""
    host = await db.get(Host, payload.host_id)
    if host is None:
        host = Host(
            id=payload.host_id,
            hostname=payload.host_id,
            status=HostStatus.ONLINE.value,
            created_at=datetime.now(timezone.utc),
        )
        db.add(host)

    scripts_outdated = bool(payload.script_catalog_version) and (
        payload.script_catalog_version
        != await compute_script_catalog_version_async(db)
    )

    host.last_heartbeat = datetime.now(timezone.utc)
    if payload.script_catalog_version:
        host.script_catalog_version = payload.script_catalog_version
    prev_status = host.status
    host.status = HostStatus.ONLINE.value

    if should_alert_retired_heartbeat(host, prev_status=prev_status):
        from backend.services.notification_service import dispatch_notification_async

        dispatch_notification_async(
            "HOST_RETIRED_HEARTBEAT", retired_heartbeat_context(host),
        )

    online_rows = await db.execute(
        select(Device.id).where(
            Device.host_id == payload.host_id,
            Device.adb_connected == True,
            Device.adb_state.notin_(["offline", "unknown", ""]),
        )
    )
    online_healthy = len(online_rows.scalars().all())

    await db.commit()

    backpressure = await get_backpressure()
    suggested_interval = suggested_heartbeat_interval(online_healthy)
    suggested_log_rate = (
        backpressure if backpressure is not None
        else suggested_log_rate_limit(online_healthy)
    )
    from backend.services.agent_version_gate import resolve_agent_min_version
    return HeartbeatResponse(
        script_catalog_outdated=scripts_outdated,
        backpressure=BackpressureInfo(
            log_rate_limit=suggested_log_rate,
            heartbeat_interval_seconds=suggested_interval,
        ),
        capacity={
            "online_healthy_devices": online_healthy,
        },
        agent_min_version=resolve_agent_min_version(),
        heartbeat_interval_seconds=suggested_interval,
    )


# 路由 / 既有测试用的端点别名。
agent_heartbeat = record_agent_host_heartbeat
