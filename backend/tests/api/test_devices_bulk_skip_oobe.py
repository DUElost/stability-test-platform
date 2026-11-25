"""POST /api/v1/devices/bulk-skip-oobe"""

from __future__ import annotations

import pytest

from backend.models.audit import AuditLog
from backend.models.host import Device
from backend.models.script import Script
from backend.realtime.socketio_server import AgentNotConnectedError


def _ensure_oobe_skip_script(
    db_session,
    *,
    version: str = "1.1.3",
    package_sha256: str | None = "b" * 64,
    is_active: bool = True,
    default_params: dict | None = None,
) -> Script:
    existing = (
        db_session.query(Script)
        .filter(Script.name == "oobe_skip", Script.version == version)
        .first()
    )
    if existing is not None:
        existing.package_sha256 = package_sha256
        existing.is_active = is_active
        existing.default_params = (
            default_params
            if default_params is not None
            else {"locales": "en-US", "wait_for_device_seconds": 120}
        )
        db_session.commit()
        db_session.refresh(existing)
        return existing

    script = Script(
        name="oobe_skip",
        display_name="oobe_skip",
        category="device",
        script_type="python",
        version=version,
        nfs_path=f"oobe_skip/v{version}/oobe_skip.py",
        content_sha256="c" * 64,
        package_sha256=package_sha256,
        default_params=(
            default_params
            if default_params is not None
            else {"locales": "en-US", "wait_for_device_seconds": 120}
        ),
        is_active=is_active,
    )
    db_session.add(script)
    db_session.commit()
    db_session.refresh(script)
    return script


def _deactivate_all_oobe_skip(db_session) -> None:
    db_session.query(Script).filter(Script.name == "oobe_skip").update(
        {"is_active": False}
    )
    db_session.commit()


@pytest.mark.asyncio
async def test_bulk_skip_oobe_success_uses_latest_packaged_script_and_audits(
    client, db_session, sample_device, sample_host, admin_headers, monkeypatch,
):
    _deactivate_all_oobe_skip(db_session)
    _ensure_oobe_skip_script(db_session, version="1.1.2", package_sha256="1" * 64)
    _ensure_oobe_skip_script(
        db_session,
        version="1.1.3",
        package_sha256="2" * 64,
        default_params={"locales": "zh-CN", "wait_for_device_seconds": 120},
    )

    async def fake_rpc(host_id, event, data, *, timeout=10.0):
        assert host_id == sample_host.id
        assert event == "control"
        assert data["command"] == "skip_device_oobe"
        payload = data["payload"]
        assert payload["serials"] == [sample_device.serial]
        assert payload["script"]["name"] == "oobe_skip"
        assert payload["script"]["version"] == "1.1.3"
        assert payload["script"]["package_sha256"] == "2" * 64
        assert payload["params"]["locales"] == "zh-CN"
        assert payload["params"]["wait_for_device_seconds"] == 5
        assert payload["params"]["verify_retries"] == 1
        assert timeout >= 60.0
        return {
            "ok": True,
            "script_version": "1.1.3",
            "results": [{"serial": sample_device.serial, "ok": True}],
        }

    monkeypatch.setattr(
        "backend.services.device_skip_oobe.call_agent_rpc",
        fake_rpc,
    )
    response = client.post(
        "/api/v1/devices/bulk-skip-oobe",
        json={"device_ids": [sample_device.id]},
        headers=admin_headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["script_version"] == "1.1.3"
    assert data["ok"] == 1
    assert data["failed"] == 0
    assert data["skipped"] == 0
    assert data["results"][0]["status"] == "ok"

    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "bulk_skip_oobe")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert audit is not None
    assert audit.details["script_version"] == "1.1.3"
    assert audit.details["ok"] == 1


def test_bulk_skip_oobe_forbidden_for_non_admin(
    client, sample_device, auth_headers,
):
    response = client.post(
        "/api/v1/devices/bulk-skip-oobe",
        json={"device_ids": [sample_device.id]},
        headers=auth_headers,
    )
    assert response.status_code == 403


