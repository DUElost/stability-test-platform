"""Tests for DeviceLease ORM model (ADR-0019 Phase 1)."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.models.enums import HostStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.device_lease import DeviceLease


@pytest.fixture
def seed(db_session):
    """Create Host + Device in the test DB."""
    suffix = uuid4().hex[:8]
    host = Host(
        id=f"lease-host-{suffix}",
        hostname=f"host-{suffix}",
        status=HostStatus.ONLINE.value,
        created_at=datetime.now(timezone.utc),
    )
    device = Device(
        serial=f"SER-{suffix}",
        host_id=host.id,
        status="ONLINE",
        tags=[],
        created_at=datetime.now(timezone.utc),
    )
    db_session.add_all([host, device])
    db_session.flush()
    return {"host_id": host.id, "device_id": device.id}


class TestDeviceLeaseModel:

    def test_create_lease(self, db_session, seed):
        now = datetime.now(timezone.utc)
        lease = DeviceLease(
            device_id=seed["device_id"],
            job_id=None,
            host_id=seed["host_id"],
            lease_type=LeaseType.MAINTENANCE.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{seed['device_id']}:1",
            lease_generation=1,
            agent_instance_id="test-agent-001",
            reason="manual inspection",
            holder="admin",
            acquired_at=now,
            renewed_at=now,
            expires_at=now + timedelta(seconds=600),
        )
        db_session.add(lease)
        db_session.flush()

        fetched = db_session.get(DeviceLease, lease.id)
        assert fetched is not None
        assert fetched.device_id == seed["device_id"]
        assert fetched.lease_type == LeaseType.MAINTENANCE.value
        assert fetched.status == LeaseStatus.ACTIVE.value
        assert fetched.fencing_token == f"{seed['device_id']}:1"
        assert fetched.reason == "manual inspection"
        assert fetched.holder == "admin"
        assert fetched.job_id is None

    def test_lease_with_job(self, db_session, seed):
        now = datetime.now(timezone.utc)
        lease = DeviceLease(
            device_id=seed["device_id"],
            job_id=42,
            host_id=seed["host_id"],
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{seed['device_id']}:2",
            lease_generation=2,
            agent_instance_id="test-agent-002",
            acquired_at=now,
            renewed_at=now,
            expires_at=now + timedelta(seconds=600),
        )
        db_session.add(lease)
        db_session.flush()

        fetched = db_session.get(DeviceLease, lease.id)
        assert fetched.job_id == 42
        assert fetched.lease_type == LeaseType.JOB.value
        assert fetched.reason is None
        assert fetched.holder is None

    def test_release_lease(self, db_session, seed):
        now = datetime.now(timezone.utc)
        lease = DeviceLease(
            device_id=seed["device_id"],
            job_id=99,
            host_id=seed["host_id"],
            lease_type=LeaseType.SCRIPT.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{seed['device_id']}:3",
            lease_generation=3,
            agent_instance_id="test-agent-003",
            acquired_at=now,
            renewed_at=now,
            expires_at=now + timedelta(seconds=600),
        )
        db_session.add(lease)
        db_session.flush()

        lease.status = LeaseStatus.RELEASED.value
        lease.released_at = datetime.now(timezone.utc)
        db_session.flush()

        fetched = db_session.get(DeviceLease, lease.id)
        assert fetched.status == LeaseStatus.RELEASED.value
        assert fetched.released_at is not None
