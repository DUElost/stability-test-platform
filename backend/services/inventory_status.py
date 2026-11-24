"""库存（Agent host 与物理设备）统一有效状态谓词与只读投影（#3325 / #4117 阶段 B1）。

单一事实来源：``session_watchdog``、``GET /api/v1/devices``、``GET /api/v1/devices/{id}``、
``GET /api/v1/hosts``、``GET /api/v1/hosts/{id}`` 以及后续独立 Facets 端点均复用本模块
的 SQL 表达式与纯 Python 投影函数，避免「读路径过滤口径与返回字段错位」。

语义边界（见 ``docs/design/2026-11-24-list-pagination-and-facets-contract.md`` §2.2）：
- 缺失心跳（``last_heartbeat IS NULL``）或心跳过期的 Agent host 判定为 ``OFFLINE``；
- 心跳新鲜的 ``DEGRADED`` Agent host 不判定为 ``OFFLINE``；
- 无所属 Agent host（``Device.host_id IS NULL``）的物理设备不受 Agent host 离线级联影响；
- 仅在只读投影层派生展示态，绝不修改 ORM 实体或触发数据库写入。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, case, literal, or_

from backend.core.device_lifecycle import normalize_now
from backend.core.job_timeout_config import HOST_HEARTBEAT_TIMEOUT_SECONDS
from backend.models.enums import HostStatus
from backend.models.host import Device, Host


def _ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def host_offline_deadline(now: Optional[datetime] = None) -> datetime:
    """计算 Agent host 心跳超时截止时间（同一请求内共用同一 ``now``）。"""
    ref = _ensure_utc(normalize_now(now))
    assert ref is not None
    return ref - timedelta(seconds=HOST_HEARTBEAT_TIMEOUT_SECONDS)


def host_heartbeat_expired_condition(*, now: Optional[datetime] = None):
    """SQL 谓词：Agent host 心跳缺失（NULL）或早于超时阈值。"""
    deadline = host_offline_deadline(now)
    return or_(Host.last_heartbeat.is_(None), Host.last_heartbeat < deadline)


def host_effective_offline_condition(*, now: Optional[datetime] = None):
    """SQL 谓词：Agent host 账面已 OFFLINE，或心跳缺失/过期。

    新鲜 ``DEGRADED`` 不满足本条件，因此不会被误判为离线。
    """
    return or_(
        Host.status == HostStatus.OFFLINE.value,
        host_heartbeat_expired_condition(now=now),
    )


def host_effective_status_expr(*, now: Optional[datetime] = None):
    """SQL 表达式：Agent host 有效状态（与 ``effective_host_status`` 同口径）。"""
    return case(
        (host_effective_offline_condition(now=now), literal(HostStatus.OFFLINE.value)),
        else_=Host.status,
    )


def device_effective_status_expr(*, now: Optional[datetime] = None):
    """SQL 表达式：物理设备有效状态（要求查询已 ``outerjoin(Host, Device.host_id == Host.id)``）。

    - ``Device.host_id IS NULL``（无主机设备）不受主机级联影响，保持 ``Device.status``；
    - 所属 Agent host 有效离线时投影为 ``OFFLINE``；
    - 所属 Agent host 为新鲜 ``DEGRADED`` 或新鲜 ``ONLINE`` 时保持 ``Device.status``。
    """
    return case(
        (
            and_(
                Device.host_id.isnot(None),
                Host.id.isnot(None),
                host_effective_offline_condition(now=now),
            ),
            literal("OFFLINE"),
        ),
        else_=Device.status,
    )


def effective_host_status(host: Any, *, now: Optional[datetime] = None) -> str:
    """纯函数：计算 Agent host 有效状态（不修改 ORM 对象）。"""
    raw = getattr(host, "status", None) or HostStatus.OFFLINE.value
    if raw == HostStatus.OFFLINE.value:
        return HostStatus.OFFLINE.value
    last = _ensure_utc(getattr(host, "last_heartbeat", None))
    if last is None or last < host_offline_deadline(now):
        return HostStatus.OFFLINE.value
    return str(raw)


def effective_device_status(
    device: Any,
    host: Any = None,
    *,
    now: Optional[datetime] = None,
) -> str:
    """纯函数：计算物理设备有效状态（不修改 ORM 对象，不触发懒加载）。"""
    raw = getattr(device, "status", None) or "OFFLINE"
    if getattr(device, "host_id", None) is None or host is None:
        return str(raw)
    if effective_host_status(host, now=now) == HostStatus.OFFLINE.value:
        return "OFFLINE"
    return str(raw)
