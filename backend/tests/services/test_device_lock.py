"""Tests for DeviceLockService (acquire / extend / release)."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.core.database import AsyncSessionLocal, SessionLocal
from backend.models.enums import HostStatus
from backend.models.host import Device, Host
from backend.services.device_lock import (
    acquire_lock,
    extend_lock,
    release_lock,
)


def _seed_device(status: str = "ONLINE", lock_run_id=None, lock_expires_at=None) -> dict:
    """Create a Host + Device row and return their IDs."""
    suffix = uuid4().hex[:8]
    host_id = f"lock-host-{suffix}"
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        host = Host(
            id=host_id,
            hostname=f"host-{suffix}",
            status=HostStatus.ONLINE.value,
            created_at=now,
        )
        device = Device(
            serial=f"SER-{suffix}",
            host_id=host_id,
            status=status,
            tags=[],
            created_at=now,
            lock_run_id=lock_run_id,
            lock_expires_at=lock_expires_at,
        )
        db.add_all([host, device])
        db.commit()
        return {"host_id": host_id, "device_id": device.id}
    finally:
        db.close()


def _get_device(device_id: int) -> Device:
    db = SessionLocal()
    try:
        d = db.get(Device, device_id)
        # Detach to avoid lazy-load issues
        db.expunge(d)
        return d
    finally:
        db.close()


# ── acquire_lock ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_acquire_lock_free_device():
    """Acquire succeeds on a device with no existing lock."""
    seed = _seed_device()
    async with AsyncSessionLocal() as db:
        result = await acquire_lock(db, seed["device_id"], job_id=999, lease_seconds=600)
        await db.commit()
    assert result is True
    d = _get_device(seed["device_id"])
    assert d.lock_run_id == 999
    assert d.status == "BUSY"
    assert d.lock_expires_at is not None


@pytest.mark.asyncio
async def test_acquire_lock_expired_lease():
    """Acquire succeeds when existing lock has expired."""
    expired = datetime.now(timezone.utc) - timedelta(seconds=60)
    seed = _seed_device(status="BUSY", lock_run_id=100, lock_expires_at=expired)
    async with AsyncSessionLocal() as db:
        result = await acquire_lock(db, seed["device_id"], job_id=200, lease_seconds=600)
        await db.commit()
    assert result is True
    d = _get_device(seed["device_id"])
    assert d.lock_run_id == 200


@pytest.mark.asyncio
async def test_acquire_lock_contested():
    """Acquire fails when device is locked by another active job."""
    future = datetime.now(timezone.utc) + timedelta(seconds=300)
    seed = _seed_device(status="BUSY", lock_run_id=100, lock_expires_at=future)
    async with AsyncSessionLocal() as db:
        result = await acquire_lock(db, seed["device_id"], job_id=200, lease_seconds=600)
        await db.commit()
    assert result is False
    d = _get_device(seed["device_id"])
    assert d.lock_run_id == 100  # unchanged


@pytest.mark.asyncio
async def test_acquire_lock_idempotent():
    """Re-acquiring with the same job_id extends the lease."""
    future = datetime.now(timezone.utc) + timedelta(seconds=300)
    seed = _seed_device(status="BUSY", lock_run_id=100, lock_expires_at=future)
    async with AsyncSessionLocal() as db:
        result = await acquire_lock(db, seed["device_id"], job_id=100, lease_seconds=600)
        await db.commit()
    assert result is True
    d = _get_device(seed["device_id"])
    assert d.lock_run_id == 100
    assert d.lock_expires_at > future  # lease was extended


# ── extend_lock ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_extend_lock_valid():
    """Extend succeeds when lock is held by the same job."""
    future = datetime.now(timezone.utc) + timedelta(seconds=60)
    seed = _seed_device(status="BUSY", lock_run_id=100, lock_expires_at=future)
    async with AsyncSessionLocal() as db:
        result = await extend_lock(db, seed["device_id"], job_id=100, lease_seconds=600)
        await db.commit()
    assert result is True
    d = _get_device(seed["device_id"])
    assert d.lock_expires_at > future


@pytest.mark.asyncio
async def test_extend_lock_stolen():
    """Extend fails when lock is held by a different job."""
    future = datetime.now(timezone.utc) + timedelta(seconds=300)
    seed = _seed_device(status="BUSY", lock_run_id=100, lock_expires_at=future)
    async with AsyncSessionLocal() as db:
        result = await extend_lock(db, seed["device_id"], job_id=200, lease_seconds=600)
        await db.commit()
    assert result is False


@pytest.mark.asyncio
async def test_extend_lock_unlocked():
    """Extend fails when device has no lock."""
    seed = _seed_device()
    async with AsyncSessionLocal() as db:
        result = await extend_lock(db, seed["device_id"], job_id=100, lease_seconds=600)
        await db.commit()
    assert result is False


# ── release_lock ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_release_lock_valid():
    """Release succeeds and restores ONLINE status."""
    future = datetime.now(timezone.utc) + timedelta(seconds=300)
    seed = _seed_device(status="BUSY", lock_run_id=100, lock_expires_at=future)
    async with AsyncSessionLocal() as db:
        result = await release_lock(db, seed["device_id"], job_id=100)
        await db.commit()
    assert result is True
    d = _get_device(seed["device_id"])
    assert d.lock_run_id is None
    assert d.lock_expires_at is None
    assert d.status == "ONLINE"


@pytest.mark.asyncio
async def test_release_lock_wrong_owner():
    """Release fails when lock is held by a different job."""
    future = datetime.now(timezone.utc) + timedelta(seconds=300)
    seed = _seed_device(status="BUSY", lock_run_id=100, lock_expires_at=future)
    async with AsyncSessionLocal() as db:
        result = await release_lock(db, seed["device_id"], job_id=200)
        await db.commit()
    assert result is False
    d = _get_device(seed["device_id"])
    assert d.lock_run_id == 100  # unchanged
