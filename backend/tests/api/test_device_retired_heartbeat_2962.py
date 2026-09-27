"""#2962 B / ADR-0057 D3·E1：已退役设备心跳（如实记录 + 保持退役 + 单次告警）。

覆盖四条边沿（对齐主机侧 #1806 的形状）：
1. 首拍 1 条；2. 持续 N 拍仍 1 条；3. 离线恢复再 1 条；4. unretire→retire 重新计轮。
另断言：DEVICE_OFFLINE 通知不为退役设备派发（E5 同旨，避免长期刷屏）。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.models.host import Device, Host
from backend.services import notification_service

_NOW = datetime.now(timezone.utc)
_EVENT = "DEVICE_RETIRED_HEARTBEAT"


@pytest.fixture
def alerts(monkeypatch):
    captured: list[tuple[str, dict]] = []

    def _fake(event_type: str, context: dict) -> None:
        captured.append((event_type, context))

    monkeypatch.setattr(notification_service, "dispatch_notification_async", _fake)
    return captured


def _host(db, host_id: str) -> Host:
    host = Host(
        id=host_id, hostname=host_id, status="ONLINE",
        last_heartbeat=_NOW, created_at=_NOW,
    )
    db.add(host)
    db.commit()
    return host


def _device(db, serial: str, host_id: str, *, retired: bool = False) -> Device:
    device = Device(
        serial=serial, host_id=host_id, status="ONLINE",
        tags=[], created_at=_NOW,
    )
    if retired:
        device.retired_at = _NOW
        device.retired_by = "admin"
        device.retire_reason = "样机报废"
    db.add(device)
    db.commit()
    return device


def _beat(client, host_id: str, serial: str, *, connected: bool = True):
    return client.post(
        "/api/v1/heartbeat",
        json={
            "host_id": host_id,
            "status": "ONLINE",
            "mount_status": {},
            "extra": {},
            "devices": [
                {"serial": serial, "adb_state": "device" if connected else "offline",
                 "adb_connected": connected},
            ],
        },
    )


class TestRetiredDeviceHeartbeat:
    def test_first_beat_alerts_once_and_keeps_retired(self, client, db_session, alerts):
        _host(db_session, "dhb-r1")
        _device(db_session, "DHB-S1", "dhb-r1", retired=True)

        resp = _beat(client, "dhb-r1", "DHB-S1")

        assert resp.status_code == 200, resp.text
        retired_alerts = [e for e, _ in alerts if e == _EVENT]
        assert retired_alerts == [_EVENT]
        _, context = alerts[-1]
        assert context["device_serial"] == "DHB-S1"
        assert context["retired_at"] is not None
        assert context["retired_by"] == "admin"

        db_session.expire_all()
        row = db_session.query(Device).filter(Device.serial == "DHB-S1").one()
        # 如实记录：心跳事实照常更新
        assert row.adb_connected is True
        # 保持退役：不复活、痕迹不动
        assert row.retired_at is not None
        assert row.retired_by == "admin"
        assert row.retire_reason == "样机报废"
        assert row.retire_alerted_at is not None

    def test_repeated_beats_still_single_alert(self, client, db_session, alerts):
        _host(db_session, "dhb-r2")
        _device(db_session, "DHB-S2", "dhb-r2", retired=True)

        for _ in range(3):
            _beat(client, "dhb-r2", "DHB-S2")

        assert [e for e, _ in alerts if e == _EVENT] == [_EVENT], (
            "同一次退役周期内持续心跳只能响一次（D3）"
        )

    def test_offline_recovery_alerts_again(self, client, db_session, alerts):
        """离线 → 恢复上报视为新 episode（再响一次）。"""
        _host(db_session, "dhb-r3")
        device = _device(db_session, "DHB-S3", "dhb-r3", retired=True)

        _beat(client, "dhb-r3", "DHB-S3")
        assert len([e for e, _ in alerts if e == _EVENT]) == 1

        # 模拟设备掉线后回场：上一次心跳把设备标 OFFLINE（prev_status 非 ONLINE）
        device.status = "OFFLINE"
        db_session.commit()
        _beat(client, "dhb-r3", "DHB-S3")

        assert len([e for e, _ in alerts if e == _EVENT]) == 2

    def test_unretire_then_retire_recounts(self, client, db_session, alerts, admin_headers):
        """unretire → retire 重新计轮（unretire 清零 retire_alerted_at）。"""
        _host(db_session, "dhb-r4")
        device = _device(db_session, "DHB-S4", "dhb-r4")
        device_id = device.id

        assert client.post(
            f"/api/v1/devices/{device_id}/retire",
            json={"retire_reason": "第一轮"}, headers=admin_headers,
        ).status_code == 200
        _beat(client, "dhb-r4", "DHB-S4")
        assert len([e for e, _ in alerts if e == _EVENT]) == 1

        assert client.post(
            f"/api/v1/devices/{device_id}/unretire",
            json={"retire_reason": "恢复"}, headers=admin_headers,
        ).status_code == 200
        assert client.post(
            f"/api/v1/devices/{device_id}/retire",
            json={"retire_reason": "第二轮"}, headers=admin_headers,
        ).status_code == 200
        _beat(client, "dhb-r4", "DHB-S4")

        assert len([e for e, _ in alerts if e == _EVENT]) == 2

    def test_active_device_beat_does_not_alert(self, client, db_session, alerts):
        _host(db_session, "dhb-active")
        _device(db_session, "DHB-A1", "dhb-active")

        _beat(client, "dhb-active", "DHB-A1")

        assert alerts == []

    def test_missing_retired_device_skips_offline_notification(
        self, client, db_session, alerts,
    ):
        """E5 同旨：退役设备不派 DEVICE_OFFLINE 通知（adb_state 恒 offline 会刷屏）。"""
        _host(db_session, "dhb-r5")
        _device(db_session, "DHB-S5", "dhb-r5", retired=True)

        # 心跳里不带该设备 → _mark_missing_devices_offline 路径
        resp = client.post(
            "/api/v1/heartbeat",
            json={"host_id": "dhb-r5", "status": "ONLINE", "mount_status": {}, "extra": {}},
        )
        assert resp.status_code == 200

        db_session.expire_all()
        row = db_session.query(Device).filter(Device.serial == "DHB-S5").one()
        assert row.status == "OFFLINE", "事实照记：OFFLINE 状态仍要落"
        assert [e for e, _ in alerts if e == "DEVICE_OFFLINE"] == []
