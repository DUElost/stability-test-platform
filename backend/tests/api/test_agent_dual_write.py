"""Route-level dual-write tests for ADR-0019 Phase 2a.

直接调用 claim_jobs / get_pending_jobs / extend_job_lock / complete_job
handler，断言 device_leases 与 lock_run_id/lock_expires_at 同时写入。

与 tests/services/test_dual_write.py 的区别：
  - 本文件走路由 handler（agent_api），覆盖完整的请求→响应路径
  - service 级测试只覆盖 lease_manager + device_lock 的独立行为
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy import select

pytestmark = pytest.mark.skipif(
    os.getenv("DATABASE_URL", "").startswith("sqlite"),
    reason="API 路由双写测试需要 PostgreSQL（device_leases 部分唯一索引）",
)

from backend.api.routes.agent_api import (
    _ActiveJobEntry,
    _ExtendLockIn,
    _JobHeartbeatIn,
    _OutboxEntry,
    _RecoverySyncIn,
    _RunCompleteIn,
    _claim_jobs_for_host,
    ClaimRequest,
    claim_jobs,
    complete_job,
    extend_job_lock,
    get_pending_jobs,
    job_heartbeat,
    recovery_sync,
)
from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType, WorkflowStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance, StepTrace, TaskTemplate
from backend.models.workflow import WorkflowDefinition, WorkflowRun

PIPELINE_DEF = {
    "stages": {
        "prepare": [],
        "execute": [{"step_id": "dummy", "action": "builtin:noop", "timeout_seconds": 1}],
        "post_process": [],
    }
}


def _seed_job(*, status: str = JobStatus.PENDING.value) -> dict:
    suffix = uuid4().hex[:8]
    host_id = f"dw-rt-host-{suffix}"
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{suffix}",
            status=HostStatus.ONLINE.value, created_at=now,
        )
        device = Device(
            serial=f"DW-RT-{suffix}", host_id=host_id,
            status="ONLINE", tags=[], created_at=now,
            adb_connected=True, adb_state="device",
        )
        wf = WorkflowDefinition(
            name=f"wf-{suffix}", description="dual-write route test",
            failure_threshold=0.1, created_by="pytest",
            created_at=now, updated_at=now,
        )
        db.add_all([host, device, wf])
        db.flush()

        tpl = TaskTemplate(
            workflow_definition_id=wf.id, name=f"tpl-{suffix}",
            pipeline_def=PIPELINE_DEF, sort_order=0, created_at=now,
        )
        db.add(tpl)
        db.flush()

        run = WorkflowRun(
            workflow_definition_id=wf.id,
            status=WorkflowStatus.RUNNING.value,
            failure_threshold=0.1, triggered_by="pytest", started_at=now,
        )
        db.add(run)
        db.flush()

        job = JobInstance(
            workflow_run_id=run.id, task_template_id=tpl.id,
            device_id=device.id, host_id=host_id,
            status=status, pipeline_def=PIPELINE_DEF,
            created_at=now, updated_at=now,
            started_at=now if status == JobStatus.RUNNING.value else None,
        )
        db.add(job)
        db.commit()

        return {
            "host_id": host_id, "device_id": device.id,
            "workflow_definition_id": wf.id, "task_template_id": tpl.id,
            "workflow_run_id": run.id, "job_id": job.id,
        }
    finally:
        db.close()


def _cleanup_seed(seed: dict) -> None:
    db = SessionLocal()
    try:
        job_id = seed.get("job_id")
        if job_id:
            db.query(DeviceLease).filter(DeviceLease.job_id == job_id).delete()
            db.query(StepTrace).filter(StepTrace.job_id == job_id).delete()
            db.query(JobInstance).filter(JobInstance.id == job_id).delete()

        run_id = seed.get("workflow_run_id")
        if run_id:
            db.query(WorkflowRun).filter(WorkflowRun.id == run_id).delete()

        tpl_id = seed.get("task_template_id")
        if tpl_id:
            db.query(TaskTemplate).filter(TaskTemplate.id == tpl_id).delete()

        wf_id = seed.get("workflow_definition_id")
        if wf_id:
            db.query(WorkflowDefinition).filter(WorkflowDefinition.id == wf_id).delete()

        device_id = seed.get("device_id")
        if device_id:
            db.query(Device).filter(Device.id == device_id).delete()

        host_id = seed.get("host_id")
        if host_id:
            db.query(Host).filter(Host.id == host_id).delete()

        db.commit()
    finally:
        db.close()


def _setup_lock_and_lease(seed: dict) -> None:
    """Pre-populate device lock + ACTIVE lease for extend/complete tests."""
    from datetime import timedelta

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=600)
        fencing_token = f"{seed['device_id']}:1"

        device = db.get(Device, seed["device_id"])
        if device:
            device.status = "BUSY"
            device.lock_run_id = seed["job_id"]
            device.lock_expires_at = expires

        lease = DeviceLease(
            device_id=seed["device_id"],
            job_id=seed["job_id"],
            host_id=seed["host_id"],
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=fencing_token,
            lease_generation=1,
            agent_instance_id=seed["host_id"],
            acquired_at=now,
            renewed_at=now,
            expires_at=expires,
        )
        db.add(lease)
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# claim_jobs 路由双写
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_claim_jobs_writes_lock_and_lease():
    """claim_jobs 成功后 device.lock_run_id + device_leases 同时写入。"""
    seed = _seed_job(status=JobStatus.PENDING.value)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await claim_jobs(
                payload=ClaimRequest(host_id=seed["host_id"], capacity=5),
                db=async_db, _=None,
            )
        assert result.error is None
        assert len(result.data) == 1
        job_out = result.data[0]
        assert job_out.id == seed["job_id"]
        assert job_out.status == JobStatus.RUNNING.value

        # 同步验证 lock_run_id + device_leases
        db = SessionLocal()
        try:
            device = db.get(Device, seed["device_id"])
            assert device is not None
            assert device.lock_run_id == seed["job_id"]
            assert device.lock_expires_at is not None

            lease = (
                db.query(DeviceLease)
                .filter(
                    DeviceLease.device_id == seed["device_id"],
                    DeviceLease.job_id == seed["job_id"],
                    DeviceLease.lease_type == LeaseType.JOB.value,
                    DeviceLease.status == LeaseStatus.ACTIVE.value,
                )
                .first()
            )
            assert lease is not None, "claim_jobs must create an ACTIVE device_lease"
            assert lease.fencing_token == f"{seed['device_id']}:{lease.lease_generation}"
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


# ---------------------------------------------------------------------------
# get_pending_jobs 路由双写
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_pending_jobs_writes_lock_and_lease():
    """get_pending_jobs 成功后 device.lock_run_id + device_leases 同时写入。"""
    seed = _seed_job(status=JobStatus.PENDING.value)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await get_pending_jobs(
                host_id=seed["host_id"], limit=5,
                db=async_db, _=None,
            )
        assert result.error is None
        assert len(result.data) == 1
        assert result.data[0].id == seed["job_id"]
        assert result.data[0].status == JobStatus.RUNNING.value

        db = SessionLocal()
        try:
            device = db.get(Device, seed["device_id"])
            assert device is not None
            assert device.lock_run_id == seed["job_id"]
            assert device.lock_expires_at is not None

            lease = (
                db.query(DeviceLease)
                .filter(
                    DeviceLease.device_id == seed["device_id"],
                    DeviceLease.job_id == seed["job_id"],
                    DeviceLease.lease_type == LeaseType.JOB.value,
                    DeviceLease.status == LeaseStatus.ACTIVE.value,
                )
                .first()
            )
            assert lease is not None, "get_pending_jobs must create an ACTIVE device_lease"
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


# ---------------------------------------------------------------------------
# extend_job_lock 路由双写
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extend_job_lock_renews_lease():
    """extend_job_lock 续期 lock_expires_at 的同时续期 lease.expires_at。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    token = f"{seed['device_id']}:1"
    try:
        # 记录原始 expires
        db = SessionLocal()
        try:
            orig_device = db.get(Device, seed["device_id"])
            orig_lock_expires = orig_device.lock_expires_at
            orig_lease = (
                db.query(DeviceLease)
                .filter(
                    DeviceLease.device_id == seed["device_id"],
                    DeviceLease.job_id == seed["job_id"],
                    DeviceLease.status == LeaseStatus.ACTIVE.value,
                )
                .first()
            )
            assert orig_lease is not None
            orig_lease_expires = orig_lease.expires_at
            orig_lease_renewed = orig_lease.renewed_at
        finally:
            db.close()

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await extend_job_lock(
                job_id=seed["job_id"],
                payload=_ExtendLockIn(fencing_token=token),
                db=async_db, _=None,
            )
        assert result.error is None
        assert result.data["job_id"] == seed["job_id"]
        assert result.data["expires_at"]

        # 同步验证两端都续期了
        db = SessionLocal()
        try:
            device = db.get(Device, seed["device_id"])
            assert device.lock_expires_at > orig_lock_expires
            db.expire_all()
            lease = (
                db.query(DeviceLease)
                .filter(
                    DeviceLease.device_id == seed["device_id"],
                    DeviceLease.job_id == seed["job_id"],
                    DeviceLease.status == LeaseStatus.ACTIVE.value,
                )
                .first()
            )
            assert lease is not None
            assert lease.expires_at > orig_lease_expires
            assert lease.renewed_at > orig_lease_renewed
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


