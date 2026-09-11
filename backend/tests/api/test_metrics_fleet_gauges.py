"""#1258：``/metrics`` 拉取时刷新 host/device 在线计数（真实生产者）。

此前 ``stability_host_online`` / ``stability_device_online`` 只有 Gauge 定义、
无任何写入调用，仪表板对应面板恒空。修复为拉取时按 DB 状态现算：
低基数、无周期任务的 staleness，label 用小写枚举值与仪表板 PromQL 对齐。
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.models.enums import DeviceStatus, HostStatus
from backend.models.host import Device, Host


def _seed_fleet(db_session) -> None:
    now = datetime.now(timezone.utc)
    db_session.add_all([
        Host(id="gauge-h1", hostname="gh1", status=HostStatus.ONLINE.value,
             last_heartbeat=now, created_at=now),
        Host(id="gauge-h2", hostname="gh2", status=HostStatus.ONLINE.value,
             last_heartbeat=now, created_at=now),
        Host(id="gauge-h3", hostname="gh3", status=HostStatus.OFFLINE.value,
             last_heartbeat=now, created_at=now),
        Device(serial="gauge-d1", host_id="gauge-h1", status=DeviceStatus.ONLINE.value,
               tags=[], created_at=now),
        Device(serial="gauge-d2", host_id="gauge-h1", status=DeviceStatus.BUSY.value,
               tags=[], created_at=now),
    ])
    db_session.commit()


def test_metrics_exposes_fleet_online_gauges(client, db_session, monkeypatch):
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    _seed_fleet(db_session)

    body = client.get("/metrics").text

    assert 'stability_host_online{status="online"} 2.0' in body
    assert 'stability_host_online{status="offline"} 1.0' in body
    assert 'stability_host_online{status="degraded"} 0.0' in body
    assert 'stability_device_online{status="online"} 1.0' in body
    assert 'stability_device_online{status="busy"} 1.0' in body
    assert 'stability_device_online{status="error"} 0.0' in body
