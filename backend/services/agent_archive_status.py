"""Host archive 运维概览（#1520 垂直切片：GET /{host_id}/archive-status）。

ADR-0025 Sprint 3：读 ``Host.extra`` 中心跳上报的 archive/capacity/health。
路由退化为 ``ok(await get_agent_archive_status(...))``。
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.host import Host


async def get_agent_archive_status(
    db: AsyncSession,
    host_id: str,
) -> dict:
    """控制面查看某 host 的存储运维概览。

    数据源：Agent 心跳上报的运维指标（Host.extra['archive']）+
    系统指标（Host.extra['capacity'] / Host.extra['health']）。
    scan 状态占位（Sprint 4）。
    """
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="host not found")

    extra = host.extra if isinstance(host.extra, dict) else {}

    return {
        "host_id": host_id,
        "agent_metrics": extra.get("archive"),
        "capacity": extra.get("capacity"),
        "health": extra.get("health"),
        "agent_version": extra.get("agent_version"),
        "scan_status": None,
        "scan_triggered_at": None,
    }