# ---------------------------------------------------------------------------
# complete_job 路由双写
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_job_releases_lock_and_lease():
    """complete_job 后 lock_run_id 清空 + device_lease → RELEASED。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    token = f"{seed['device_id']}:1"
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await complete_job(
                job_id=seed["job_id"],
                payload=_RunCompleteIn(update={"status": "FINISHED", "exit_code": 0}, fencing_token=token),
                db=async_db, _=None,
            )
        assert result.error is None
        assert result.data["status"] == JobStatus.COMPLETED.value

        db = SessionLocal()
        try:
            device = db.get(Device, seed["device_id"])
            assert device.lock_run_id is None
            assert device.lock_expires_at is None

            db.expire_all()
            lease = (
                db.query(DeviceLease)
                .filter(
                    DeviceLease.device_id == seed["device_id"],
                    DeviceLease.job_id == seed["job_id"],
                )
                .first()
            )
            assert lease is not None
            assert lease.status == LeaseStatus.RELEASED.value
            assert lease.released_at is not None
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_complete_job_idempotent_no_release_lease_miss_warning(caplog):
    """已终态 job 重复 complete → release_lease miss 只打 debug 不打 warning。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    token = f"{seed['device_id']}:1"
    try:
        # 第一次 complete
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result1 = await complete_job(
                job_id=seed["job_id"],
                payload=_RunCompleteIn(update={"status": "FINISHED", "exit_code": 0}, fencing_token=token),
                db=async_db, _=None,
            )
        assert result1.error is None
        assert result1.data["status"] == JobStatus.COMPLETED.value

        # 第二次 complete（幂等重放）—— 需要重建 session
        caplog.set_level(logging.DEBUG, logger="backend.api.routes.agent_api")
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result2 = await complete_job(
                job_id=seed["job_id"],
                payload=_RunCompleteIn(update={"status": "FINISHED", "exit_code": 0}, fencing_token=token),
                db=async_db, _=None,
            )
        assert result2.error is None
        assert result2.data["status"] == JobStatus.COMPLETED.value

        # 断言：第二次不产生 release_lease_miss WARNING
        warnings = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING and "release_lease_miss" in r.message
        ]
        assert len(warnings) == 0, (
            f"Idempotent replay must not log release_lease_miss warning, "
            f"got {len(warnings)}: {[r.message for r in warnings]}"
        )

        # 断言：第二次产生 release_lease_already_released DEBUG
        debugs = [
            r for r in caplog.records
            if r.levelno >= logging.DEBUG and "release_lease_already_released" in r.message
        ]
        assert len(debugs) >= 1, "Idempotent replay should log release_lease_already_released debug"
    finally:
        _cleanup_seed(seed)


# ============================================================================
# Phase 2b fencing_token 强协议测试（14 个路由级）
# ============================================================================


# ── C1: claim_jobs 响应包含 fencing_token ───────────────────────────────────

@pytest.mark.asyncio
async def test_claim_jobs_response_includes_fencing_token():
    """claim_jobs 响应中每个 JobOut 均包含必填 fencing_token 字段。"""
    seed = _seed_job(status=JobStatus.PENDING.value)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await claim_jobs(
                payload=ClaimRequest(host_id=seed["host_id"], capacity=5),
                db=async_db, _=None,
            )
        assert result.error is None
        assert len(result.data) == 1
        item = result.data[0]
        assert isinstance(item.fencing_token, str), "fencing_token must be present"
        assert item.fencing_token.startswith(f"{seed['device_id']}:")
    finally:
        _cleanup_seed(seed)


# ── C2: heartbeat fencing_token 校验 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_heartbeat_valid_token_returns_200():
    """正确 fencing_token → heartbeat 200。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    token = f"{seed['device_id']}:1"
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await job_heartbeat(
                job_id=seed["job_id"],
                payload=_JobHeartbeatIn(status="RUNNING", fencing_token=token),
                db=async_db, _=None,
            )
        assert result.error is None
        assert result.data["status"] == JobStatus.RUNNING.value
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_heartbeat_wrong_token_returns_409():
    """错误 fencing_token → heartbeat 409。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as exc_info:
                await job_heartbeat(
                    job_id=seed["job_id"],
                    payload=_JobHeartbeatIn(status="RUNNING", fencing_token="WRONG_TOKEN"),
                    db=async_db, _=None,
                )
        assert exc_info.value.status_code == 409
        assert "fencing_token" in exc_info.value.detail.lower()
    finally:
        _cleanup_seed(seed)


def test_heartbeat_missing_token_raises_validation_error():
    """缺 fencing_token → Pydantic 拒收 _JobHeartbeatIn 构造。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _JobHeartbeatIn(status="RUNNING")


@pytest.mark.asyncio
async def test_heartbeat_no_active_lease_returns_409():
    """无 ACTIVE lease 时 heartbeat 直接 409。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as exc_info:
                await job_heartbeat(
                    job_id=seed["job_id"],
                    payload=_JobHeartbeatIn(status="RUNNING", fencing_token="ANY"),
                    db=async_db, _=None,
                )
        assert exc_info.value.status_code == 409
    finally:
        _cleanup_seed(seed)


# ── C3: extend_job_lock fencing_token 校验 ──────────────────────────────────

@pytest.mark.asyncio
async def test_extend_lock_valid_token_returns_200():
    """正确 fencing_token → extend_job_lock 200。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    token = f"{seed['device_id']}:1"
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await extend_job_lock(
                job_id=seed["job_id"],
                payload=_ExtendLockIn(fencing_token=token),
                db=async_db, _=None,
            )
        assert result.error is None
        assert result.data["job_id"] == seed["job_id"]
        assert result.data["expires_at"]
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_extend_lock_wrong_token_returns_409():
    """错误 fencing_token → extend_job_lock 409。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as exc_info:
                await extend_job_lock(
                    job_id=seed["job_id"],
                    payload=_ExtendLockIn(fencing_token="WRONG"),
                    db=async_db, _=None,
                )
        assert exc_info.value.status_code == 409
    finally:
        _cleanup_seed(seed)


def test_extend_lock_missing_token_raises_validation_error():
    """缺 fencing_token → Pydantic 拒收 _ExtendLockIn 构造。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _ExtendLockIn()


@pytest.mark.asyncio
async def test_extend_lock_no_active_lease_returns_409():
    """无 ACTIVE lease 时 extend_job_lock 直接 409。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as exc_info:
                await extend_job_lock(
                    job_id=seed["job_id"],
                    payload=_ExtendLockIn(fencing_token="ANY"),
                    db=async_db, _=None,
                )
        assert exc_info.value.status_code == 409
    finally:
        _cleanup_seed(seed)


# ── C4: complete_job fencing_token 校验 ─────────────────────────────────────

@pytest.mark.asyncio
async def test_complete_job_valid_token_returns_200():
    """正确 fencing_token（ACTIVE lease）→ complete_job 200。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    token = f"{seed['device_id']}:1"
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await complete_job(
                job_id=seed["job_id"],
                payload=_RunCompleteIn(
                    update={"status": "FINISHED", "exit_code": 0},
                    fencing_token=token,
                ),
                db=async_db, _=None,
            )
        assert result.error is None
        assert result.data["status"] == JobStatus.COMPLETED.value
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_complete_job_wrong_token_returns_409():
    """错误 fencing_token（ACTIVE lease）→ complete_job 409。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as exc_info:
                await complete_job(
                    job_id=seed["job_id"],
                    payload=_RunCompleteIn(
                        update={"status": "FINISHED", "exit_code": 0},
                        fencing_token="WRONG",
                    ),
                    db=async_db, _=None,
                )
        assert exc_info.value.status_code == 409
    finally:
        _cleanup_seed(seed)


