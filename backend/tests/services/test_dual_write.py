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
    """complete_job: release_lease (Phase 2c: device_leases is source of truth)."""

    @pytest.mark.asyncio(loop_scope="module")
    async def test_release_lock_and_lease(self):
        suffix = uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            host, device, job = await _create_seed(db, suffix)
            did, jid = device.id, job.id

            # Phase 2c: acquire_lease projects to device table; no separate acquire_lock needed
            lease = await acquire_lease(
                db, device_id=did, host_id=host.id,
                lease_type=LeaseType.JOB, agent_instance_id=host.id, job_id=jid,
            )
            lease_id = lease.id
            await db.commit()

            # Phase 2c: release_lease handles both lease release AND device projection
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
        # Phase 2c: project to device table (release_lease_sync requires it)
        device.status = "BUSY"
        device.lock_run_id = job.id
        device.lock_expires_at = expires
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


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2c: Source of Truth — service-level projection tests
# ══════════════════════════════════════════════════════════════════════════════


class TestAcquireLeaseProjection:
    """acquire_lease 必须正确投影到 device 表 (Phase 2c)."""

    @pytest.mark.asyncio(loop_scope="module")
    async def test_acquire_lease_projects_device_lock(self):
        """acquire_lease 成功后 device.status='BUSY', lock_run_id=job_id, lock_expires_at 非空."""
        suffix = uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            host, device, job = await _create_seed(db, suffix)
            did, jid = device.id, job.id

            lease = await acquire_lease(
                db, device_id=did, host_id=host.id,
                lease_type=LeaseType.JOB, agent_instance_id=host.id, job_id=jid,
            )
            assert lease is not None
            await db.commit()

            await db.refresh(device)
            assert device.status == "BUSY", "acquire_lease must set device status to BUSY"
            assert device.lock_run_id == jid, "acquire_lease must set lock_run_id"
            assert device.lock_expires_at is not None, "acquire_lease must set lock_expires_at"

            await db.rollback()

    @pytest.mark.asyncio(loop_scope="module")
    async def test_acquire_lease_conflict_returns_none_no_projection(self):
        """冲突时 acquire_lease 返回 None，device 表不被误写 BUSY."""
        suffix = uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            host, device, job = await _create_seed(db, suffix)
            did, jid = device.id, job.id

            # First acquire succeeds and projects
            lease1 = await acquire_lease(
                db, device_id=did, host_id=host.id,
                lease_type=LeaseType.JOB, agent_instance_id=host.id, job_id=jid,
            )
            assert lease1 is not None
            await db.commit()
            await db.refresh(device)
            first_lock_run_id = device.lock_run_id
            assert first_lock_run_id == jid

            # Create second job on same device
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

            # Second acquire on same device — must return None
            async with db.begin_nested():
                lease2 = await acquire_lease(
                    db, device_id=did, host_id=host.id,
                    lease_type=LeaseType.JOB, agent_instance_id=host.id, job_id=jid2,
                )
                assert lease2 is None

            # Device projection must NOT change (still points to first job)
            await db.refresh(device)
            assert device.lock_run_id == first_lock_run_id, (
                "Conflict must not overwrite existing device projection"
            )
            assert device.status == "BUSY"

            await db.rollback()

    @pytest.mark.asyncio(loop_scope="module")
    async def test_expired_active_lease_recycled_on_acquire(self):
        """过期 ACTIVE lease → acquire_lease 自动标记 EXPIRED，新 lease 成功插入."""
        suffix = uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            host, device, job = await _create_seed(db, suffix)
            did, jid = device.id, job.id

            # Create an ACTIVE lease that is already expired
            past = datetime.now(timezone.utc) - timedelta(seconds=3600)
            expired_lease = DeviceLease(
                device_id=did,
                job_id=jid,
                host_id=host.id,
                lease_type=LeaseType.JOB.value,
                status=LeaseStatus.ACTIVE.value,
                fencing_token=f"{did}:99",
                lease_generation=99,
                agent_instance_id=host.id,
                acquired_at=past - timedelta(seconds=7200),
                renewed_at=past,
                expires_at=past,  # already expired
            )
            db.add(expired_lease)
            # Must project to device for FK consistency
            device.status = "BUSY"
            device.lock_run_id = jid
            device.lock_expires_at = past
            await db.commit()
            old_lease_id = expired_lease.id

            # Create second job
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

            # Now acquire_lease should recycle the expired one and succeed
            new_lease = await acquire_lease(
                db, device_id=did, host_id=host.id,
                lease_type=LeaseType.JOB, agent_instance_id=host.id, job_id=jid2,
            )
            assert new_lease is not None, "acquire_lease should succeed after recycling expired lease"
            # lease_generation is derived from device.lease_generation (not the old lease's),
            # so it won't exceed 99 — the key assertion is that the new lease exists
            await db.commit()

            # Old lease must be EXPIRED
            await db.refresh(expired_lease)
            assert expired_lease.status == LeaseStatus.EXPIRED.value, (
                f"Expired ACTIVE lease must be recycled to EXPIRED; got {expired_lease.status}"
            )

            # New lease must be ACTIVE
            assert new_lease.status == LeaseStatus.ACTIVE.value

            # Device projection must point to new job
            await db.refresh(device)
            assert device.lock_run_id == jid2
            assert device.status == "BUSY"

            await db.rollback()


