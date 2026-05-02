"""Lease manager 主路径语义测试（ADR-0019 Phase 6d-1 迁移自 test_dual_write.py）。

聚焦 acquire_lease / extend_lease / release_lease / release_lease_sync 的
ACTIVE → RELEASED / EXPIRED 主路径，以及冲突、续期、幂等等行为。

Phase 6d-1 边界：
  - 不再断言投影列（device.lock_run_id / device.lock_expires_at）被同步更新
  - 投影列空值断言整体延后到 6d-2 验证步骤中加入
  - setup 阶段对投影列的写入仍保留，因为生产代码仍依赖
    `Device.lock_run_id == job_id` 作为 WHERE guard
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
from backend.services.lease_manager import (
    acquire_lease,
    extend_lease,
    release_lease,
    release_lease_sync,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
IS_PG = TEST_DATABASE_URL.startswith("postgresql")

pytestmark = pytest.mark.skipif(
    not IS_PG,
    reason="lease_manager tests require PostgreSQL (savepoints + partial unique index)",
)


async def _create_seed(db, suffix, host_id=None, serial=None):
    """Create Host + Device + WorkflowFKs + JobInstance (PENDING).
    Returns (host, device, job) with flushed IDs."""
    now = datetime.now(timezone.utc)
    hid = host_id or f"lmh-{suffix}"
    ser = serial or f"LMS-{suffix}"

    host = Host(id=hid, hostname=f"h-{suffix}", status=HostStatus.ONLINE.value, created_at=now)
    device = Device(serial=ser, host_id=host.id, status="ONLINE", tags=[], created_at=now)
    db.add_all([host, device])
    await db.flush()

    wd = WorkflowDefinition(name=f"lmf-{suffix}", failure_threshold=0.1, created_by="test")
    db.add(wd)
    await db.flush()
    tt = JobTaskTemplate(
        workflow_definition_id=wd.id, name=f"lmt-{suffix}",
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


# ── acquire_lease ────────────────────────────────────────────────────────────

class TestAcquireLeaseMain:
    """acquire_lease 主路径语义。"""

    @pytest.mark.asyncio(loop_scope="module")
    async def test_acquire_lease_none_on_conflict(self):
        """同一设备上第二次 acquire_lease 必须返回 None（已有 ACTIVE lease）。"""
        suffix = uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            host, device, job = await _create_seed(db, suffix)
            did, jid = device.id, job.id

            # 第一次 acquire 成功
            lease1 = await acquire_lease(
                db, device_id=did, host_id=host.id,
                lease_type=LeaseType.JOB, agent_instance_id=host.id, job_id=jid,
            )
            assert lease1 is not None
            await db.commit()

            # 同一设备上创建第二个 JobInstance（不同 job_id）
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

            # 第二次 acquire 必须返回 None
            async with db.begin_nested():
                lease2 = await acquire_lease(
                    db, device_id=did, host_id=host.id,
                    lease_type=LeaseType.JOB, agent_instance_id=host.id, job_id=jid2,
                )
                assert lease2 is None, "acquire_lease should return None on conflict"

            await db.rollback()


# ── extend_lease ─────────────────────────────────────────────────────────────

class TestExtendLeaseMain:
    """extend_lease 主路径语义。"""

    @pytest.mark.asyncio(loop_scope="module")
    async def test_extend_lease_updates_expires_and_renewed(self):
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
            await db.commit()

            await asyncio.sleep(0.01)
            assert await extend_lease(db, did, jid, LeaseType.JOB, 600)

            await db.refresh(lease)
            assert lease.expires_at > original_expires
            assert lease.renewed_at > original_renewed

            await db.rollback()

    @pytest.mark.asyncio(loop_scope="module")
    async def test_extend_lease_miss_returns_false(self):
        """没有 ACTIVE lease 时 extend_lease 返回 False。"""
        suffix = uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            host, device, _ = await _create_seed(db, suffix)
            await db.commit()  # 提交 Host/Device 但不创建 lease

            result = await extend_lease(
                db, device_id=device.id, job_id=99999, lease_type=LeaseType.JOB,
            )
            assert result is False
            await db.rollback()


# ── release_lease ────────────────────────────────────────────────────────────

class TestReleaseLeaseMain:
    """release_lease 主路径语义。"""

    @pytest.mark.asyncio(loop_scope="module")
    async def test_release_lock_and_lease(self):
        suffix = uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            host, device, job = await _create_seed(db, suffix)
            did, jid = device.id, job.id

            lease = await acquire_lease(
                db, device_id=did, host_id=host.id,
                lease_type=LeaseType.JOB, agent_instance_id=host.id, job_id=jid,
            )
            lease_id = lease.id
            await db.commit()

            rel_ok = await release_lease(db, did, jid, LeaseType.JOB)
            assert rel_ok
            await db.commit()

            db.expire_all()
            fetched = await db.get(DeviceLease, lease_id)
            assert fetched.status == LeaseStatus.RELEASED.value
            assert fetched.released_at is not None

            await db.rollback()

    @pytest.mark.asyncio(loop_scope="module")
    async def test_release_lease_miss_returns_false(self):
        """没有 ACTIVE lease 时 release_lease 返回 False。"""
        suffix = uuid4().hex[:8]
        async with AsyncSessionLocal() as db:
            host, device, _ = await _create_seed(db, suffix)
            await db.commit()

            result = await release_lease(
                db, device_id=device.id, job_id=99999, lease_type=LeaseType.JOB,
            )
            assert result is False
            await db.rollback()

    @pytest.mark.asyncio(loop_scope="module")
    async def test_release_lease_idempotent_returns_false(self):
        """对已 RELEASED 的 lease 再次 release_lease 返回 False。"""
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


# ── release_lease_sync ───────────────────────────────────────────────────────

def test_release_lease_sync_marks_active_to_released():
    """release_lease_sync 将 ACTIVE lease 转为 RELEASED（同步版，供 recycler 使用）。"""
    from datetime import timezone as tz

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
        # Phase 6d-2: projection columns decommissioned.
        device.status = "BUSY"
        db.commit()

        # Act
        result = release_lease_sync(db, device.id, job.id, LeaseType.JOB)
        assert result is True

        db.expire_all()
        db.refresh(lease)
        assert lease.status == LeaseStatus.RELEASED.value
        assert lease.released_at is not None

        # 幂等：第二次调用返回 False
        result2 = release_lease_sync(db, device.id, job.id, LeaseType.JOB)
        assert result2 is False
    finally:
        db.close()
