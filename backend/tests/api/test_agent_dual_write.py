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
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.skipif(
    os.getenv("DATABASE_URL", "").startswith("sqlite"),
    reason="API 路由双写测试需要 PostgreSQL（device_leases 部分唯一索引）",
)

from backend.api.routes.agent_api import (
    _ExtendLockIn,
    _JobHeartbeatIn,
    _RunCompleteIn,
    ClaimRequest,
    claim_jobs,
    complete_job,
    extend_job_lock,
    get_pending_jobs,
    job_heartbeat,
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