class TestAcquireLeaseProjectionError:
    """LeaseProjectionError 必须向上传播，不能被打包成 None (Phase 2c)."""

    def test_lease_projection_error_is_runtime_error(self):
        """LeaseProjectionError extends RuntimeError."""
        from backend.services.lease_manager import LeaseProjectionError
        assert issubclass(LeaseProjectionError, RuntimeError)
        with pytest.raises(LeaseProjectionError):
            raise LeaseProjectionError("test propagation")

    def test_acquire_lease_except_clauses_do_not_catch_projection_error(self):
        """acquire_lease 的 except 子句不捕获 LeaseProjectionError（需向上传播）."""
        import inspect
        from backend.services.lease_manager import LeaseProjectionError as LPE
        src = inspect.getsource(acquire_lease)
        # The only except clauses should catch _LeaseConflict and IntegrityError
        assert "except _LeaseConflict" in src
        assert "except IntegrityError" in src
        assert "except LeaseProjectionError" not in src, (
            "acquire_lease must NOT catch LeaseProjectionError — it must propagate"
        )


class TestExtendLeaseProjection:
    """extend_lease 必须投影到 device.lock_expires_at (Phase 2c)."""

    @pytest.mark.asyncio(loop_scope="module")
    async def test_extend_lease_projects_lock_expires_at(self):
        """extend_lease 成功后 device.lock_expires_at 被更新."""
        suffix = uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            host, device, job = await _create_seed(db, suffix)
            did, jid = device.id, job.id

            lease = await acquire_lease(
                db, device_id=did, host_id=host.id,
                lease_type=LeaseType.JOB, agent_instance_id=host.id, job_id=jid,
            )
            original_expires = lease.expires_at
            await db.commit()

            await db.refresh(device)
            assert device.lock_expires_at == original_expires

            # Extend
            assert await extend_lease(db, did, jid, LeaseType.JOB, ttl=1200)

            await db.refresh(device)
            assert device.lock_expires_at > original_expires, (
                "extend_lease must update device.lock_expires_at"
            )

            await db.rollback()