def test_complete_job_missing_token_raises_validation_error():
    """缺 fencing_token → Pydantic 拒收 _RunCompleteIn 构造。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _RunCompleteIn(update={"status": "FINISHED", "exit_code": 0})


# ── C5: complete_job 幂等重放 fencing_token 校验 ────────────────────────────

@pytest.mark.asyncio
async def test_complete_job_idempotent_replay_same_token_returns_200():
    """第一次 complete（ACTIVE→RELEASED），第二次同 token 匹配 RELEASED lease → 200。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    token = f"{seed['device_id']}:1"
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            r1 = await complete_job(
                job_id=seed["job_id"],
                payload=_RunCompleteIn(
                    update={"status": "FINISHED", "exit_code": 0},
                    fencing_token=token,
                ),
                db=async_db, _=None,
            )
        assert r1.error is None
        assert r1.data["status"] == JobStatus.COMPLETED.value

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            r2 = await complete_job(
                job_id=seed["job_id"],
                payload=_RunCompleteIn(
                    update={"status": "FINISHED", "exit_code": 0},
                    fencing_token=token,
                ),
                db=async_db, _=None,
            )
        assert r2.error is None
        assert r2.data["status"] == JobStatus.COMPLETED.value
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_complete_job_idempotent_replay_wrong_token_returns_409():
    """第一次 complete 后，第二次用错误 token（RELEASED lease 不匹配）→ 409。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    token = f"{seed['device_id']}:1"
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            r1 = await complete_job(
                job_id=seed["job_id"],
                payload=_RunCompleteIn(
                    update={"status": "FINISHED", "exit_code": 0},
                    fencing_token=token,
                ),
                db=async_db, _=None,
            )
        assert r1.error is None

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as exc_info:
                await complete_job(
                    job_id=seed["job_id"],
                    payload=_RunCompleteIn(
                        update={"status": "FINISHED", "exit_code": 0},
                        fencing_token="WRONG_TOKEN",
                    ),
                    db=async_db, _=None,
                )
        assert exc_info.value.status_code == 409
    finally:
        _cleanup_seed(seed)


# ── C6: session_watchdog release_lease ──────────────────────────────────────

@pytest.mark.asyncio
async def test_session_watchdog_releases_lease_on_host_timeout():
    """Host heartbeat timeout → release_lock + release_lease 同时触发。"""
    from backend.tasks.session_watchdog import _check_host_heartbeat_timeouts

    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    try:
        db = SessionLocal()
        try:
            host = db.get(Host, seed["host_id"])
            host.last_heartbeat = datetime(2020, 1, 1, tzinfo=timezone.utc)
            db.commit()
        finally:
            db.close()

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            hosts_off, jobs_unknown = await _check_host_heartbeat_timeouts(async_db)
            await async_db.commit()

        assert hosts_off >= 1

        db = SessionLocal()
        try:
            lease = (
                db.query(DeviceLease)
                .filter(
                    DeviceLease.device_id == seed["device_id"],
                    DeviceLease.job_id == seed["job_id"],
                )
                .first()
            )
            assert lease is not None
            assert lease.status == LeaseStatus.RELEASED.value, (
                f"Watchdog must release lease on host timeout; got {lease.status}"
            )
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_session_watchdog_releases_lease_on_lock_expiration():
    """DeviceLease 过期 → watchdog release_lease（Phase 2c: 读 DeviceLease 而非 Device.lock_run_id）。"""
    from backend.tasks.session_watchdog import _check_device_lock_expiration

    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    try:
        # Phase 2c: expire the LEASE (not just device.lock_expires_at)
        db = SessionLocal()
        try:
            db.query(DeviceLease).filter(
                DeviceLease.device_id == seed["device_id"],
                DeviceLease.job_id == seed["job_id"],
            ).update({"expires_at": datetime(2020, 1, 1, tzinfo=timezone.utc)})
            db.commit()
        finally:
            db.close()

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            released = await _check_device_lock_expiration(async_db)
            await async_db.commit()

        assert released >= 1

        db = SessionLocal()
        try:
            lease = (
                db.query(DeviceLease)
                .filter(
                    DeviceLease.device_id == seed["device_id"],
                    DeviceLease.job_id == seed["job_id"],
                )
                .first()
            )
            assert lease is not None
            assert lease.status == LeaseStatus.RELEASED.value, (
                f"Watchdog must release lease on lock expiration; got {lease.status}"
            )
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2c API integration tests
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_get_pending_jobs_deprecated_header():
    """GET /jobs/pending 返回 Deprecation + Sunset header (Phase 2c)."""
    from fastapi import Response as FapiResponse

    seed = _seed_job(status=JobStatus.PENDING.value)
    try:
        await async_engine.dispose()
        r = FapiResponse()
        async with AsyncSessionLocal() as async_db:
            result = await get_pending_jobs(
                host_id=seed["host_id"], limit=5,
                response=r, db=async_db, _=None,
            )
        assert result.error is None
        assert r.headers.get("Deprecation") == "true"
        assert "Sunset" in r.headers
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_get_pending_jobs_still_works():
    """GET /jobs/pending 虽已 deprecated 但功能与 claim_jobs 一致 (Phase 2c)."""
    seed = _seed_job(status=JobStatus.PENDING.value)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await get_pending_jobs(
                host_id=seed["host_id"], limit=5,
                db=async_db, _=None,
            )
        assert result.error is None
        assert len(result.data) == 1
        item = result.data[0]
        assert item.id == seed["job_id"]
        assert item.status == JobStatus.RUNNING.value
        assert item.fencing_token is not None

        db = SessionLocal()
        try:
            device = db.get(Device, seed["device_id"])
            assert device.lock_run_id == seed["job_id"], (
                "get_pending_jobs must project device lock via acquire_lease"
            )
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_claim_jobs_skip_on_active_lease():
    """设备已有 ACTIVE lease 时 claim_jobs 跳过该设备上的 PENDING job (Phase 2c)."""
    seed = _seed_job(status=JobStatus.PENDING.value)
    _setup_lock_and_lease(seed)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await claim_jobs(
                payload=ClaimRequest(host_id=seed["host_id"], capacity=5),
                db=async_db, _=None,
            )
        assert result.data == [], (
            "claim_jobs must skip jobs on devices with ACTIVE lease"
        )

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job.status == JobStatus.PENDING.value, (
                "Job must stay PENDING when device has ACTIVE lease"
            )
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_claim_jobs_claims_after_expired_lease():
    """旧 lease 过期后 claim_jobs 可重新 claim 同设备不同 job (Phase 2c 过期回收)."""
    from datetime import timedelta

    seed = _seed_job(status=JobStatus.PENDING.value)
    old_lease_id = None
    # Create expired ACTIVE lease using sync session
    db = SessionLocal()
    try:
        past = datetime.now(timezone.utc) - timedelta(seconds=3600)
        lease = DeviceLease(
            device_id=seed["device_id"],
            job_id=seed["job_id"],
            host_id=seed["host_id"],
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{seed['device_id']}:1",
            lease_generation=1,
            agent_instance_id=seed["host_id"],
            acquired_at=past - timedelta(seconds=7200),
            renewed_at=past,
            expires_at=past,
        )
        db.add(lease)
        db.flush()
        old_lease_id = lease.id
        dev = db.get(Device, seed["device_id"])
        dev.status = "BUSY"
        dev.lock_run_id = 99999
        dev.lock_expires_at = past
        db.commit()
    finally:
        db.close()

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await claim_jobs(
                payload=ClaimRequest(host_id=seed["host_id"], capacity=5),
                db=async_db, _=None,
            )
        assert len(result.data) == 1, (
            "claim_jobs must reclaim after expired lease is recycled"
        )
        assert result.data[0].id == seed["job_id"]

        db = SessionLocal()
        try:
            old_lease = db.get(DeviceLease, old_lease_id)
            assert old_lease is not None
            assert old_lease.status == LeaseStatus.EXPIRED.value, (
                f"Expired ACTIVE lease must be recycled to EXPIRED; got {old_lease.status}"
            )
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2c watchdog: reads DeviceLease NOT Device.lock_run_id
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_device_lock_expiration_reads_device_leases_not_device_table():
    """DeviceLease 过期但 Device.lock_run_id=NULL 时 watchdog 仍释放 lease (Phase 2c)."""
    from backend.tasks.session_watchdog import _check_device_lock_expiration

    seed = _seed_job(status=JobStatus.RUNNING.value)
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        expired = datetime(2020, 1, 1, tzinfo=timezone.utc)
        lease = DeviceLease(
            device_id=seed["device_id"],
            job_id=seed["job_id"],
            host_id=seed["host_id"],
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{seed['device_id']}:1",
            lease_generation=1,
            agent_instance_id=seed["host_id"],
            acquired_at=now,
            renewed_at=now,
            expires_at=expired,
        )
        db.add(lease)
        # Intentionally set lock_run_id=NULL — projection out of sync
        dev = db.get(Device, seed["device_id"])
        dev.lock_run_id = None
        dev.lock_expires_at = None
        db.commit()
    finally:
        db.close()

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            released = await _check_device_lock_expiration(async_db)
            await async_db.commit()

        assert released >= 1, (
            "Watchdog must release lease even when Device.lock_run_id=NULL"
        )

        db = SessionLocal()
        try:
            db.expire_all()
            dl = (
                db.query(DeviceLease)
                .filter(
                    DeviceLease.device_id == seed["device_id"],
                    DeviceLease.job_id == seed["job_id"],
                )
                .first()
            )
            assert dl is not None
            assert dl.status == LeaseStatus.RELEASED.value, (
                f"Lease must be RELEASED; got {dl.status}"
            )
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def _cleanup_custom(seed: dict) -> None:
    """Clean up a custom seed dict with arbitrary *_id* keys."""
    db = SessionLocal()
    try:
        # Collect all IDs by type
        job_ids = set()
        run_ids = set()
        tpl_ids = set()
        wf_ids = set()
        device_ids = set()
        host_id = None
        for key, val in seed.items():
            if not val:
                continue
            if key.startswith("job") and isinstance(val, int):
                job_ids.add(val)
            elif key.startswith("device") and isinstance(val, int):
                device_ids.add(val)
        if "host_id" in seed:
            host_id = seed["host_id"]
        if "workflow_run_id" in seed:
            run_ids.add(seed["workflow_run_id"])
        if "task_template_id" in seed:
            tpl_ids.add(seed["task_template_id"])
        if "workflow_definition_id" in seed:
            wf_ids.add(seed["workflow_definition_id"])

        # Delete leases by device_id (for job_id=None leases) and by job_id
        for did in device_ids:
            db.query(DeviceLease).filter(DeviceLease.device_id == did).delete()
        for jid in job_ids:
            db.query(DeviceLease).filter(DeviceLease.job_id == jid).delete()
            db.query(StepTrace).filter(StepTrace.job_id == jid).delete()
            db.query(JobInstance).filter(JobInstance.id == jid).delete()
        for rid in run_ids:
            db.query(WorkflowRun).filter(WorkflowRun.id == rid).delete()
        for tid in tpl_ids:
            db.query(TaskTemplate).filter(TaskTemplate.id == tid).delete()
        for wid in wf_ids:
            db.query(WorkflowDefinition).filter(WorkflowDefinition.id == wid).delete()
        for did in device_ids:
            db.query(Device).filter(Device.id == did).delete()
        if host_id:
            db.query(Host).filter(Host.id == host_id).delete()
        db.commit()
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2d: Claim SQL hardening
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_skip_locked_skips_manually_locked_job():
    """Session A manually locks a PENDING job row; session B's SKIP LOCKED
    must skip the locked row and claim the other device's job."""
    seed_a = _seed_job(status=JobStatus.PENDING.value)
    seed_b = _seed_job(status=JobStatus.PENDING.value)
    host_id = seed_a["host_id"]

    # Merge device B and job B onto host A / workflow A
    db_sync = SessionLocal()
    try:
        dev_b = db_sync.get(Device, seed_b["device_id"])
        job_b = db_sync.get(JobInstance, seed_b["job_id"])
        if dev_b:
            dev_b.host_id = host_id
        if job_b:
            job_b.host_id = host_id
            job_b.workflow_run_id = seed_a["workflow_run_id"]
            job_b.task_template_id = seed_a["task_template_id"]
        db_sync.flush()
        # Delete seed_b's orphan records (no longer referenced)
        run_b = db_sync.get(WorkflowRun, seed_b["workflow_run_id"])
        tpl_b = db_sync.get(TaskTemplate, seed_b["task_template_id"])
        wf_b = db_sync.get(WorkflowDefinition, seed_b["workflow_definition_id"])
        host_b = db_sync.get(Host, seed_b["host_id"])
        for obj in (run_b, tpl_b, wf_b, host_b):
            if obj:
                db_sync.delete(obj)
        db_sync.commit()
    finally:
        db_sync.close()

    merged_seed = {
        "host_id": host_id,
        "device_id_a": seed_a["device_id"], "device_id_b": seed_b["device_id"],
        "workflow_definition_id": seed_a["workflow_definition_id"],
        "task_template_id": seed_a["task_template_id"],
        "workflow_run_id": seed_a["workflow_run_id"],
        "job_id_a": seed_a["job_id"], "job_id_b": seed_b["job_id"],
    }

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as db_a:
            async with db_a.begin():
                locked = (await db_a.execute(
                    select(JobInstance).where(
                        JobInstance.id == seed_a["job_id"],
                        JobInstance.status == JobStatus.PENDING.value,
                    ).with_for_update()
                )).scalars().first()
                assert locked is not None, "Must be able to lock job_A"

                async with AsyncSessionLocal() as db_b:
                    claimed, _ = await _claim_jobs_for_host(
                        db_b, host_id, capacity=2,
                    )
                    claimed_ids = [j.id for j in claimed]
                    assert seed_a["job_id"] not in claimed_ids, (
                        "SKIP LOCKED must skip locked job_A"
                    )
                    assert seed_b["job_id"] in claimed_ids, (
                        "SKIP LOCKED: session B must claim unlocked job_B"
                    )
    finally:
        _cleanup_custom(merged_seed)


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("lease_type", [
    LeaseType.JOB,
    LeaseType.SCRIPT,
    LeaseType.MAINTENANCE,
])
async def test_active_lease_excludes_device(lease_type):
    """Any non-expired ACTIVE lease (JOB/SCRIPT/MAINTENANCE) on a device
    excludes its PENDING jobs from claim candidates."""
    suffix = uuid4().hex[:8]
    host_id = f"dwh-{suffix}"
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{suffix}",
            status=HostStatus.ONLINE.value, created_at=now,
        )
        dev_a = Device(serial=f"DWA-{suffix}", host_id=host_id, status="ONLINE", tags=[], created_at=now, adb_connected=True, adb_state="device")
        dev_b = Device(serial=f"DWB-{suffix}", host_id=host_id, status="ONLINE", tags=[], created_at=now, adb_connected=True, adb_state="device")
        wf = WorkflowDefinition(
            name=f"wf-{suffix}", description="", failure_threshold=0.1,
            created_by="pytest", created_at=now, updated_at=now,
        )
        db.add_all([host, dev_a, dev_b, wf])
        db.flush()

        tpl = TaskTemplate(
            workflow_definition_id=wf.id, name=f"tpl-{suffix}",
            pipeline_def=PIPELINE_DEF, sort_order=0, created_at=now,
        )
        db.add(tpl)
        db.flush()

        run = WorkflowRun(
            workflow_definition_id=wf.id,
            status=WorkflowStatus.RUNNING.value,
            failure_threshold=0.1, triggered_by="pytest", started_at=now,
        )
        db.add(run)
        db.flush()

        job_a = JobInstance(
            workflow_run_id=run.id, task_template_id=tpl.id,
            device_id=dev_a.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now, updated_at=now,
        )
        job_b = JobInstance(
            workflow_run_id=run.id, task_template_id=tpl.id,
            device_id=dev_b.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now, updated_at=now,
        )
        db.add_all([job_a, job_b])
        db.flush()

        fencing_token = f"{dev_a.id}:1"
        lease = DeviceLease(
            device_id=dev_a.id, job_id=job_a.id,
            host_id=host_id, lease_type=lease_type.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=fencing_token, lease_generation=1,
            agent_instance_id=host_id,
            acquired_at=now, renewed_at=now,
            expires_at=now + timedelta(seconds=600),
        )
        db.add(lease)
        db.commit()

        seed = {
            "host_id": host_id, "device_id_a": dev_a.id, "device_id_b": dev_b.id,
            "workflow_definition_id": wf.id, "task_template_id": tpl.id,
            "workflow_run_id": run.id, "job_id_a": job_a.id, "job_id_b": job_b.id,
        }
    finally:
        db.close()

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            claimed, _ = await _claim_jobs_for_host(
                async_db, host_id, capacity=10,
            )
            claimed_ids = [j.id for j in claimed]
            assert job_a.id not in claimed_ids, (
                f"Device A with ACTIVE {lease_type.value} lease must be excluded"
            )
            assert len(claimed) == 1, (
                f"Expected exactly 1 claimed (device B), got {len(claimed)}"
            )
            assert claimed[0].id == job_b.id, (
                f"Must claim device B job (id={job_b.id}); "
                f"got job id={claimed[0].id} device_id={claimed[0].device_id}"
            )
    finally:
        _cleanup_custom(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_capacity_capped_by_max_concurrent_jobs():
    """host.max_concurrent_jobs=2, 1 active JOB lease -> effective_capacity=1."""
    suffix = uuid4().hex[:8]
    host_id = f"dwh-{suffix}"
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{suffix}",
            max_concurrent_jobs=2, status=HostStatus.ONLINE.value, created_at=now,
        )
        dev_a = Device(serial=f"CA-{suffix}", host_id=host_id, status="BUSY", tags=[], created_at=now, adb_connected=True, adb_state="device")
        dev_b = Device(serial=f"CB-{suffix}", host_id=host_id, status="ONLINE", tags=[], created_at=now, adb_connected=True, adb_state="device")
        dev_c = Device(serial=f"CC-{suffix}", host_id=host_id, status="ONLINE", tags=[], created_at=now, adb_connected=True, adb_state="device")
        wf = WorkflowDefinition(
            name=f"wf-{suffix}", description="", failure_threshold=0.1,
            created_by="pytest", created_at=now, updated_at=now,
        )
        db.add_all([host, dev_a, dev_b, dev_c, wf])
        db.flush()

        tpl = TaskTemplate(
            workflow_definition_id=wf.id, name=f"tpl-{suffix}",
            pipeline_def=PIPELINE_DEF, sort_order=0, created_at=now,
        )
        db.add(tpl)
        db.flush()

        run = WorkflowRun(
            workflow_definition_id=wf.id,
            status=WorkflowStatus.RUNNING.value,
            failure_threshold=0.1, triggered_by="pytest", started_at=now,
        )
        db.add(run)
        db.flush()

        lease = DeviceLease(
            device_id=dev_a.id, job_id=None, host_id=host_id,
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{dev_a.id}:1", lease_generation=1,
            agent_instance_id=host_id,
            acquired_at=now, renewed_at=now,
            expires_at=now + timedelta(seconds=600),
        )
        db.add(lease)

        job_b = JobInstance(
            workflow_run_id=run.id, task_template_id=tpl.id,
            device_id=dev_b.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now, updated_at=now,
        )
        job_c = JobInstance(
            workflow_run_id=run.id, task_template_id=tpl.id,
            device_id=dev_c.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now + timedelta(seconds=1), updated_at=now,
        )
        db.add_all([job_b, job_c])
        db.commit()

        seed = {
            "host_id": host_id, "device_id_a": dev_a.id, "device_id_b": dev_b.id,
            "device_id_c": dev_c.id, "workflow_definition_id": wf.id,
            "task_template_id": tpl.id, "workflow_run_id": run.id,
            "job_id_b": job_b.id, "job_id_c": job_c.id,
        }
    finally:
        db.close()

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            claimed, _ = await _claim_jobs_for_host(
                async_db, host_id, capacity=10,
            )
            assert len(claimed) == 1, (
                f"Effective capacity must cap at 1; got {len(claimed)}"
            )
            active_leases = (await async_db.execute(
                select(DeviceLease).where(
                    DeviceLease.host_id == host_id,
                    DeviceLease.lease_type == LeaseType.JOB.value,
                    DeviceLease.status == LeaseStatus.ACTIVE.value,
                )
            )).scalars().all()
            assert len(active_leases) == 2, (
                "Should have 2 ACTIVE JOB leases (1 pre-existing + 1 claimed)"
            )
    finally:
        _cleanup_custom(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_zero_capacity_returns_empty_no_state_change():
    """effective_capacity=0 -> empty list, job status unchanged, host lock released."""
    suffix = uuid4().hex[:8]
    host_id = f"dwh-{suffix}"
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{suffix}",
            max_concurrent_jobs=1, status=HostStatus.ONLINE.value, created_at=now,
        )
        dev_a = Device(serial=f"ZA-{suffix}", host_id=host_id, status="BUSY", tags=[], created_at=now, adb_connected=True, adb_state="device")
        dev_b = Device(serial=f"ZB-{suffix}", host_id=host_id, status="ONLINE", tags=[], created_at=now, adb_connected=True, adb_state="device")
        wf = WorkflowDefinition(
            name=f"wf-{suffix}", description="", failure_threshold=0.1,
            created_by="pytest", created_at=now, updated_at=now,
        )
        db.add_all([host, dev_a, dev_b, wf])
        db.flush()

        tpl = TaskTemplate(
            workflow_definition_id=wf.id, name=f"tpl-{suffix}",
            pipeline_def=PIPELINE_DEF, sort_order=0, created_at=now,
        )
        db.add(tpl)
        db.flush()

        run = WorkflowRun(
            workflow_definition_id=wf.id,
            status=WorkflowStatus.RUNNING.value,
            failure_threshold=0.1, triggered_by="pytest", started_at=now,
        )
        db.add(run)
        db.flush()

        lease = DeviceLease(
            device_id=dev_a.id, job_id=None, host_id=host_id,
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{dev_a.id}:1", lease_generation=1,
            agent_instance_id=host_id,
            acquired_at=now, renewed_at=now,
            expires_at=now + timedelta(seconds=600),
        )
        db.add(lease)

        job_b = JobInstance(
            workflow_run_id=run.id, task_template_id=tpl.id,
            device_id=dev_b.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now, updated_at=now,
        )
        db.add(job_b)
        db.commit()

        seed = {
            "host_id": host_id, "device_id_a": dev_a.id, "device_id_b": dev_b.id,
            "workflow_definition_id": wf.id, "task_template_id": tpl.id,
            "workflow_run_id": run.id, "job_id_b": job_b.id,
        }
    finally:
        db.close()

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            claimed, _ = await _claim_jobs_for_host(
                async_db, host_id, capacity=10,
            )
            assert claimed == [], "Zero effective capacity must return empty list"

            job_b_ref = await async_db.get(JobInstance, job_b.id)
            assert job_b_ref.status == JobStatus.PENDING.value, (
                "Job must remain PENDING when no capacity"
            )

        # Verify host lock was released by rollback(): a second session
        # must be able to acquire the host row lock immediately.
        async with AsyncSessionLocal() as verify_db:
            host_check = (await verify_db.execute(
                select(Host).where(Host.id == host_id).with_for_update(nowait=True)
            )).scalars().first()
            assert host_check is not None, (
                "Host row must be lockable immediately — "
                "proves rollback() released the FOR UPDATE lock"
            )
    finally:
        _cleanup_custom(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_expired_active_lease_allows_claim():
    """Expired ACTIVE lease does NOT block claim -- pre-filter passes it through,
    acquire_lease recycles it."""
    seed = _seed_job(status=JobStatus.PENDING.value)
    now = datetime.now(timezone.utc)
    past = now - timedelta(seconds=600)

    db = SessionLocal()
    try:
        lease = DeviceLease(
            device_id=seed["device_id"], job_id=seed["job_id"],
            host_id=seed["host_id"],
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{seed['device_id']}:1",
            lease_generation=1,
            agent_instance_id=seed["host_id"],
            acquired_at=past, renewed_at=past,
            expires_at=past + timedelta(seconds=60),
        )
        db.add(lease)
        db.commit()
    finally:
        db.close()

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            claimed, _ = await _claim_jobs_for_host(
                async_db, seed["host_id"], capacity=10,
            )
            assert len(claimed) == 1, (
                f"Expired ACTIVE lease must not block claim; got {len(claimed)}"
            )
            assert claimed[0].id == seed["job_id"]
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_per_device_first_does_not_waste_capacity():
    """3 devices, 3 PENDING jobs, capacity=2. Per-device row_number() +
    LIMIT picks exactly 2 distinct devices without duplicates."""
    suffix = uuid4().hex[:8]
    host_id = f"dwh-{suffix}"
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{suffix}",
            max_concurrent_jobs=3, status=HostStatus.ONLINE.value, created_at=now,
        )
        dev_a = Device(serial=f"PA-{suffix}", host_id=host_id, status="ONLINE", tags=[], created_at=now, adb_connected=True, adb_state="device")
        dev_b = Device(serial=f"PB-{suffix}", host_id=host_id, status="ONLINE", tags=[], created_at=now, adb_connected=True, adb_state="device")
        dev_c = Device(serial=f"PC-{suffix}", host_id=host_id, status="ONLINE", tags=[], created_at=now, adb_connected=True, adb_state="device")
        wf = WorkflowDefinition(
            name=f"wf-{suffix}", description="", failure_threshold=0.1,
            created_by="pytest", created_at=now, updated_at=now,
        )
        db.add_all([host, dev_a, dev_b, dev_c, wf])
        db.flush()

        tpl = TaskTemplate(
            workflow_definition_id=wf.id, name=f"tpl-{suffix}",
            pipeline_def=PIPELINE_DEF, sort_order=0, created_at=now,
        )
        db.add(tpl)
        db.flush()

        run = WorkflowRun(
            workflow_definition_id=wf.id,
            status=WorkflowStatus.RUNNING.value,
            failure_threshold=0.1, triggered_by="pytest", started_at=now,
        )
        db.add(run)
        db.flush()

        job_a = JobInstance(
            workflow_run_id=run.id, task_template_id=tpl.id,
            device_id=dev_a.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now, updated_at=now,
        )
        job_b = JobInstance(
            workflow_run_id=run.id, task_template_id=tpl.id,
            device_id=dev_b.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now + timedelta(seconds=1), updated_at=now,
        )
        job_c = JobInstance(
            workflow_run_id=run.id, task_template_id=tpl.id,
            device_id=dev_c.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now + timedelta(seconds=2), updated_at=now,
        )
        db.add_all([job_a, job_b, job_c])
        db.commit()

        seed = {
            "host_id": host_id,
            "device_id_a": dev_a.id, "device_id_b": dev_b.id, "device_id_c": dev_c.id,
            "workflow_definition_id": wf.id, "task_template_id": tpl.id,
            "workflow_run_id": run.id,
            "job_a": job_a.id, "job_b": job_b.id, "job_c": job_c.id,
        }
    finally:
        db.close()

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            claimed, _ = await _claim_jobs_for_host(
                async_db, host_id, capacity=2,
            )
            claimed_devices = {j.device_id for j in claimed}

            assert len(claimed) == 2, f"Expected 2 claimed; got {len(claimed)}"
            assert len(claimed_devices) == 2, (
                f"Must claim 2 distinct devices; got {claimed_devices}"
            )
            # The earliest 2 PENDING jobs (by created_at) should be claimed
            claimed_ids = [j.id for j in claimed]
            assert job_a.id in claimed_ids, "Earliest PENDING (device A) must be claimed"
            assert job_b.id in claimed_ids, "Second PENDING (device B) must be claimed"
            assert job_c.id not in claimed_ids, (
                "Third PENDING (device C) must NOT be claimed (capacity=2)"
            )
    finally:
        _cleanup_custom(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_concurrent_claim_capacity_does_not_exceed():
    """Host row lock serialization: max_concurrent_jobs=1, two concurrent claims
    must produce exactly 1 successful claim."""
    import asyncio

    suffix = uuid4().hex[:8]
    host_id = f"dwh-{suffix}"
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{suffix}",
            max_concurrent_jobs=1, status=HostStatus.ONLINE.value, created_at=now,
        )
        dev_a = Device(serial=f"CC1-{suffix}", host_id=host_id, status="ONLINE", tags=[], created_at=now, adb_connected=True, adb_state="device")
        dev_b = Device(serial=f"CC2-{suffix}", host_id=host_id, status="ONLINE", tags=[], created_at=now, adb_connected=True, adb_state="device")
        wf = WorkflowDefinition(
            name=f"wf-{suffix}", description="", failure_threshold=0.1,
            created_by="pytest", created_at=now, updated_at=now,
        )
        db.add_all([host, dev_a, dev_b, wf])
        db.flush()

        tpl = TaskTemplate(
            workflow_definition_id=wf.id, name=f"tpl-{suffix}",
            pipeline_def=PIPELINE_DEF, sort_order=0, created_at=now,
        )
        db.add(tpl)
        db.flush()

        run = WorkflowRun(
            workflow_definition_id=wf.id,
            status=WorkflowStatus.RUNNING.value,
            failure_threshold=0.1, triggered_by="pytest", started_at=now,
        )
        db.add(run)
        db.flush()

        job_a = JobInstance(
            workflow_run_id=run.id, task_template_id=tpl.id,
            device_id=dev_a.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now, updated_at=now,
        )
        job_b = JobInstance(
            workflow_run_id=run.id, task_template_id=tpl.id,
            device_id=dev_b.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now + timedelta(seconds=1), updated_at=now,
        )
        db.add_all([job_a, job_b])
        db.commit()

        seed = {
            "host_id": host_id, "device_id_a": dev_a.id, "device_id_b": dev_b.id,
            "workflow_definition_id": wf.id, "task_template_id": tpl.id,
            "workflow_run_id": run.id, "job_id_a": job_a.id, "job_id_b": job_b.id,
        }
    finally:
        db.close()

    try:
        results = []

        async def _claim(sess):
            async with sess as db:
                claimed, _ = await _claim_jobs_for_host(
                    db, host_id, capacity=1,
                )
                results.append(claimed)

        await async_engine.dispose()
        tasks = [_claim(AsyncSessionLocal()), _claim(AsyncSessionLocal())]
        await asyncio.gather(*tasks)

        total_claimed = sum(len(r) for r in results)
        assert total_claimed == 1, (
            f"Exactly 1 claim must succeed; got {total_claimed}"
        )

        db_verify = SessionLocal()
        try:
            active_leases = (
                db_verify.query(DeviceLease)
                .filter(
                    DeviceLease.host_id == host_id,
                    DeviceLease.lease_type == LeaseType.JOB.value,
                    DeviceLease.status == LeaseStatus.ACTIVE.value,
                )
                .all()
            )
            assert len(active_leases) == 1, (
                f"Must have exactly 1 ACTIVE JOB lease; got {len(active_leases)}"
            )
        finally:
            db_verify.close()
    finally:
        _cleanup_custom(seed)


