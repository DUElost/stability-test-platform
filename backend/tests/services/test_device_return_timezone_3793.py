"""B6-U9 / #3723 §3.9: real debt entry points on isolated PostgreSQL."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from backend.core.database import AsyncSessionLocal, get_db
from backend.main import fastapi_app
from backend.models.device_lease import DeviceLease
from backend.models.device_return_debt import DeviceReturnDebt
from backend.scripts import init_device_return as init_cli
from backend.services.device_retirement import retire_device, unretire_device
from backend.services.device_return import (
    handle_job_lease_release,
    handle_job_lease_release_sync,
    is_debt_lease,
    open_debts_for_host_sync,
)
from backend.tests.services.test_device_return_u3_3728 import _seed

EXPECTED_EXPIRY = datetime(9999, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def timezone_sessions(request, engine, client, monkeypatch):
    # Only this test's connections change timezone; the shared/container DB and
    # application configuration stay untouched. Every CLI/API session uses psycopg.
    zoned_engine = create_engine(
        engine.url,
        connect_args={"options": f"-c timezone={request.param}"},
    )
    sessions = sessionmaker(bind=zoned_engine)

    def test_db():
        with sessions() as db:
            assert db.scalar(text("SHOW TIMEZONE")) == request.param
            yield db

    monkeypatch.setattr(init_cli, "SessionLocal", sessions)
    monkeypatch.setitem(fastapi_app.dependency_overrides, get_db, test_db)
    try:
        with sessions() as db:
            assert db.scalar(text("SHOW TIMEZONE")) == request.param
        yield sessions
    finally:
        zoned_engine.dispose()


def _initialize_stock_debt(db_session):
    device, _job, lease, host = _seed(db_session)
    # A historical failed return on a device whose old JOB lease is gone.
    db_session.delete(lease)
    db_session.commit()
    assert init_cli.main(["--apply"]) == 0
    return device.id, host.id, device.serial


@pytest.mark.parametrize(
    "timezone_sessions", ["Asia/Shanghai", "Pacific/Kiritimati"], indirect=True,
)
@pytest.mark.parametrize("entry", ["open_debts", "heartbeat", "override"])
def test_initialized_debt_non_utc_entry_points(
    db_session, timezone_sessions, client, admin_headers, entry,
):
    """§3.9.4a: each consumer independently reads a real CLI-created lease.

    Keeping consumers independent makes the old constant fail at all three
    entry points, rather than stopping the test at the first failing query.
    """
    device_id, host_id, serial = _initialize_stock_debt(db_session)
    with timezone_sessions() as db:
        lease_id, token = db.execute(
            select(DeviceLease.id, DeviceLease.fencing_token).where(
                DeviceLease.device_id == device_id, DeviceLease.status == "ACTIVE",
            )
        ).one()
    expected = [{
        "device_id": device_id, "serial": serial,
        "fencing_token": token, "families": ["power-cycle"],
    }]

    if entry == "open_debts":
        with timezone_sessions() as db:
            assert open_debts_for_host_sync(db, host_id) == expected
    elif entry == "heartbeat":
        response = client.post("/api/v1/heartbeat", json={
            "host_id": host_id, "status": "ONLINE",
            "devices": [{
                "serial": serial, "adb_connected": True, "adb_state": "device",
            }],
        })
        assert response.status_code == 200, response.text
        assert response.json()["return_debts"] == expected
    else:
        response = client.post(
            f"/api/v1/devices/{device_id}/return-override",
            headers=admin_headers,
            json={
                "families": ["power-cycle"], "reason_category": "flashed",
                "evidence_ref": "test:B6-U9",
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()["data"]
        assert result["closed_count"] == 1
        assert result["open_debt_count"] == 0
        assert result["debt_lease_released"] is True

    # §3.9.4c initialization branch; refresh from PG, not an ORM identity cache.
    with timezone_sessions() as db:
        lease = db.get(DeviceLease, lease_id)
        assert lease.expires_at == EXPECTED_EXPIRY
        assert is_debt_lease(lease)
        assert lease.status == ("RELEASED" if entry == "override" else "ACTIVE")
        if entry == "override":
            assert db.scalars(select(DeviceReturnDebt).where(
                DeviceReturnDebt.device_id == device_id,
                DeviceReturnDebt.state == "open",
            )).all() == []


@pytest.mark.parametrize("in_place", [True, False], ids=["convert", "acquire"])
def test_terminal_sync_writes_safe_expiry(db_session, in_place):
    """§3.9.4c: sync terminal release, both conversion and missing-lease paths."""
    device, job, lease, _host = _seed(db_session)
    original_id = lease.id
    if not in_place:
        db_session.delete(lease)
        db_session.commit()
    outcome = handle_job_lease_release_sync(
        db_session, device_id=device.id, job_id=job.id,
    )
    db_session.commit()
    db_session.expire_all()
    active = db_session.scalars(select(DeviceLease).where(
        DeviceLease.device_id == device.id, DeviceLease.status == "ACTIVE",
    )).one()
    assert outcome.converted_to_debt is True
    assert is_debt_lease(active)
    assert (active.id == original_id) is in_place
    assert active.expires_at == EXPECTED_EXPIRY


@pytest.mark.parametrize("in_place", [True, False], ids=["convert", "acquire"])
async def test_terminal_async_writes_safe_expiry(db_session, in_place):
    """§3.9.4c: the real async service also covers both branches."""
    device, job, lease, _host = _seed(db_session)
    device_id, job_id, original_id = device.id, job.id, lease.id
    if not in_place:
        db_session.delete(lease)
        db_session.commit()
    async with AsyncSessionLocal() as db:
        outcome = await handle_job_lease_release(
            db, device_id=device_id, job_id=job_id,
        )
        await db.commit()
    db_session.expire_all()
    active = db_session.scalars(select(DeviceLease).where(
        DeviceLease.device_id == device_id, DeviceLease.status == "ACTIVE",
    )).one()
    assert outcome.converted_to_debt is True
    assert is_debt_lease(active)
    assert (active.id == original_id) is in_place
    assert active.expires_at == EXPECTED_EXPIRY


def test_unretire_with_debt_writes_safe_expiry(db_session):
    """§3.9.4c: cancellation of retirement recreates a safe debt lease."""
    device_id, _host_id, _serial = _initialize_stock_debt(db_session)
    original = db_session.scalars(select(DeviceLease).where(
        DeviceLease.device_id == device_id, DeviceLease.status == "ACTIVE",
    )).one()
    original_id = original.id
    retire_device(
        db_session, device_id=device_id, reason="test U9 retirement",
        actor_id=None, actor_username="admin",
    )
    db_session.refresh(original)
    assert original.status == "RELEASED"
    restored = unretire_device(
        db_session, device_id=device_id, reason="test U9 unretire",
        actor_id=None, actor_username="admin",
    )
    assert restored.retired_at is None
    db_session.expire_all()
    active = db_session.scalars(select(DeviceLease).where(
        DeviceLease.device_id == device_id, DeviceLease.status == "ACTIVE",
    )).one()
    assert active.id != original_id
    assert is_debt_lease(active)
    assert active.expires_at == EXPECTED_EXPIRY
