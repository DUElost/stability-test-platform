"""#2962 B / ADR-0057 D4 第 7 面：AI 助手读面排除退役设备。

与 host 侧既有口径（`Host.retired_at.is_(None)`）对齐：助手把退役设备报成
在役库存会让运维判断出错，且助手无 include_retired 开关。
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.models.host import Device, Host
from backend.services.ai_assistant.tools import _q_devices, _q_platform_health

_NOW = datetime.now(timezone.utc)


def _seed(db) -> None:
    db.add(Host(
        id="ai-h1", hostname="ai-h1", status="ONLINE",
        last_heartbeat=_NOW, created_at=_NOW,
    ))
    db.add_all([
        Device(serial="AI-ACTIVE", host_id="ai-h1", status="ONLINE",
               tags=[], created_at=_NOW, last_seen=_NOW),
        Device(serial="AI-RETIRED", host_id="ai-h1", status="ONLINE",
               tags=[], created_at=_NOW, last_seen=_NOW,
               retired_at=_NOW, retired_by="admin", retire_reason="报废"),
    ])
    db.commit()


def test_q_devices_excludes_retired(db_session):
    _seed(db_session)

    out = _q_devices(db_session, {})

    assert "AI-ACTIVE" in out
    assert "AI-RETIRED" not in out


def test_platform_health_device_counts_exclude_retired(db_session):
    _seed(db_session)

    out = _q_platform_health(db_session, {})

    assert "设备：{'ONLINE': 1}" in out, f"退役设备不应进设备计数：{out}"