# ══════════════════════════════════════════════════════════════════════════════
# ADR-0019 Phase 3a: heartbeat stores agent identity
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_heartbeat_stores_agent_identity():
    """Heartbeat 携带 agent_instance_id + boot_id → Host 表正确记录."""
    from backend.api.routes.heartbeat import heartbeat
    from backend.api.schemas.host import HeartbeatIn

    suffix = uuid4().hex[:8]
    host_id = f"ident-host-{suffix}"
    instance_id = uuid4().hex
    boot_id = uuid4().hex

    db = SessionLocal()
    try:
        # Seed host
        host = Host(
            id=host_id,
            hostname=f"ident-{suffix}",
            status=HostStatus.ONLINE.value,
            ip="10.0.0.1",
            ip_address="10.0.0.1",
        )
        db.add(host)
        db.flush()

        # Send heartbeat with identity
        payload = HeartbeatIn(
            host_id=host_id,
            status="ONLINE",
            agent_instance_id=instance_id,
            boot_id=boot_id,
        )
        await heartbeat(payload, db)

        db.refresh(host)
        assert host.boot_id == boot_id, f"boot_id not stored; got {host.boot_id!r}"
        assert host.last_agent_instance_id == instance_id, (
            f"last_agent_instance_id not stored; got {host.last_agent_instance_id!r}"
        )
    finally:
        db.rollback()
        db.query(Host).filter(Host.id == host_id).delete()
        db.commit()
        db.close()


