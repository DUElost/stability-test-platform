"""PostgreSQL-only tests for ADR-0019 Phase 2a dual-write (claim/extend/complete).

Verifies that device_leases rows are created, extended, and released
alongside the existing lock_run_id/lock_expires_at paths.

All tests create real WorkflowDefinition + TaskTemplate + WorkflowRun +
JobInstance to satisfy FK constraints on device_leases.job_id.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from sqlalchemy import select

from backend.core.database import AsyncSessionLocal, SessionLocal
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobInstance, TaskTemplate as JobTaskTemplate
from backend.models.workflow import WorkflowDefinition, WorkflowRun
from backend.services.device_lock import acquire_lock, extend_lock, release_lock
from backend.services.lease_manager import (
    acquire_lease,
    extend_lease,
    release_lease,
    release_lease_sync,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
IS_PG = TEST_DATABASE_URL.startswith("postgresql")

pytestmark = pytest.mark.skipif(not IS_PG, reason="Dual-write tests require PostgreSQL (savepoints + partial unique index)")


async def _create_seed(db, suffix, host_id=None, serial=None):
    """Create Host + Device + WorkflowFKs + JobInstance (PENDING).
    Returns (host, device, job) with flushed IDs."""
    now = datetime.now(timezone.utc)
    hid = host_id or f"dwh-{suffix}"
    ser = serial or f"DWS-{suffix}"

    host = Host(id=hid, hostname=f"h-{suffix}", status=HostStatus.ONLINE.value, created_at=now)
    device = Device(serial=ser, host_id=host.id, status="ONLINE", tags=[], created_at=now)
    db.add_all([host, device])
    await db.flush()

    wd = WorkflowDefinition(name=f"dwf-{suffix}", failure_threshold=0.1, created_by="test")
    db.add(wd)
    await db.flush()
    tt = JobTaskTemplate(
        workflow_definition_id=wd.id, name=f"dwt-{suffix}",
        pipeline_def={"version": 1, "stages": []}, sort_order=0,
    )
    db.add(tt)
    await db.flush()
    wr = WorkflowRun(workflow_definition_id=wd.id, status="RUNNING", failure_threshold=0.1, triggered_by="test")
    db.add(wr)
    await db.flush()

    job = JobInstance(
        workflow_run_id=wr.id, task_template_id=tt.id,
        device_id=device.id, host_id=host.id,
        status=JobStatus.PENDING.value,
        pipeline_def={"version": 1, "stages": []},
    )
    db.add(job)
    await db.flush()
    return host, device, job


class TestClaimDualWrite:
    """claim_jobs + get_pending_jobs both write device_leases."""

    @pytest.mark.asyncio(loop_scope="module")
    async def test_acquire_lock_and_lease_same_transaction(self):
        suffix = uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            host, device, job = await _create_seed(db, suffix)
            did, jid = device.id, job.id

            locked = await acquire_lock(db, did, jid, 600)
            assert locked
            db.expire(device)

            lease = await acquire_lease(
                db, device_id=did, host_id=host.id,
                lease_type=LeaseType.JOB, agent_instance_id=host.id, job_id=jid,
            )
            assert lease is not None
            assert lease.device_id == did
            assert lease.job_id == jid
            assert lease.lease_type == LeaseType.JOB.value
            assert lease.status == LeaseStatus.ACTIVE.value
            assert lease.fencing_token == f"{did}:{lease.lease_generation}"
            await db.commit()

            dev = await db.get(Device, did)
            assert dev.lock_run_id == jid
            assert dev.lock_expires_at is not None

            await db.rollback()

    @pytest.mark.asyncio(loop_scope="module")
    async def test_acquire_lease_none_on_conflict(self):
        """Second acquire_lease on same device returns None (already ACTIVE)."""
        suffix = uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            host, device, job = await _create_seed(db, suffix)
            did, jid = device.id, job.id

            # First lease — succeeds
            lease1 = await acquire_lease(
                db, device_id=did, host_id=host.id,
                lease_type=LeaseType.JOB, agent_instance_id=host.id, job_id=jid,
            )
            assert lease1 is not None
            await db.commit()

            # Create second JobInstance on the same device (different job_id)
            # Reuse existing WorkflowDefinition/TaskTemplate/WorkflowRun
            wd = (await db.execute(select(WorkflowDefinition).limit(1))).scalars().first()
            wr = (await db.execute(select(WorkflowRun).limit(1))).scalars().first()
            tt = (await db.execute(select(JobTaskTemplate).limit(1))).scalars().first()
            job2 = JobInstance(
                workflow_run_id=wr.id, task_template_id=tt.id,
                device_id=did, host_id=host.id,
                status=JobStatus.COMPLETED.value,
                pipeline_def={"version": 1, "stages": []},
            )
            db.add(job2)
            await db.flush()
            jid2 = job2.id
            await db.commit()

            # Second lease on same device — must return None
            async with db.begin_nested():
                locked = await acquire_lock(db, did, jid2, 600)
                if locked:
                    lease2 = await acquire_lease(
                        db, device_id=did, host_id=host.id,
                        lease_type=LeaseType.JOB, agent_instance_id=host.id, job_id=jid2,
                    )
                    assert lease2 is None, "acquire_lease should return None on conflict"

            await db.rollback()


class TestExtendDualWrite:
    """extend_lock + extend_lease dual-write."""

    @pytest.mark.asyncio(loop_scope="module")
    async def test_extend_lock_and_lease(self):
        suffix = uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            host, device, job = await _create_seed(db, suffix)
            did, jid = device.id, job.id

            lease = await acquire_lease(
                db, device_id=did, host_id=host.id,
                lease_type=LeaseType.JOB, agent_instance_id=host.id, job_id=jid,
            )
            original_expires = lease.expires_at
            original_renewed = lease.renewed_at
            # acquire_lock must precede for extend_lock to work
            await acquire_lock(db, did, jid, 600)
            await db.commit()

            await asyncio.sleep(0.01)
            assert await extend_lock(db, did, jid, 600)
            assert await extend_lease(db, did, jid, LeaseType.JOB, 600)

            await db.refresh(lease)
            assert lease.expires_at > original_expires
            assert lease.renewed_at > original_renewed

            await db.rollback()

    @pytest.mark.asyncio(loop_scope="module")
    async def test_extend_lease_miss_returns_false(self):
        """No ACTIVE lease → extend_lease returns False."""
        suffix = uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            host, device, _ = await _create_seed(db, suffix)
            await db.commit()  # commit Host/Device but no lease

            result = await extend_lease(db, device_id=device.id, job_id=99999, lease_type=LeaseType.JOB)
            assert result is False
            await db.rollback()


class TestCompleteDualWrite:
    """complete_job: release_lock + release_lease."""

    @pytest.mark.asyncio(loop_scope="module")
    async def test_release_lock_and_lease(self):
        suffix = uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            host, device, job = await _create_seed(db, suffix)
            did, jid = device.id, job.id

            await acquire_lock(db, did, jid, 600)
            lease = await acquire_lease(
                db, device_id=did, host_id=host.id,
                lease_type=LeaseType.JOB, agent_instance_id=host.id, job_id=jid,
            )
            lease_id = lease.id
            await db.commit()

            await release_lock(db, did, jid)
            rel_ok = await release_lease(db, did, jid, LeaseType.JOB)
            assert rel_ok
            await db.commit()

            db.expire_all()
            fetched = await db.get(DeviceLease, lease_id)
            assert fetched.status == LeaseStatus.RELEASED.value
            assert fetched.released_at is not None

            dev = await db.get(Device, did)
            assert dev.lock_run_id is None
            assert dev.lock_expires_at is None

            await db.rollback()

    @pytest.mark.asyncio(loop_scope="module")
    async def test_release_lease_miss_returns_false(self):
        """No ACTIVE lease → release_lease returns False."""
        suffix = uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            host, device, _ = await _create_seed(db, suffix)
            await db.commit()

            result = await release_lease(db, device_id=device.id, job_id=99999, lease_type=LeaseType.JOB)
            assert result is False
            await db.rollback()

    @pytest.mark.asyncio(loop_scope="module")
    async def test_release_lease_idempotent_returns_false(self):
        """Second release_lease on already-RELEASED lease returns False."""
        suffix = uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            host, device, job = await _create_seed(db, suffix)
            did, jid = device.id, job.id

            await acquire_lease(
                db, device_id=did, host_id=host.id,
                lease_type=LeaseType.JOB, agent_instance_id=host.id, job_id=jid,
            )
            await db.commit()

            assert await release_lease(db, did, jid, LeaseType.JOB)
            assert not await release_lease(db, did, jid, LeaseType.JOB)

            await db.rollback()


# ── Phase 2b: release_lease_sync ────────────────────────────────────────────

def test_release_lease_sync_marks_active_to_released():
    """release_lease_sync 将 ACTIVE lease 转为 RELEASED（同步版，供 recycler 使用）。"""
    from datetime import datetime, timezone as tz

    suffix = uuid4().hex[:8]
    now = datetime.now(tz.utc)
    expires = now + timedelta(seconds=600)
    db = SessionLocal()
    try:
        host = Host(
            id=f"sync-host-{suffix}", hostname=f"h-{suffix}",
            status=HostStatus.ONLINE.value, created_at=now,
        )
        device = Device(
            serial=f"SYNC-{suffix}", host_id=host.id,
            status="ONLINE", tags=[], created_at=now,
        )
        db.add_all([host, device])
        db.flush()

        wf = WorkflowDefinition(
            name=f"wf-{suffix}", description="sync test",
            failure_threshold=0.1, created_by="pytest",
            created_at=now, updated_at=now,
        )
        db.add(wf)
        db.flush()

        tpl = JobTaskTemplate(
            workflow_definition_id=wf.id, name=f"tpl-{suffix}",
            pipeline_def={"stages": {"prepare": [], "execute": [], "post_process": []}},
            sort_order=0, created_at=now,
        )
        db.add(tpl)
        db.flush()

        run = WorkflowRun(
            workflow_definition_id=wf.id,
            status="RUNNING", failure_threshold=0.1,
            triggered_by="pytest", started_at=now,
        )
        db.add(run)
        db.flush()

        job = JobInstance(
            workflow_run_id=run.id, task_template_id=tpl.id,
            device_id=device.id, host_id=host.id,
            status=JobStatus.RUNNING.value,
            pipeline_def={"stages": {"prepare": [], "execute": [], "post_process": []}},
            created_at=now, updated_at=now, started_at=now,
        )
        db.add(job)
        db.flush()

        lease = DeviceLease(
            device_id=device.id, job_id=job.id, host_id=host.id,
            lease_type=LeaseType.JOB.value, status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{device.id}:1", lease_generation=1,
            agent_instance_id=host.id, acquired_at=now,
            renewed_at=now, expires_at=expires,
        )
        db.add(lease)
        db.commit()

        # Act
        result = release_lease_sync(db, device.id, job.id, LeaseType.JOB)
        assert result is True

        db.expire_all()
        db.refresh(lease)
        assert lease.status == LeaseStatus.RELEASED.value
        assert lease.released_at is not None

        # Idempotent: second call returns False
        result2 = release_lease_sync(db, device.id, job.id, LeaseType.JOB)
        assert result2 is False
    finally:
        db.close()