def test_bulk_skip_oobe_empty_ids(client, admin_headers):
    response = client.post(
        "/api/v1/devices/bulk-skip-oobe",
        json={"device_ids": []},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_bulk_skip_oobe_missing_device(client, admin_headers):
    response = client.post(
        "/api/v1/devices/bulk-skip-oobe",
        json={"device_ids": [999999]},
        headers=admin_headers,
    )
    assert response.status_code == 404


def test_bulk_skip_oobe_returns_409_when_no_active_script(
    client, db_session, sample_device, admin_headers,
):
    _deactivate_all_oobe_skip(db_session)
    response = client.post(
        "/api/v1/devices/bulk-skip-oobe",
        json={"device_ids": [sample_device.id]},
        headers=admin_headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "OOBE_SKIP_SCRIPT_NOT_FOUND"


def test_bulk_skip_oobe_returns_409_when_script_unpackaged(
    client, db_session, sample_device, admin_headers,
):
    _deactivate_all_oobe_skip(db_session)
    _ensure_oobe_skip_script(db_session, version="1.1.3", package_sha256=None)
    response = client.post(
        "/api/v1/devices/bulk-skip-oobe",
        json={"device_ids": [sample_device.id]},
        headers=admin_headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "OOBE_SKIP_SCRIPT_UNPACKAGED"


@pytest.mark.asyncio
async def test_bulk_skip_oobe_skips_offline_host_no_host_and_busy_device(
    client, db_session, sample_host, sample_offline_host, admin_headers, monkeypatch,
):
    _deactivate_all_oobe_skip(db_session)
    _ensure_oobe_skip_script(db_session, version="1.1.3", package_sha256="3" * 64)

    dev_offline_host = Device(
        serial="OFF-OOBE-1",
        host_id=sample_offline_host.id,
        status="ONLINE",
    )
    dev_no_host = Device(
        serial="NOHOST-OOBE-1",
        host_id=None,
        status="ONLINE",
    )
    dev_busy = Device(
        serial="BUSY-OOBE-1",
        host_id=sample_host.id,
        status="BUSY",
    )
    db_session.add_all([dev_offline_host, dev_no_host, dev_busy])
    db_session.commit()
    for dev in (dev_offline_host, dev_no_host, dev_busy):
        db_session.refresh(dev)

    called = False

    async def fake_rpc(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not RPC when all selected devices are skipped")

    monkeypatch.setattr(
        "backend.services.device_skip_oobe.call_agent_rpc",
        fake_rpc,
    )
    response = client.post(
        "/api/v1/devices/bulk-skip-oobe",
        json={"device_ids": [dev_offline_host.id, dev_no_host.id, dev_busy.id]},
        headers=admin_headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["skipped"] == 3
    assert data["ok"] == 0
    assert data["failed"] == 0
    assert called is False
    by_serial = {r["serial"]: r for r in data["results"]}
    assert by_serial["OFF-OOBE-1"]["error"] == "host offline"
    assert by_serial["NOHOST-OOBE-1"]["error"] == "no host"
    assert by_serial["BUSY-OOBE-1"]["error"] == "device busy"


@pytest.mark.asyncio
async def test_bulk_skip_oobe_agent_offline_counts_failed(
    client, db_session, sample_device, admin_headers, monkeypatch,
):
    _deactivate_all_oobe_skip(db_session)
    _ensure_oobe_skip_script(db_session, version="1.1.3", package_sha256="4" * 64)

    async def fake_rpc(*args, **kwargs):
        raise AgentNotConnectedError("down")

    monkeypatch.setattr(
        "backend.services.device_skip_oobe.call_agent_rpc",
        fake_rpc,
    )
    response = client.post(
        "/api/v1/devices/bulk-skip-oobe",
        json={"device_ids": [sample_device.id]},
        headers=admin_headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["failed"] == 1
    assert data["ok"] == 0
    assert data["results"][0]["error"] == "agent not connected"