# ---------------------------------------------------------------------------
# ADR-0019 Phase 3a: Claim passes real agent_instance_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_claim_jobs_uses_real_agent_instance_id():
    """claim_jobs 传入真实 agent_instance_id → DeviceLease.agent_instance_id 为 uuid4（非 host_id）。"""
    seed = _seed_job(status=JobStatus.PENDING.value)
    real_instance_id = uuid4().hex
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await claim_jobs(
                payload=ClaimRequest(
                    host_id=seed["host_id"], capacity=5,
                    agent_instance_id=real_instance_id,
                ),
                db=async_db, _=None,
            )
        assert result.error is None
        assert len(result.data) == 1

        db = SessionLocal()
        try:
            lease = (
                db.query(DeviceLease)
                .filter(
                    DeviceLease.device_id == seed["device_id"],
                    DeviceLease.job_id == seed["job_id"],
                    DeviceLease.status == LeaseStatus.ACTIVE.value,
                )
                .first()
            )
            assert lease is not None
            assert lease.agent_instance_id == real_instance_id, (
                f"expected agent_instance_id={real_instance_id!r}, got {lease.agent_instance_id!r}"
            )
            # 关键断言：不再是 host_id
            assert lease.agent_instance_id != seed["host_id"], (
                f"agent_instance_id should NOT equal host_id; got {lease.agent_instance_id!r}"
            )
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_claim_request_default_agent_instance_id_empty():
    """ClaimRequest 不传 agent_instance_id → 默认空字符串（向后兼容）。"""
    req = ClaimRequest(host_id="host-1", capacity=5)
    assert req.agent_instance_id == ""


