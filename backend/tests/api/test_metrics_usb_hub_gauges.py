"""S1 控制面指标与 Host API 测试：USB/Hub 精细 Gauge 导出、未知不落 0、离网清理与 health_probe 下发。"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.agent.contracts.kernel_usb_faults import KERNEL_ERROR_COUNTER_KEYS
from backend.models.enums import HostStatus
from backend.models.host import Host


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mk_host(
    db,
    host_id: str,
    *,
    extra: dict,
    status: str = HostStatus.ONLINE.value,
    retired: bool = False,
) -> Host:
    octet = (sum(ord(ch) for ch in host_id) % 200) + 10
    host = Host(
        id=host_id,
        hostname=host_id,
        status=status,
        ip=f"10.55.1.{octet}",
        ssh_user="root",
        ssh_port=22,
        extra=extra,
        last_heartbeat=_now(),
        created_at=_now(),
    )
    if retired:
        host.retired_at = _now()
    db.add(host)
    return host


def _metric_value(body: str, prefix: str) -> float | None:
    for line in body.splitlines():
        if line.startswith(prefix + " "):
            return float(line.rsplit(" ", 1)[1])
    return None


def test_usb_hub_fine_gauges_exported_and_unknown_omitted(
    client, db_session, monkeypatch
):
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    _mk_host(
        db_session,
        "hub-h1",
        extra={
            "health": {"status": "HEALTHY", "reasons": []},
            "capacity": {
                "usb_device_count": 16,
                "adb_interface_count": 15,
                "usb_kernel_log": "ok",
                "usb_hub_topology": {
                    "cascade_hubs": 5,
                    "unbound_hubs": 1,
                    "unbound_idle_bus_hubs": 1,
                },
                "usb_kernel_errors_1h": {
                    "link_errors": 12,
                    "cable_suspect": 0,
                    "disconnects": 4,
                    "resets": 2,
                    "enum_errors": 1,
                    "hub_port_errors": 67,
                },
            },
        },
    )
    # 老 Agent / 采集失败：缺字段或 None 不得伪造 0
    _mk_host(
        db_session,
        "hub-h2-old",
        extra={
            "health": {"status": "HEALTHY", "reasons": []},
            "capacity": {
                "usb_device_count": None,
                "adb_interface_count": None,
            },
        },
    )
    db_session.commit()

    body = client.get("/metrics").text
    assert _metric_value(body, 'stability_host_usb_devices{host_id="hub-h1"}') == 16.0
    assert _metric_value(body, 'stability_host_adb_interfaces{host_id="hub-h1"}') == 15.0
    assert _metric_value(body, 'stability_host_usb_cascade_hubs{host_id="hub-h1"}') == 5.0
    assert (
        _metric_value(body, 'stability_host_usb_unbound_hubs{host_id="hub-h1",scope="total"}')
        == 1.0
    )
    assert (
        _metric_value(body, 'stability_host_usb_unbound_hubs{host_id="hub-h1",scope="idle_bus"}')
        == 1.0
    )
    assert (
        _metric_value(
            body, 'stability_host_usb_kernel_errors_1h{host_id="hub-h1",kind="hub_port_errors"}'
        )
        == 67.0
    )
    assert (
        _metric_value(
            body, 'stability_host_usb_kernel_errors_1h{host_id="hub-h1",kind="disconnects"}'
        )
        == 4.0
    )

    assert 'stability_host_usb_devices{host_id="hub-h2-old"}' not in body
    assert 'stability_host_adb_interfaces{host_id="hub-h2-old"}' not in body
    assert 'stability_host_usb_cascade_hubs{host_id="hub-h2-old"}' not in body
    assert 'stability_host_usb_unbound_hubs{host_id="hub-h2-old"' not in body
    assert 'stability_host_usb_kernel_errors_1h{host_id="hub-h2-old"' not in body


def test_usb_hub_fine_gauges_removed_when_host_goes_offline(
    client, db_session, monkeypatch
):
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    h = _mk_host(
        db_session,
        "hub-h3-off",
        extra={
            "health": {"status": "HEALTHY", "reasons": []},
            "capacity": {
                "usb_device_count": 8,
                "adb_interface_count": 8,
                "usb_hub_topology": {
                    "cascade_hubs": 2,
                    "unbound_hubs": 0,
                    "unbound_idle_bus_hubs": 0,
                },
                "usb_kernel_errors_1h": {k: 0 for k in KERNEL_ERROR_COUNTER_KEYS},
            },
        },
    )
    db_session.commit()

    body1 = client.get("/metrics").text
    assert _metric_value(body1, 'stability_host_usb_devices{host_id="hub-h3-off"}') == 8.0

    h.status = HostStatus.OFFLINE.value
    db_session.commit()

    body2 = client.get("/metrics").text
    assert 'stability_host_usb_devices{host_id="hub-h3-off"}' not in body2
    assert 'stability_host_adb_interfaces{host_id="hub-h3-off"}' not in body2
    assert 'stability_host_usb_cascade_hubs{host_id="hub-h3-off"}' not in body2
    assert 'stability_host_usb_unbound_hubs{host_id="hub-h3-off"' not in body2
    assert 'stability_host_usb_kernel_errors_1h{host_id="hub-h3-off"' not in body2


def test_hosts_api_exposes_health_probe_summary(client, db_session, auth_headers):
    _mk_host(
        db_session,
        "hub-h4-api",
        extra={
            "health": {"status": "DEGRADED", "reasons": ["usb_link_degraded"]},
            "capacity": {
                "usb_device_count": 10,
                "adb_interface_count": 10,
                "usb_hub_topology": {
                    "cascade_hubs": 5,
                    "unbound_hubs": 1,
                    "unbound_idle_bus_hubs": 1,
                },
            },
            "health_probe": {
                "verdict": "aligned",
                "topology": "ok",
                "strike_open": False,
                "signals": [],
            },
        },
    )
    db_session.commit()

    resp = client.get("/api/v1/hosts/hub-h4-api", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["health_probe"]["topology"] == "ok"
    assert data["health_probe"]["strike_open"] is False
    assert data["capacity"]["usb_hub_topology"]["unbound_hubs"] == 1
