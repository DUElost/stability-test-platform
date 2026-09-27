"""#2962 / ADR-0057 D5·D6：设备容量口径（陈旧单列 + 退役排除）在 /metrics 现算。

既有口径（#1258 / #2754 / ADR-0038 D5）：
- fleet gauge `stability_device_online{status}` 是容量视角；
- per-host adb 分桶是「单台 host 设备批量掉线」的视角。

#2962 增补两条：
- 陈旧设备（OFFLINE 且 7 天未见）不再计进 `status="offline"`，单列
  `stability_device_stale`（A：OFFLINE 计数拆分）；
- 退役设备两类口径都不计（B：不再是容量；告警侧同一依赖）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.models.enums import DeviceStatus, HostStatus
from backend.models.host import Device, Host


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _seed(db) -> None:
    db.add(Host(
        id="dml-h1", hostname="dml-h1", status=HostStatus.ONLINE.value,
        ip="10.77.0.1", ssh_user="root", ssh_port=22, extra={},
        last_heartbeat=_now(), created_at=_now(),
    ))
    db.add_all([
        Device(serial="dml-online", host_id="dml-h1", status=DeviceStatus.ONLINE.value,
               adb_state="device", tags=[], created_at=_now(), last_seen=_now()),
        Device(serial="dml-off-recent", host_id="dml-h1", status=DeviceStatus.OFFLINE.value,
               adb_state="offline", tags=[], created_at=_now(),
               last_seen=_now() - timedelta(hours=2)),
        Device(serial="dml-off-stale", host_id="dml-h1", status=DeviceStatus.OFFLINE.value,
               adb_state="offline", tags=[], created_at=_now(),
               last_seen=_now() - timedelta(days=10)),
        Device(serial="dml-retired", host_id="dml-h1", status=DeviceStatus.ONLINE.value,
               adb_state="device", tags=[], created_at=_now(), last_seen=_now(),
               retired_at=_now(), retired_by="admin", retire_reason="报废"),
    ])
    db.commit()


def test_fleet_gauges_split_offline_and_exclude_retired(
    client, db_session, monkeypatch,
):
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    _seed(db_session)

    body = client.get("/metrics").text

    # 容量：ONLINE 只算在役设备（退役的 ONLINE 行不算）
    assert 'stability_device_online{status="online"} 1.0' in body
    # OFFLINE 桶只剩「近期掉线」；陈旧单列
    assert 'stability_device_online{status="offline"} 1.0' in body
    assert "stability_device_stale 1.0" in body


def test_per_host_adb_buckets_exclude_stale_and_retired(
    client, db_session, monkeypatch,
):
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    _seed(db_session)

    body = client.get("/metrics").text

    # 近期掉线仍在 offline 桶；退役（device 桶）与陈旧都不计
    assert 'stability_host_device_adb_state{host_id="dml-h1",state="offline"} 1.0' in body
    assert 'stability_host_device_adb_state{host_id="dml-h1",state="device"} 1.0' in body
