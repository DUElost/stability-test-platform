"""#1806 / ADR-0038 ⑤：退役主机心跳（如实记录 + 保持退役 + 单次告警去重）。

覆盖 issue 验收：
1. 四条边沿：首拍 1 条 / 持续 N 拍仍 1 条 / 离线恢复再 1 条 / unretire→retire 重新计轮；
2. 两条心跳端点各有测试（含权威端点「按 IP 找回命中退役行」路径）；
3. 设备 re-home 语义不变（退役不阻断，补断言）；
4. 反例实证见 PR/Agent Note（去掉去重 → 持续拍转红；去掉恢复清零 → 恢复拍转红）。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.models.host import Device, Host
from backend.services import notification_service

_NOW = datetime.now(timezone.utc)
_EVENT = "HOST_RETIRED_HEARTBEAT"


@pytest.fixture
def alerts(monkeypatch):
    """捕获平台侧告警（心跳路径经 dispatch_notification_async 出栈）。"""
    captured: list[tuple[str, dict]] = []

    def _fake(event_type: str, context: dict) -> None:
        captured.append((event_type, context))

    monkeypatch.setattr(notification_service, "dispatch_notification_async", _fake)
    return captured


def _host(
    db_session, host_id: str, *, retired: bool = False, status: str = "ONLINE", **kw,
) -> Host:
    host = Host(
        id=host_id,
        hostname=host_id,
        status=status,
        retired_at=_NOW if retired else None,
        retired_by="admin" if retired else None,
        retire_reason="样机报废" if retired else None,
        watcher_admin_active=True,
        **kw,
    )
    db_session.add(host)
    db_session.commit()
    return host


def _beat(client, host_id: str, *, status: str = "ONLINE"):
    return client.post(
        "/api/v1/heartbeat",
        json={"host_id": host_id, "status": status, "mount_status": {}, "extra": {}},
    )


def _light_beat(client, host_id: str):
    return client.post("/api/v1/agent/heartbeat", json={"host_id": host_id})


class TestAuthoritativeHeartbeat:
    def test_first_beat_alerts_once_and_keeps_retired(
        self, client, db_session, alerts,
    ):
        host = _host(db_session, "hb-r1", retired=True)

        resp = _beat(client, "hb-r1")

        assert resp.status_code == 200, resp.text
        assert [e for e, _ in alerts] == [_EVENT]
        _, context = alerts[0]
        assert context["host_id"] == "hb-r1"
        assert context["retired_at"] is not None
        assert context["event_key"] == f"hb-r1|{host.retired_at.isoformat()}|{_EVENT}"

        db_session.expire_all()
        row = db_session.get(Host, "hb-r1")
        # 如实记录：心跳字段照常更新
        assert row.status == "ONLINE"
        assert row.last_heartbeat is not None
        # 保持退役：不复活、不解除，痕迹不动
        assert row.retired_at == host.retired_at
        assert row.retired_by == "admin"
        assert row.retire_reason == "样机报废"
        assert row.retire_alerted_at is not None

    def test_repeated_beats_still_single_alert(self, client, db_session, alerts):
        _host(db_session, "hb-r2", retired=True)

        for _ in range(3):
            _beat(client, "hb-r2")

        assert len(alerts) == 1, "同一次退役周期内持续心跳只能响一次（D4）"

    def test_offline_recovery_alerts_again(self, client, db_session, alerts):
        """离线/降级 → ONLINE 的恢复拍视为新 episode（再响一次）。"""
        host = _host(db_session, "hb-r3", retired=True)
        _beat(client, "hb-r3")
        assert len(alerts) == 1

        # 模拟 watchdog 把心跳超时主机标记 OFFLINE 后，主机又恢复心跳
        host.status = "OFFLINE"
        db_session.commit()
        _beat(client, "hb-r3")

        assert len(alerts) == 2

    def test_recovery_then_steady_beats_still_single_alert_each_episode(
        self, client, db_session, alerts,
    ):
        host = _host(db_session, "hb-r4", retired=True)
        _beat(client, "hb-r4")
        host.status = "OFFLINE"
        db_session.commit()
        for _ in range(3):
            _beat(client, "hb-r4")

        assert len(alerts) == 2  # 首拍 + 恢复拍；恢复后的持续拍不再响

    def test_active_host_beat_does_not_alert(self, client, db_session, alerts):
        _host(db_session, "hb-active")

        _beat(client, "hb-active")

        assert alerts == []

    def test_ip_recovery_hits_retired_row(
        self, client, db_session, alerts,
    ):
        """旧 agent 用 host_id=0 自动注册时按 IP 找回旧行——命中退役行不新建、
        不复活，只记心跳 + 单次告警。"""
        _host(db_session, "hb-ip", retired=True, ip="10.9.9.9", ip_address="10.9.9.9")

        resp = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": 0, "status": "ONLINE", "mount_status": {},
                "host": {"ip": "10.9.9.9"},
            },
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["host_id"] == "hb-ip"
        assert len(alerts) == 1

        db_session.expire_all()
        rows = db_session.query(Host).filter(Host.ip == "10.9.9.9").all()
        assert len(rows) == 1, "IP 找回必须命中既有退役行，不得新建"
        assert rows[0].retired_at is not None


class TestLightHeartbeat:
    def test_light_endpoint_shares_single_alert_dedup(self, client, db_session, alerts):
        _host(db_session, "hb-light", retired=True)

        first = _light_beat(client, "hb-light")
        second = _light_beat(client, "hb-light")

        assert first.status_code == 200 and second.status_code == 200
        assert len(alerts) == 1, "轻量心跳与权威路径共用同一去重判据"

        db_session.expire_all()
        row = db_session.get(Host, "hb-light")
        assert row.status == "ONLINE"
        assert row.retired_at is not None
        assert row.retire_alerted_at is not None

    def test_light_endpoint_active_host_silent(self, client, db_session, alerts):
        _host(db_session, "hb-light-active")

        _light_beat(client, "hb-light-active")

        assert alerts == []


class TestUnretireRetireRecounts:
    def test_new_cycle_alerts_again(self, client, db_session, alerts, admin_headers):
        """unretire → retire 重新计轮（② 的 unretire 清零 retire_alerted_at）。"""
        _host(db_session, "hb-cycle")

        assert client.post(
            "/api/v1/hosts/hb-cycle/retire",
            json={"retire_reason": "第一轮"}, headers=admin_headers,
        ).status_code == 200
        _beat(client, "hb-cycle")
        assert len(alerts) == 1

        assert client.post(
            "/api/v1/hosts/hb-cycle/unretire",
            json={"retire_reason": "恢复"}, headers=admin_headers,
        ).status_code == 200
        assert client.post(
            "/api/v1/hosts/hb-cycle/retire",
            json={"retire_reason": "第二轮"}, headers=admin_headers,
        ).status_code == 200

        _beat(client, "hb-cycle")
        assert len(alerts) == 2, "重新退役后应能再响一次"


class TestDeviceRehomeSemantics:
    def test_retired_host_still_rehomes_devices(self, client, db_session, alerts):
        """设备 re-home 语义不变：退役主机不像维护窗口那样阻断归属更新。"""
        host_a = _host(db_session, "hb-a", retired=True)
        host_b = _host(db_session, "hb-b")
        device = Device(serial="hb-dev", host_id=host_b.id, status="ONLINE")
        db_session.add(device)
        db_session.commit()

        resp = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": host_a.id, "status": "ONLINE", "mount_status": {},
                "devices": [{"serial": "hb-dev", "status": "ONLINE"}],
            },
        )

        assert resp.status_code == 200, resp.text
        db_session.expire_all()
        assert db_session.get(Device, device.id).host_id == host_a.id