# ---------------------------------------------------------------------------
# ADR-0019 Phase 3a: Recovery Sync tests
# ---------------------------------------------------------------------------

def _seed_recovery_host(host_id: str, boot_id: str = "", instance_id: str = "") -> Host:
    """Create a minimal host for recovery sync testing."""
    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"rec-{host_id}",
            status=HostStatus.ONLINE.value,
            ip="10.0.0.1", ip_address="10.0.0.1",
            boot_id=boot_id, last_agent_instance_id=instance_id,
        )
        db.add(host)
        db.commit()
        return host
    finally:
        db.close()


def _cleanup_recovery_host(host_id: str) -> None:
    db = SessionLocal()
    try:
        db.query(DeviceLease).filter(DeviceLease.host_id == host_id).delete()
        db.query(StepTrace).filter(StepTrace.job_id.in_(
            db.query(JobInstance.id).filter(JobInstance.host_id == host_id)
        )).delete()
        db.query(JobInstance).filter(JobInstance.host_id == host_id).delete()
        db.query(Device).filter(Device.host_id == host_id).delete()
        db.query(Host).filter(Host.id == host_id).delete()
        db.commit()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_recovery_sync_host_not_found_404():
    """recovery sync with unknown host → 404."""
    payload = _RecoverySyncIn(
        host_id="nonexistent-host-999",
        agent_instance_id=uuid4().hex,
        boot_id=uuid4().hex,
    )
    mock_db = AsyncMock()
    mock_db.get.return_value = None  # host not found
    with pytest.raises(HTTPException) as exc:
        await recovery_sync(payload, db=mock_db, _=None)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_recovery_sync_same_instance_resume():
    """Lease 的 agent_instance_id == 请求的 instance_id → RESUME."""
    suffix = uuid4().hex[:8]
    host_id = f"rec-host-{suffix}"
    instance_id = uuid4().hex
    boot_id = uuid4().hex
    _seed_recovery_host(host_id, boot_id=boot_id, instance_id=instance_id)

    # Create a job + device + ACTIVE lease via _seed_job + claim
    seed = _seed_job(status=JobStatus.RUNNING.value)
    try:
        # Update the job's host_id to our recovery host
        db_sync = SessionLocal()
        try:
            job = db_sync.get(JobInstance, seed["job_id"])
            device = db_sync.get(Device, seed["device_id"])
            device.host_id = host_id
            device.status = "BUSY"
            device.lock_run_id = seed["job_id"]
            device.lock_expires_at = datetime.now(timezone.utc) + timedelta(seconds=600)
            job.host_id = host_id
            db_sync.commit()

            # Create an ACTIVE lease
            lease = DeviceLease(
                device_id=seed["device_id"],
                job_id=seed["job_id"],
                host_id=host_id,
                lease_type=LeaseType.JOB.value,
                status=LeaseStatus.ACTIVE.value,
                fencing_token=f"{seed['device_id']}:1",
                agent_instance_id=instance_id,
                lease_generation=1,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
            )
            db_sync.add(lease)
            db_sync.commit()
        finally:
            db_sync.close()

        payload = _RecoverySyncIn(
            host_id=host_id,
            agent_instance_id=instance_id,
            boot_id=boot_id,
            active_jobs=[_ActiveJobEntry(
                job_id=seed["job_id"], device_id=seed["device_id"],
                fencing_token=f"{seed['device_id']}:1",
            )],
        )

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await recovery_sync(payload, db=async_db, _=None)

        assert result.error is None
        actions = result.data["actions"]
        assert len(actions) == 1
        assert actions[0]["action"] == "RESUME"
        assert actions[0]["reason"] == "same_instance"
        assert actions[0]["fencing_token"] == f"{seed['device_id']}:1"
    finally:
        _cleanup_seed(seed)
        _cleanup_recovery_host(host_id)


@pytest.mark.asyncio
async def test_recovery_sync_legacy_lease_adopted():
    """Lease agent_instance_id == host_id → RESUME (legacy lease adopted)."""
    suffix = uuid4().hex[:8]
    host_id = f"rec-legacy-{suffix}"
    instance_id = uuid4().hex
    boot_id = uuid4().hex
    _seed_recovery_host(host_id, boot_id=boot_id)

    seed = _seed_job(status=JobStatus.RUNNING.value)
    try:
        db_sync = SessionLocal()
        try:
            job = db_sync.get(JobInstance, seed["job_id"])
            device = db_sync.get(Device, seed["device_id"])
            device.host_id = host_id
            device.status = "BUSY"
            device.lock_run_id = seed["job_id"]
            device.lock_expires_at = datetime.now(timezone.utc) + timedelta(seconds=600)
            job.host_id = host_id
            db_sync.commit()

            lease = DeviceLease(
                device_id=seed["device_id"],
                job_id=seed["job_id"],
                host_id=host_id,
                lease_type=LeaseType.JOB.value,
                status=LeaseStatus.ACTIVE.value,
                fencing_token=f"{seed['device_id']}:1",
                agent_instance_id=host_id,  # legacy: equals host_id
                lease_generation=1,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
            )
            db_sync.add(lease)
            db_sync.commit()
        finally:
            db_sync.close()

        payload = _RecoverySyncIn(
            host_id=host_id,
            agent_instance_id=instance_id,
            boot_id=boot_id,
            active_jobs=[_ActiveJobEntry(
                job_id=seed["job_id"], device_id=seed["device_id"],
            )],
        )

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await recovery_sync(payload, db=async_db, _=None)

        assert result.error is None
        actions = result.data["actions"]
        assert len(actions) == 1
        assert actions[0]["action"] == "RESUME"
        assert actions[0]["reason"] == "legacy_lease_adopted"

        # Verify lease agent_instance_id was updated
        db_sync2 = SessionLocal()
        try:
            updated = (
                db_sync2.query(DeviceLease)
                .filter(DeviceLease.job_id == seed["job_id"])
                .first()
            )
            assert updated.agent_instance_id == instance_id
        finally:
            db_sync2.close()
    finally:
        _cleanup_seed(seed)
        _cleanup_recovery_host(host_id)


