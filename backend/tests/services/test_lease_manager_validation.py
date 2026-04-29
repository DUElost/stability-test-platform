"""Tests for LeaseManager input validation (ADR-0019 Phase 1).

These tests verify the required-fields contract without needing a full
transaction context:

  - JOB / SCRIPT must have job_id
  - MAINTENANCE must have reason + holder
"""

import pytest

from backend.models.enums import LeaseType
from backend.services.lease_manager import acquire_lease


class TestAcquireLeaseValidation:
    """Input validation happens before any DB interaction."""

    @pytest.mark.asyncio
    async def test_job_requires_job_id(self):
        with pytest.raises(ValueError, match="requires job_id"):
            await acquire_lease(
                db=None,  # type: ignore — validation fails before DB access
                device_id=1,
                host_id="h1",
                lease_type=LeaseType.JOB,
                agent_instance_id="a1",
                job_id=None,
            )

    @pytest.mark.asyncio
    async def test_script_requires_job_id(self):
        with pytest.raises(ValueError, match="requires job_id"):
            await acquire_lease(
                db=None,  # type: ignore
                device_id=1,
                host_id="h1",
                lease_type=LeaseType.SCRIPT,
                agent_instance_id="a1",
                job_id=None,
            )

    @pytest.mark.asyncio
    async def test_maintenance_requires_reason(self):
        with pytest.raises(ValueError, match="requires reason and holder"):
            await acquire_lease(
                db=None,  # type: ignore
                device_id=1,
                host_id="h1",
                lease_type=LeaseType.MAINTENANCE,
                agent_instance_id="a1",
                reason="",
                holder="admin",
            )

    @pytest.mark.asyncio
    async def test_maintenance_requires_holder(self):
        with pytest.raises(ValueError, match="requires reason and holder"):
            await acquire_lease(
                db=None,  # type: ignore
                device_id=1,
                host_id="h1",
                lease_type=LeaseType.MAINTENANCE,
                agent_instance_id="a1",
                reason="maintenance",
                holder="",
            )

    @pytest.mark.asyncio
    async def test_maintenance_valid_no_job_id(self):
        """MAINTENANCE with reason+holder is valid even without job_id."""
        # Validation passes (would fail later at DB when db=None,
        # but the ValueError should NOT be raised at validation stage).
        try:
            await acquire_lease(
                db=None,  # type: ignore
                device_id=1,
                host_id="h1",
                lease_type=LeaseType.MAINTENANCE,
                agent_instance_id="a1",
                reason="manual inspection",
                holder="admin",
                job_id=None,
            )
        except ValueError:
            pytest.fail("MAINTENANCE with reason+holder should not raise ValueError")
        except Exception:
            # Expected: db=None will cause AttributeError later, not ValueError
            pass

    @pytest.mark.asyncio
    async def test_job_with_id_valid(self):
        """JOB with job_id should pass validation."""
        try:
            await acquire_lease(
                db=None,  # type: ignore
                device_id=1,
                host_id="h1",
                lease_type=LeaseType.JOB,
                agent_instance_id="a1",
                job_id=42,
            )
        except ValueError:
            pytest.fail("JOB with job_id should not raise ValueError")
        except Exception:
            # Expected: db=None will cause AttributeError later
            pass
