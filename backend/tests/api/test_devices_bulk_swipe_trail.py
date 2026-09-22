"""POST /api/v1/devices/bulk-swipe-trail"""

from __future__ import annotations

import pytest

from backend.models.host import Device
from backend.realtime.socketio_server import AgentNotConnectedError


@pytest.mark.asyncio
async def test_bulk_swipe_trail_success(
    client, sample_device, sample_host, admin_headers, monkeypatch,
):
    async def fake_rpc(host_id, event, data, *, timeout=10.0):
        assert host_id == sample_host.id
        assert data["command"] == "set_device_swipe_trail"
        assert data["payload"]["enabled"] is True
        assert data["payload"]["serials"] == [sample_device.serial]
        return {
            "ok": True,
            "results": [{"serial": sample_device.serial, "ok": True}],
        }

    monkeypatch.setattr(
        "backend.services.device_swipe_trail.call_agent_rpc",
        fake_rpc,
    )
    response = client.post(
        "/api/v1/devices/bulk-swipe-trail",
        json={"device_ids": [sample_device.id], "enabled": True},
        headers=admin_headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["enabled"] is True
    assert data["ok"] == 1
    assert data["failed"] == 0
    assert data["skipped"] == 0
    assert data["results"][0]["status"] == "ok"


def test_bulk_swipe_trail_forbidden_for_non_admin(
    client, sample_device, auth_headers,
):
    response = client.post(
        "/api/v1/devices/bulk-swipe-trail",
        json={"device_ids": [sample_device.id], "enabled": False},
        headers=auth_headers,
    )
    assert response.status_code == 403


def test_bulk_swipe_trail_empty_ids(client, admin_headers):
    response = client.post(
        "/api/v1/devices/bulk-swipe-trail",
        json={"device_ids": [], "enabled": True},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_bulk_swipe_trail_missing_device(client, admin_headers):
    response = client.post(
        "/api/v1/devices/bulk-swipe-trail",
        json={"device_ids": [999999], "enabled": True},
        headers=admin_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_bulk_swipe_trail_skips_offline_host(
    client, db_session, sample_offline_host, admin_headers, monkeypatch,
):
    device = Device(
        serial="OFF-SWIPE-1",
        host_id=sample_offline_host.id,
        status="ONLINE",
    )
    db_session.add(device)
    db_session.commit()
    db_session.refresh(device)

    called = False

    async def fake_rpc(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not RPC offline host")

    monkeypatch.setattr(
        "backend.services.device_swipe_trail.call_agent_rpc",
        fake_rpc,
    )
    response = client.post(
        "/api/v1/devices/bulk-swipe-trail",
        json={"device_ids": [device.id], "enabled": False},
        headers=admin_headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["skipped"] == 1
    assert data["ok"] == 0
    assert called is False
    assert data["results"][0]["error"] == "host offline"


@pytest.mark.asyncio
async def test_bulk_swipe_trail_agent_offline_counts_failed(
    client, sample_device, admin_headers, monkeypatch,
):
    async def fake_rpc(*args, **kwargs):
        raise AgentNotConnectedError("down")

    monkeypatch.setattr(
        "backend.services.device_swipe_trail.call_agent_rpc",
        fake_rpc,
    )
    response = client.post(
        "/api/v1/devices/bulk-swipe-trail",
        json={"device_ids": [sample_device.id], "enabled": True},
        headers=admin_headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["failed"] == 1
    assert data["ok"] == 0
    assert data["results"][0]["error"] == "agent not connected"