@pytest.mark.asyncio
async def test_recovery_sync_same_boot_different_instance_resume():
    """Same boot_id, different instance → RESUME (same_boot_instance_updated)."""
    suffix = uuid4().hex[:8]
    host_id = f"rec-boot-{suffix}"
    old_instance = uuid4().hex
    new_instance = uuid4().hex
    boot_id = uuid4().hex
    _seed_recovery_host(host_id, boot_id=boot_id, instance_id=old_instance)

    seed = _seed_job(status=JobStatus.RUNNING.value)
    try:
        db_sync = SessionLocal()
        try:
            job = db_sync.get(JobInstance, seed["job_id"])
            device = db_sync.get(Device, seed["device_id"])
            device.host_id = host_id
            device.status = "BUSY"
            device.lock_run_id = seed["job_id"]
            device.lock_expires_at = datetime.now(timezone.utc) + timedelta(seconds=600)
            job.host_id = host_id
            db_sync.commit()

            lease = DeviceLease(
                device_id=seed["device_id"],
                job_id=seed["job_id"],
                host_id=host_id,
                lease_type=LeaseType.JOB.value,
                status=LeaseStatus.ACTIVE.value,
                fencing_token=f"{seed['device_id']}:1",
                agent_instance_id=old_instance,  # old instance, same boot
                lease_generation=1,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
            )
            db_sync.add(lease)
            db_sync.commit()
        finally:
            db_sync.close()

        payload = _RecoverySyncIn(
            host_id=host_id,
            agent_instance_id=new_instance,
            boot_id=boot_id,  # same boot
            active_jobs=[_ActiveJobEntry(
                job_id=seed["job_id"], device_id=seed["device_id"],
            )],
        )

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await recovery_sync(payload, db=async_db, _=None)

        assert result.error is None
        actions = result.data["actions"]
        assert len(actions) == 1
        assert actions[0]["action"] == "RESUME"
        assert actions[0]["reason"] == "same_boot_instance_updated"

        # Verify lease was adopted
        db_sync2 = SessionLocal()
        try:
            updated = (
                db_sync2.query(DeviceLease)
                .filter(DeviceLease.job_id == seed["job_id"])
                .first()
            )
            assert updated.agent_instance_id == new_instance
        finally:
            db_sync2.close()
    finally:
        _cleanup_seed(seed)
        _cleanup_recovery_host(host_id)


@pytest.mark.asyncio
async def test_recovery_sync_boot_id_mismatch_cleanup():
    """Different boot_id → CLEANUP (release_lease + job→FAILED)."""
    suffix = uuid4().hex[:8]
    host_id = f"rec-cleanup-{suffix}"
    old_boot = uuid4().hex
    new_boot = uuid4().hex
    _seed_recovery_host(host_id, boot_id=old_boot)

    seed = _seed_job(status=JobStatus.RUNNING.value)
    try:
        db_sync = SessionLocal()
        try:
            job = db_sync.get(JobInstance, seed["job_id"])
            device = db_sync.get(Device, seed["device_id"])
            device.host_id = host_id
            device.status = "BUSY"
            device.lock_run_id = seed["job_id"]
            device.lock_expires_at = datetime.now(timezone.utc) + timedelta(seconds=600)
            job.host_id = host_id
            db_sync.commit()

            lease = DeviceLease(
                device_id=seed["device_id"],
                job_id=seed["job_id"],
                host_id=host_id,
                lease_type=LeaseType.JOB.value,
                status=LeaseStatus.ACTIVE.value,
                fencing_token=f"{seed['device_id']}:1",
                agent_instance_id=uuid4().hex,
                lease_generation=1,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
            )
            db_sync.add(lease)
            db_sync.commit()
        finally:
            db_sync.close()

        payload = _RecoverySyncIn(
            host_id=host_id,
            agent_instance_id=uuid4().hex,
            boot_id=new_boot,  # different boot
            active_jobs=[_ActiveJobEntry(
                job_id=seed["job_id"], device_id=seed["device_id"],
            )],
        )

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await recovery_sync(payload, db=async_db, _=None)

        assert result.error is None
        actions = result.data["actions"]
        assert len(actions) == 1
        assert actions[0]["action"] == "CLEANUP"
        assert actions[0]["reason"] == "boot_id_mismatch"

        # Verify lease → RELEASED
        db_sync2 = SessionLocal()
        try:
            updated_lease = (
                db_sync2.query(DeviceLease)
                .filter(DeviceLease.job_id == seed["job_id"])
                .first()
            )
            assert updated_lease.status == LeaseStatus.RELEASED.value

            # Verify job → FAILED
            updated_job = db_sync2.get(JobInstance, seed["job_id"])
            assert updated_job.status == JobStatus.FAILED.value
        finally:
            db_sync2.close()
    finally:
        _cleanup_seed(seed)
        _cleanup_recovery_host(host_id)


@pytest.mark.asyncio
async def test_recovery_sync_no_lease_abort_local():
    """No ACTIVE lease for the job → ABORT_LOCAL."""
    suffix = uuid4().hex[:8]
    host_id = f"rec-nolease-{suffix}"
    _seed_recovery_host(host_id)

    payload = _RecoverySyncIn(
        host_id=host_id,
        agent_instance_id=uuid4().hex,
        boot_id=uuid4().hex,
        active_jobs=[_ActiveJobEntry(
            job_id=99999, device_id=99999,
        )],
    )

    await async_engine.dispose()
    async with AsyncSessionLocal() as async_db:
        result = await recovery_sync(payload, db=async_db, _=None)

    assert result.error is None
    actions = result.data["actions"]
    assert len(actions) == 1
    assert actions[0]["action"] == "ABORT_LOCAL"
    assert actions[0]["reason"] == "no_active_lease"
    _cleanup_recovery_host(host_id)


@pytest.mark.asyncio
async def test_recovery_sync_outbox_not_terminal_upload():
    """Outbox entry for non-terminal job → UPLOAD_TERMINAL."""
    suffix = uuid4().hex[:8]
    host_id = f"rec-outbox-{suffix}"
    _seed_recovery_host(host_id)

    seed = _seed_job(status=JobStatus.RUNNING.value)
    try:
        # Update host_id
        db_sync = SessionLocal()
        try:
            job = db_sync.get(JobInstance, seed["job_id"])
            device = db_sync.get(Device, seed["device_id"])
            device.host_id = host_id
            device.status = "BUSY"
            device.lock_run_id = seed["job_id"]
            device.lock_expires_at = datetime.now(timezone.utc) + timedelta(seconds=600)
            job.host_id = host_id
            db_sync.commit()
        finally:
            db_sync.close()

        payload = _RecoverySyncIn(
            host_id=host_id,
            agent_instance_id=uuid4().hex,
            boot_id=uuid4().hex,
            pending_outbox=[_OutboxEntry(job_id=seed["job_id"], event_type="RUN_COMPLETED")],
        )

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await recovery_sync(payload, db=async_db, _=None)

        assert result.error is None
        outbox_actions = result.data["outbox_actions"]
        assert len(outbox_actions) == 1
        assert outbox_actions[0]["action"] == "UPLOAD_TERMINAL"
        assert outbox_actions[0]["reason"] == "not_terminal_on_backend"
    finally:
        _cleanup_seed(seed)
        _cleanup_recovery_host(host_id)


@pytest.mark.asyncio
async def test_recovery_sync_outbox_already_terminal_noop():
    """Outbox entry for already-terminal job → NOOP."""
    suffix = uuid4().hex[:8]
    host_id = f"rec-noop-{suffix}"
    _seed_recovery_host(host_id)

    seed = _seed_job(status=JobStatus.COMPLETED.value)
    try:
        db_sync = SessionLocal()
        try:
            job = db_sync.get(JobInstance, seed["job_id"])
            device = db_sync.get(Device, seed["device_id"])
            device.host_id = host_id
            device.status = "BUSY"
            device.lock_run_id = seed["job_id"]
            device.lock_expires_at = datetime.now(timezone.utc) + timedelta(seconds=600)
            job.host_id = host_id
            db_sync.commit()
        finally:
            db_sync.close()

        payload = _RecoverySyncIn(
            host_id=host_id,
            agent_instance_id=uuid4().hex,
            boot_id=uuid4().hex,
            pending_outbox=[_OutboxEntry(job_id=seed["job_id"])],
        )

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await recovery_sync(payload, db=async_db, _=None)

        assert result.error is None
        outbox_actions = result.data["outbox_actions"]
        assert len(outbox_actions) == 1
        assert outbox_actions[0]["action"] == "NOOP"
        assert outbox_actions[0]["reason"] == "already_terminal"
    finally:
        _cleanup_seed(seed)
        _cleanup_recovery_host(host_id)


@pytest.mark.asyncio
async def test_recovery_sync_outbox_job_not_found_noop():
    """Outbox entry for nonexistent job → NOOP."""
    suffix = uuid4().hex[:8]
    host_id = f"rec-nf-{suffix}"
    _seed_recovery_host(host_id)

    payload = _RecoverySyncIn(
        host_id=host_id,
        agent_instance_id=uuid4().hex,
        boot_id=uuid4().hex,
        pending_outbox=[_OutboxEntry(job_id=99999)],
    )

    await async_engine.dispose()
    async with AsyncSessionLocal() as async_db:
        result = await recovery_sync(payload, db=async_db, _=None)

    assert result.error is None
    outbox_actions = result.data["outbox_actions"]
    assert len(outbox_actions) == 1
    assert outbox_actions[0]["action"] == "NOOP"
    assert outbox_actions[0]["reason"] == "job_not_found"
    _cleanup_recovery_host(host_id)


