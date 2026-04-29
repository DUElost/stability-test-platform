"""PostgreSQL-only test for partial unique index (ADR-0019 Phase 1).

The partial unique index ``uq_device_leases_active_per_device`` is created
via raw SQL in the Alembic migration.  It is NOT declared in the ORM
``__table_args__``, so SQLite test databases (even in-memory) will not
have it.  These tests must be skipped when not running against PostgreSQL.
"""

import os

import pytest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
IS_PG = TEST_DATABASE_URL.startswith("postgresql")

pytestmark = pytest.mark.skipif(not IS_PG, reason="Partial unique index requires PostgreSQL")


class TestDeviceLeasesUniqueConstraint:
    """PostgreSQL-only: partial unique index enforcement."""

    @pytest.mark.asyncio(loop_scope="module")
    async def test_two_active_leases_same_device_fails(self):
        """A second ACTIVE lease on the same device must be rejected."""
        from datetime import datetime, timedelta, timezone
        from uuid import uuid4

        from backend.core.database import AsyncSessionLocal
        from backend.models.enums import HostStatus, LeaseStatus, LeaseType
        from backend.models.host import Device, Host
        from backend.models.device_lease import DeviceLease
        from sqlalchemy.exc import IntegrityError

        suffix = uuid4().hex[:8]
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=600)

        async with AsyncSessionLocal() as db:
            host = Host(
                id=f"uq-host-{suffix}",
                hostname=f"host-{suffix}",
                status=HostStatus.ONLINE.value,
                created_at=now,
            )
            device = Device(
                serial=f"UQ-SER-{suffix}",
                host_id=host.id,
                status="ONLINE",
                tags=[],
                created_at=now,
            )
            db.add_all([host, device])
            await db.flush()

            # First ACTIVE lease — should succeed
            lease1 = DeviceLease(
                device_id=device.id,
                job_id=None,
                host_id=host.id,
                lease_type=LeaseType.MAINTENANCE.value,
                status=LeaseStatus.ACTIVE.value,
                fencing_token=f"{device.id}:1",
                lease_generation=1,
                agent_instance_id="test",
                reason="test-1",
                holder="admin",
                acquired_at=now,
                renewed_at=now,
                expires_at=expires,
            )
            db.add(lease1)
            await db.flush()

            # Second ACTIVE lease on same device — must fail
            lease2 = DeviceLease(
                device_id=device.id,
                job_id=None,
                host_id=host.id,
                lease_type=LeaseType.MAINTENANCE.value,
                status=LeaseStatus.ACTIVE.value,
                fencing_token=f"{device.id}:2",
                lease_generation=2,
                agent_instance_id="test-2",
                reason="test-2",
                holder="admin",
                acquired_at=now,
                renewed_at=now,
                expires_at=expires,
            )
            db.add(lease2)

            with pytest.raises(IntegrityError):
                await db.flush()

            await db.rollback()

    @pytest.mark.asyncio(loop_scope="module")
    async def test_released_then_new_active_ok(self):
        """After a lease is RELEASED, a new ACTIVE lease on the same device
        should be allowed."""
        from datetime import datetime, timedelta, timezone
        from uuid import uuid4

        from backend.core.database import AsyncSessionLocal
        from backend.models.enums import HostStatus, LeaseStatus, LeaseType
        from backend.models.host import Device, Host
        from backend.models.device_lease import DeviceLease

        suffix = uuid4().hex[:8]
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=600)

        async with AsyncSessionLocal() as db:
            host = Host(
                id=f"uq-host-{suffix}",
                hostname=f"host-{suffix}",
                status=HostStatus.ONLINE.value,
                created_at=now,
            )
            device = Device(
                serial=f"UQR-SER-{suffix}",
                host_id=host.id,
                status="ONLINE",
                tags=[],
                created_at=now,
            )
            db.add_all([host, device])
            await db.flush()

            # Create and release first lease
            lease1 = DeviceLease(
                device_id=device.id,
                job_id=None,
                host_id=host.id,
                lease_type=LeaseType.MAINTENANCE.value,
                status=LeaseStatus.ACTIVE.value,
                fencing_token=f"{device.id}:1",
                lease_generation=1,
                agent_instance_id="test",
                reason="test-1",
                holder="admin",
                acquired_at=now,
                renewed_at=now,
                expires_at=expires,
            )
            db.add(lease1)
            await db.flush()

            lease1.status = LeaseStatus.RELEASED.value
            lease1.released_at = datetime.now(timezone.utc)
            await db.flush()

            # New ACTIVE lease on same device — should succeed
            lease2 = DeviceLease(
                device_id=device.id,
                job_id=None,
                host_id=host.id,
                lease_type=LeaseType.MAINTENANCE.value,
                status=LeaseStatus.ACTIVE.value,
                fencing_token=f"{device.id}:2",
                lease_generation=2,
                agent_instance_id="test-2",
                reason="test-2",
                holder="admin",
                acquired_at=now,
                renewed_at=now,
                expires_at=expires,
            )
            db.add(lease2)
            await db.flush()

            assert lease2.id is not None
            await db.rollback()