class TestReleaseLeaseProjection:
    """release_lease / release_lease_sync 投影清空 device 表 (Phase 2c)."""

    @pytest.mark.asyncio(loop_scope="module")
    async def test_release_lease_projects_clear_lock(self):
        """release_lease 成功后 device.lock_run_id=NULL, lock_expires_at=NULL, status='ONLINE'."""
        suffix = uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            host, device, job = await _create_seed(db, suffix)
            did, jid = device.id, job.id

            lease = await acquire_lease(
                db, device_id=did, host_id=host.id,
                lease_type=LeaseType.JOB, agent_instance_id=host.id, job_id=jid,
            )
            await db.commit()
            await db.refresh(device)
            assert device.lock_run_id == jid
            assert device.status == "BUSY"

            # Release
            assert await release_lease(db, did, jid, LeaseType.JOB)
            await db.commit()

            await db.refresh(device)
            assert device.lock_run_id is None, "release_lease must clear lock_run_id"
            assert device.lock_expires_at is None, "release_lease must clear lock_expires_at"
            assert device.status == "ONLINE", "release_lease must restore status to ONLINE"

            await db.rollback()

    @pytest.mark.asyncio(loop_scope="module")
    async def test_release_lease_wrong_holder_raises(self):
        """device.lock_run_id != job_id 时 release_lease 抛 LeaseProjectionError，lease 保持 ACTIVE."""
        suffix = uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            host, device, job = await _create_seed(db, suffix)
            did, jid = device.id, job.id

            # Create ACTIVE lease for job A
            lease = await acquire_lease(
                db, device_id=did, host_id=host.id,
                lease_type=LeaseType.JOB, agent_instance_id=host.id, job_id=jid,
            )
            await db.commit()

            # Tamper: set device.lock_run_id to a different job (simulating inconsistent state)
            device.lock_run_id = 99999
            await db.commit()

            # release_lease should raise because lock_run_id doesn't match
            from backend.services.lease_manager import LeaseProjectionError as LPE
            with pytest.raises(LPE) as exc_info:
                await release_lease(db, did, jid, LeaseType.JOB)
            assert "projection failed" in str(exc_info.value)

            # Lease is still ACTIVE (savepoint rolled back)
            await db.refresh(lease)
            assert lease.status == LeaseStatus.ACTIVE.value, (
                "Lease must stay ACTIVE when projection fails — savepoint must roll back"
            )

            await db.rollback()

    def test_release_lease_sync_projects_clear_lock(self):
        """release_lease_sync 成功后 device 表投影正确清空."""
        from datetime import datetime, timezone as tz

        suffix = uuid4().hex[:8]
        now = datetime.now(tz.utc)
        expires = now + timedelta(seconds=600)
        db = SessionLocal()
        try:
            host = Host(
                id=f"proj-sync-{suffix}", hostname=f"h-{suffix}",
                status=HostStatus.ONLINE.value, created_at=now,
            )
            device = Device(
                serial=f"PROJ-{suffix}", host_id=host.id,
                status="ONLINE", tags=[], created_at=now,
            )
            db.add_all([host, device])
            db.flush()

            wf = WorkflowDefinition(
                name=f"wf-{suffix}", failure_threshold=0.1, created_by="pytest",
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
                workflow_definition_id=wf.id, status="RUNNING",
                failure_threshold=0.1, triggered_by="pytest", started_at=now,
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
            device.status = "BUSY"
            device.lock_run_id = job.id
            device.lock_expires_at = expires
            db.commit()

            # Act
            result = release_lease_sync(db, device.id, job.id, LeaseType.JOB)
            assert result is True

            db.refresh(device)
            assert device.lock_run_id is None, "release_lease_sync must clear lock_run_id"
            assert device.lock_expires_at is None, "release_lease_sync must clear lock_expires_at"
            assert device.status == "ONLINE", "release_lease_sync must restore ONLINE"
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2c: Projection failure → no residue
# ══════════════════════════════════════════════════════════════════════════════


class TestAcquireLeaseProjectionFailure:
    """acquire_lease 投影失败必须向上传播 LeaseProjectionError，不残留 ACTIVE lease."""

    @pytest.mark.asyncio(loop_scope="module")
    async def test_acquire_lease_projection_failure_rolls_back_lease(self):
        """投影 UPDATE 返回 rowcount=0 → LeaseProjectionError，savepoint 回滚，无 ACTIVE lease 残留."""
        from unittest.mock import AsyncMock, PropertyMock, patch

        from backend.services.lease_manager import LeaseProjectionError as LPE

        suffix = uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            host, device, job = await _create_seed(db, suffix)
            did, jid = device.id, job.id
            await db.commit()

        # Re-open a fresh session for the actual acquire_lease call
        async with AsyncSessionLocal() as db:
            device_ref = await db.get(Device, did)
            assert device_ref is not None

            real_execute = db.execute

            # Counter: the SECOND Device UPDATE inside acquire_lease is the projection.
            # (First Device UPDATE is lease_generation increment.)
            device_update_count = [0]
            zero_result = AsyncMock()
            type(zero_result).rowcount = PropertyMock(return_value=0)

            async def _mock_execute(stmt, *args, **kwargs):
                result = await real_execute(stmt, *args, **kwargs)
                if (
                    hasattr(stmt, 'is_update') and stmt.is_update
                    and hasattr(stmt, 'table') and stmt.table.name == 'device'
                ):
                    device_update_count[0] += 1
                    if device_update_count[0] == 2:
                        return zero_result
                return result

            with patch.object(db, 'execute', _mock_execute):
                with pytest.raises(LPE) as exc_info:
                    await acquire_lease(
                        db, device_id=did, host_id=host.id,
                        lease_type=LeaseType.JOB, agent_instance_id=host.id, job_id=jid,
                    )
                assert "projection failed" in str(exc_info.value)

            # After exception, no ACTIVE lease should remain (savepoint rolled back)
            existing = (await db.execute(
                select(DeviceLease).where(
                    DeviceLease.device_id == did,
                    DeviceLease.status == LeaseStatus.ACTIVE.value,
                )
            )).scalars().first()
            assert existing is None, (
                "Projection failure must roll back the entire savepoint — "
                "no ACTIVE lease should remain"
            )

            await db.rollback()