@pytest.mark.asyncio
async def test_recovery_sync_boot_id_not_overwritten_before_compare():
    """D1: host.boot_id 在比较之前不覆盖 — 先 snapshot 后比较。"""
    suffix = uuid4().hex[:8]
    host_id = f"rec-d1-{suffix}"
    old_boot = uuid4().hex
    new_boot = uuid4().hex
    new_instance = uuid4().hex
    _seed_recovery_host(host_id, boot_id=old_boot)

    seed = _seed_job(status=JobStatus.RUNNING.value)
    try:
        db_sync = SessionLocal()
        try:
            job = db_sync.get(JobInstance, seed["job_id"])
            device = db_sync.get(Device, seed["device_id"])
            device.host_id = host_id
            device.status = "BUSY"
            device.lock_run_id = seed["job_id"]
            device.lock_expires_at = datetime.now(timezone.utc) + timedelta(seconds=600)
            job.host_id = host_id
            db_sync.commit()

            lease = DeviceLease(
                device_id=seed["device_id"],
                job_id=seed["job_id"],
                host_id=host_id,
                lease_type=LeaseType.JOB.value,
                status=LeaseStatus.ACTIVE.value,
                fencing_token=f"{seed['device_id']}:1",
                agent_instance_id=uuid4().hex,  # some other instance
                lease_generation=1,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
            )
            db_sync.add(lease)
            db_sync.commit()
        finally:
            db_sync.close()

        payload = _RecoverySyncIn(
            host_id=host_id,
            agent_instance_id=new_instance,
            boot_id=new_boot,  # different from old_boot
            active_jobs=[_ActiveJobEntry(
                job_id=seed["job_id"], device_id=seed["device_id"],
            )],
        )

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await recovery_sync(payload, db=async_db, _=None)

        assert result.error is None
        actions = result.data["actions"]
        assert len(actions) == 1
        # 因为 old_boot != new_boot，应触发 CLEANUP 而非 RESUME
        assert actions[0]["action"] == "CLEANUP"
        assert actions[0]["reason"] == "boot_id_mismatch"

        # Verify host.boot_id was updated after comparison
        db_sync2 = SessionLocal()
        try:
            updated_host = db_sync2.get(Host, host_id)
            assert updated_host.boot_id == new_boot
        finally:
            db_sync2.close()
    finally:
        _cleanup_seed(seed)
        _cleanup_recovery_host(host_id)


# ---------------------------------------------------------------------------
# ADR-0019 Phase 3c: Capacity / Health 结构化可观测
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_stores_capacity_and_health():
    """Heartbeat POST → host.extra["capacity"] + host.extra["health"] 正确存储."""
    from backend.api.routes.heartbeat import heartbeat
    from backend.api.schemas.host import HeartbeatIn

    suffix = uuid4().hex[:8]
    host_id = f"caph-host-{suffix}"

    db = SessionLocal()
    try:
        host = Host(
            id=host_id,
            hostname=f"caph-{suffix}",
            status=HostStatus.ONLINE.value,
            ip="10.0.0.2",
            ip_address="10.0.0.2",
        )
        db.add(host)
        db.flush()

        cap = {"available_slots": 5, "max_concurrent_jobs": 8,
               "active_jobs": 3, "online_healthy_devices": 6,
               "effective_slots": 4, "active_devices": 2}
        health = {"status": "HEALTHY", "reasons": [],
                  "cpu_load": 20.0, "ram_usage": 50.0,
                  "disk_usage": 40.0, "mount_ok": True, "adb_ok": True}

        payload = HeartbeatIn(
            host_id=host_id,
            status="ONLINE",
            capacity=cap,
            health=health,
        )
        response_data = await heartbeat(payload, db)

        db.refresh(host)
        assert host.extra.get("capacity") == cap, f"capacity not stored; got {host.extra.get('capacity')}"
        assert host.extra.get("health") == health, f"health not stored; got {host.extra.get('health')}"

        # Heartbeat response also includes backend capacity view
        assert response_data["capacity"]["max_concurrent_jobs"] == 8
        assert response_data["capacity"]["online_healthy_devices"] == 0  # no devices in payload
        assert "backend_available_slots" in response_data["capacity"]
    finally:
        db.rollback()
        db.query(Host).filter(Host.id == host_id).delete()
        db.commit()
        db.close()


def test_hosts_api_returns_capacity_health():
    """_host_to_out() 从 host.extra 正确提取 capacity/health/max_concurrent_jobs."""
    from backend.api.routes.hosts import _host_to_out

    suffix = uuid4().hex[:8]
    host_id = f"hout-host-{suffix}"

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        cap = {"available_slots": 3, "max_concurrent_jobs": 5,
               "effective_slots": 2, "active_jobs": 2,
               "online_healthy_devices": 4, "active_devices": 1}
        health = {"status": "HEALTHY", "reasons": [],
                  "cpu_load": 30.0, "ram_usage": 60.0,
                  "disk_usage": 50.0, "mount_ok": True, "adb_ok": True}

        host = Host(
            id=host_id, hostname=f"hout-{suffix}",
            status=HostStatus.ONLINE.value, ip="10.0.0.3",
            ip_address="10.0.0.3", last_heartbeat=now,
            extra={"capacity": cap, "health": health},
            max_concurrent_jobs=5,
        )
        db.add(host)
        db.commit()
        db.refresh(host)

        host_out = _host_to_out(host)

        assert host_out.capacity == cap, f"capacity mismatch: {host_out.capacity}"
        assert host_out.health == health, f"health mismatch: {host_out.health}"
        assert host_out.max_concurrent_jobs == 5
    finally:
        db.rollback()
        db.query(Host).filter(Host.id == host_id).delete()
        db.commit()
        db.close()


@pytest.mark.asyncio
async def test_claim_filters_unhealthy_devices():
    """adb_connected=false 的设备不进入 claim 候选.

    两个设备共用一个 WorkflowDefinition/WorkflowRun/TaskTemplate，
    各有一个 PENDING job。只 expect healthy device 被 claim。
    """
    suffix = uuid4().hex[:8]
    host_id = f"cfilt-host-{suffix}"
    now = datetime.now(timezone.utc)

    seed_bad = _seed_job(status=JobStatus.PENDING.value)
    seed_good = _seed_job(status=JobStatus.PENDING.value)

    db_sync = SessionLocal()
    try:
        # Update good device to healthy; bad device to unhealthy
        device_good = db_sync.get(Device, seed_good["device_id"])
        device_good.adb_connected = True
        device_good.adb_state = "device"
        device_good.host_id = host_id

        device_bad = db_sync.get(Device, seed_bad["device_id"])
        device_bad.adb_connected = False
        device_bad.adb_state = "offline"
        device_bad.host_id = host_id

        # Reassign both jobs to same host (they were created with different hosts)
        job_good = db_sync.get(JobInstance, seed_good["job_id"])
        job_good.host_id = host_id
        job_good.device_id = seed_good["device_id"]

        job_bad = db_sync.get(JobInstance, seed_bad["job_id"])
        job_bad.host_id = host_id
        job_bad.device_id = seed_bad["device_id"]

        # Create shared host
        host = Host(
            id=host_id, hostname=f"cfilt-{suffix}",
            status=HostStatus.ONLINE.value, max_concurrent_jobs=5,
        )
        db_sync.add(host)
        db_sync.commit()

        try:
            await async_engine.dispose()
            async with AsyncSessionLocal() as async_db:
                claimed, _ = await _claim_jobs_for_host(async_db, host_id, capacity=5)

            claimed_device_ids = {j.device_id for j in claimed}
            assert device_good.id in claimed_device_ids, (
                f"healthy device {device_good.id} should be claimed"
            )
            assert device_bad.id not in claimed_device_ids, (
                f"unhealthy device {device_bad.id} should be filtered"
            )
        finally:
            pass  # cleanup handled by _cleanup_seed below
    finally:
        db_sync.close()

    _cleanup_seed(seed_good)
    _cleanup_seed(seed_bad)
    # Clean up the shared host
    db = SessionLocal()
    try:
        db.query(Host).filter(Host.id == host_id).delete()
        db.commit()
    finally:
        db.close()


def test_hosts_api_backward_compat_no_capacity():
    """旧 host 无 capacity/health → _host_to_out returns None for capacity/health."""
    from backend.api.routes.hosts import _host_to_out

    suffix = uuid4().hex[:8]
    host_id = f"hbc-host-{suffix}"

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        host = Host(
            id=host_id, hostname=f"hbc-{suffix}",
            status=HostStatus.ONLINE.value, ip="10.0.0.4",
            ip_address="10.0.0.4", last_heartbeat=now,
            extra={},  # no capacity/health
        )
        db.add(host)
        db.commit()
        db.refresh(host)

        host_out = _host_to_out(host)

        assert host_out.capacity is None
        assert host_out.health is None
        # max_concurrent_jobs defaults to 2 in DB; backward compat means
        # it's still present but not overridden by extra
        assert host_out.max_concurrent_jobs == 2
    finally:
        db.rollback()
        db.query(Host).filter(Host.id == host_id).delete()
        db.commit()
        db.close()
