"""Route-level dual-write tests for ADR-0019 Phase 2a.

直接调用 claim_jobs / get_pending_jobs / extend_job_lock / complete_job
handler，断言 device_leases 写入正确。

Phase 6d-2：device_leases 是真源；生产代码已停止写投影列
（device.lock_run_id / device.lock_expires_at），测试 setup 不再写
投影列，仅保留对 device_leases 的断言 + 投影列 None 的负向断言。

与 tests/services/test_lease_manager.py 的区别：
  - 本文件走路由 handler（agent_api），覆盖完整的请求→响应路径
  - service 级测试只覆盖 lease_manager 的独立行为
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, MagicMock, patch
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
    _agent_version_is_supported,
    _claim_jobs_for_host,
    ClaimRequest,
    JobStatusUpdate,
    StepTraceIn,
    claim_jobs,
    complete_job,
    extend_job_lock,
    get_pending_jobs,
    job_heartbeat,
    recovery_sync,
    update_job_status,
    upload_step_traces,
)
from backend.core.database import AsyncSessionLocal, SessionLocal
from backend.models.audit import AuditLog
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType, PlanRunStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance, StepTrace
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun

PIPELINE_DEF = {
    "lifecycle": {
        "init": [],
        "teardown": [],
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
        plan = Plan(
            name=f"plan-{suffix}", description="dual-write route test",
            failure_threshold=0.1,             created_by="pytest",
        )
        db.add_all([host, device, plan])
        db.flush()

        plan_run = PlanRun(
            plan_id=plan.id,
            status="RUNNING",
            failure_threshold=0.1, plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL", triggered_by="pytest",
        )
        db.add(plan_run)
        db.flush()

        job = JobInstance(
            plan_run_id=plan_run.id, plan_id=plan.id,
            device_id=device.id, host_id=host_id,
            status=status, pipeline_def=PIPELINE_DEF,
            created_at=now, updated_at=now,
            started_at=now if status == JobStatus.RUNNING.value else None,
        )
        db.add(job)
        db.commit()

        return {
            "host_id": host_id, "device_id": device.id,
            "plan_id": plan.id, "plan_run_id": plan_run.id,
            "job_id": job.id,
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

        run_id = seed.get("plan_run_id")
        if run_id:
            db.query(PlanRun).filter(PlanRun.id == run_id).delete()

        plan_id = seed.get("plan_id")
        if plan_id:
            db.query(PlanStep).filter(PlanStep.plan_id == plan_id).delete()
            db.query(Plan).filter(Plan.id == plan_id).delete()

        device_id = seed.get("device_id")
        if device_id:
            db.query(Device).filter(Device.id == device_id).delete()

        host_id = seed.get("host_id")
        if host_id:
            db.query(Host).filter(Host.id == host_id).delete()

        db.commit()
    finally:
        db.close()


def _setup_lock_and_lease(seed: dict) -> str:
    """Pre-populate ACTIVE lease for extend/complete tests.

    Phase 6d: device_leases is the sole source of truth — no projection
    writes to device.lock_run_id / lock_expires_at.
    """
    from datetime import timedelta

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=600)
        fencing_token = f"{seed['device_id']}:1"

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
        return fencing_token
    finally:
        db.close()


# ---------------------------------------------------------------------------
# claim_jobs 路由双写
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="module")
async def test_claim_jobs_writes_lock_and_lease():
    """claim_jobs 成功后只写 device_leases（Phase 6d：投影列已废止）。"""
    seed = _seed_job(status=JobStatus.PENDING.value)
    try:
        async with AsyncSessionLocal() as async_db:
            result = await claim_jobs(
                payload=ClaimRequest(
                    host_id=seed["host_id"], capacity=5, agent_version="2.0.0",
                ),
                db=async_db, _=None,
            )
        assert result.error is None
        assert len(result.data) == 1
        job_out = result.data[0]
        assert job_out.id == seed["job_id"]
        assert job_out.status == JobStatus.RUNNING.value

        # Phase 6d: device_leases is the sole source of truth.
        db = SessionLocal()
        try:
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

@pytest.mark.asyncio(loop_scope="module")
async def test_get_pending_jobs_is_removed_without_claiming():
    """The legacy GET claim path is gone and cannot mutate Job/lease state."""
    seed = _seed_job(status=JobStatus.PENDING.value)
    try:
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as exc:
                await get_pending_jobs(
                    host_id=seed["host_id"], limit=5,
                    db=async_db, _=None,
                )
        assert exc.value.status_code == 410

        db = SessionLocal()
        try:
            # Phase 6d: device_leases is the sole source of truth.
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
            assert lease is None
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


# ---------------------------------------------------------------------------
# extend_job_lock 路由双写
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="module")
async def test_extend_job_lock_renews_lease():
    """extend_job_lock 续期 lease.expires_at（Phase 6d：投影列已废止）。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    token = f"{seed['device_id']}:1"
    try:
        # 记录原始 expires
        db = SessionLocal()
        try:
            # Phase 6d: device_leases is the sole source of truth.
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

        async with AsyncSessionLocal() as async_db:
            result = await extend_job_lock(
                job_id=seed["job_id"],
                payload=_ExtendLockIn(fencing_token=token),
                db=async_db, _=None,
            )
        assert result.error is None
        assert result.data["job_id"] == seed["job_id"]
        assert result.data["expires_at"]

        # Phase 6d: lease 续期生效 + 投影列保持 None。
        db = SessionLocal()
        try:
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

@pytest.mark.asyncio(loop_scope="module")
async def test_complete_job_releases_lock_and_lease():
    """complete_job 后 device_lease → RELEASED（Phase 6d：投影列已废止）。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    token = f"{seed['device_id']}:1"
    try:
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
            # Phase 6d: device_leases is the sole source of truth.
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


@pytest.mark.asyncio(loop_scope="module")
async def test_complete_job_idempotent_replay_skips_release_lease_logging(caplog):
    """已终态 job 重复 complete → 不再重放 release_lease 相关日志。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    token = f"{seed['device_id']}:1"
    try:
        # 第一次 complete
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
        async with AsyncSessionLocal() as async_db:
            result2 = await complete_job(
                job_id=seed["job_id"],
                payload=_RunCompleteIn(update={"status": "FINISHED", "exit_code": 0}, fencing_token=token),
                db=async_db, _=None,
            )
        assert result2.error is None
        assert result2.data["status"] == JobStatus.COMPLETED.value

        # 第二次 complete 直接走 already_terminal 幂等路径，不再执行 release_lease。
        release_lease_logs = [
            r for r in caplog.records
            if "release_lease_miss" in r.message or "release_lease_already_released" in r.message
        ]
        assert len(release_lease_logs) == 0, (
            "Already-terminal replay should skip release_lease logging; "
            f"got {[r.message for r in release_lease_logs]}"
        )
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_postgresql_concurrent_same_terminal_payload_is_idempotent():
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lock_and_lease(seed)
    payload = _RunCompleteIn(
        update={"status": "FINISHED", "exit_code": 0},
        fencing_token=token,
    )

    async def complete_once():
        async with AsyncSessionLocal() as async_db:
            return await complete_job(
                job_id=seed["job_id"],
                payload=payload,
                db=async_db,
                _=None,
            )

    try:
        results = await asyncio.gather(complete_once(), complete_once())
        assert [result.data["status"] for result in results] == [
            JobStatus.COMPLETED.value,
            JobStatus.COMPLETED.value,
        ]
        assert sum(
            result.data.get("idempotent", False) for result in results
        ) == 1
        with SessionLocal() as db:
            job = db.get(JobInstance, seed["job_id"])
            assert job.status == JobStatus.COMPLETED.value
            assert (
                db.query(StepTrace)
                .filter(
                    StepTrace.job_id == seed["job_id"],
                    StepTrace.step_id == "__job__",
                    StepTrace.event_type == "RUN_COMPLETE",
                )
                .count()
                == 1
            )
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_postgresql_concurrent_conflicting_terminal_payload_is_rejected():
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lock_and_lease(seed)
    payloads = [
        _RunCompleteIn(
            update={"status": "FINISHED", "exit_code": 0},
            fencing_token=token,
        ),
        _RunCompleteIn(
            update={"status": "FAILED", "exit_code": 1},
            fencing_token=token,
        ),
    ]

    async def complete_once(payload):
        async with AsyncSessionLocal() as async_db:
            return await complete_job(
                job_id=seed["job_id"],
                payload=payload,
                db=async_db,
                _=None,
            )

    try:
        results = await asyncio.gather(
            *(complete_once(payload) for payload in payloads),
            return_exceptions=True,
        )
        failures = [
            result for result in results if isinstance(result, HTTPException)
        ]
        successes = [
            result for result in results if not isinstance(result, Exception)
        ]
        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0].status_code == 409
        assert failures[0].detail["code"] == "TERMINAL_PAYLOAD_CONFLICT"

        with SessionLocal() as db:
            assert (
                db.query(StepTrace)
                .filter(
                    StepTrace.job_id == seed["job_id"],
                    StepTrace.step_id == "__job__",
                    StepTrace.event_type == "RUN_COMPLETE",
                )
                .count()
                == 1
            )
            assert (
                db.query(AuditLog)
                .filter(
                    AuditLog.action == "terminal_payload_conflict",
                    AuditLog.resource_id == str(seed["job_id"]),
                )
                .count()
                == 1
            )
    finally:
        _cleanup_seed(seed)


# ============================================================================
# Phase 2b fencing_token 强协议测试（14 个路由级）
# ============================================================================


# ── C1: claim_jobs 响应包含 fencing_token ───────────────────────────────────

@pytest.mark.asyncio(loop_scope="module")
async def test_claim_jobs_response_includes_fencing_token():
    """claim_jobs 响应中每个 JobOut 均包含必填 fencing_token 字段。"""
    seed = _seed_job(status=JobStatus.PENDING.value)
    try:
        async with AsyncSessionLocal() as async_db:
            result = await claim_jobs(
                payload=ClaimRequest(
                    host_id=seed["host_id"], capacity=5, agent_version="2.0.0",
                ),
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

@pytest.mark.asyncio(loop_scope="module")
async def test_heartbeat_valid_token_returns_200():
    """正确 fencing_token → heartbeat 200。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    token = f"{seed['device_id']}:1"
    try:
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


@pytest.mark.asyncio(loop_scope="module")
async def test_heartbeat_wrong_token_returns_409():
    """错误 fencing_token → heartbeat 409。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    try:
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


@pytest.mark.asyncio(loop_scope="module")
async def test_heartbeat_no_active_lease_returns_409():
    """无 ACTIVE lease 时 heartbeat 直接 409。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    try:
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

@pytest.mark.asyncio(loop_scope="module")
async def test_extend_lock_valid_token_returns_200():
    """正确 fencing_token → extend_job_lock 200。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    token = f"{seed['device_id']}:1"
    try:
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


@pytest.mark.asyncio(loop_scope="module")
async def test_extend_lock_wrong_token_returns_409():
    """错误 fencing_token → extend_job_lock 409。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    try:
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


@pytest.mark.asyncio(loop_scope="module")
async def test_extend_lock_no_active_lease_returns_409():
    """无 ACTIVE lease 时 extend_job_lock 直接 409。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    try:
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

@pytest.mark.asyncio(loop_scope="module")
async def test_complete_job_valid_token_returns_200():
    """正确 fencing_token（ACTIVE lease）→ complete_job 200。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    token = f"{seed['device_id']}:1"
    try:
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


@pytest.mark.asyncio(loop_scope="module")
async def test_complete_job_wrong_token_returns_409():
    """错误 fencing_token（ACTIVE lease）→ complete_job 409。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    try:
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


@pytest.mark.asyncio(loop_scope="module")
async def test_unknown_complete_recovers_to_running_before_completed():
    """UNKNOWN 的晚到完成必须先续约并走 UNKNOWN→RUNNING，再走 RUNNING→COMPLETED。"""
    from backend.services.state_machine import JobStateMachine

    seed = _seed_job(status=JobStatus.UNKNOWN.value)
    token = _setup_lock_and_lease(seed)
    db = SessionLocal()
    try:
        job = db.get(JobInstance, seed["job_id"])
        job.ended_at = datetime.now(timezone.utc) - timedelta(seconds=30)
        db.commit()
    finally:
        db.close()

    try:
        with patch.object(
            JobStateMachine,
            "transition",
            wraps=JobStateMachine.transition,
        ) as transition:
            async with AsyncSessionLocal() as async_db:
                result = await complete_job(
                    job_id=seed["job_id"],
                    payload=_RunCompleteIn(
                        update={"status": "FINISHED", "exit_code": 0},
                        fencing_token=token,
                    ),
                    db=async_db,
                    _=None,
                )

        targets = [call.args[1] for call in transition.call_args_list]
        assert targets[:2] == [JobStatus.RUNNING, JobStatus.COMPLETED]
        assert result.data["status"] == JobStatus.COMPLETED.value

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            lease = db.query(DeviceLease).filter(
                DeviceLease.job_id == seed["job_id"],
            ).one()
            assert job.status == JobStatus.COMPLETED.value
            assert lease.status == LeaseStatus.RELEASED.value
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_complete_job_missing_token_raises_validation_error():
    """缺 fencing_token → Pydantic 拒收 _RunCompleteIn 构造。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _RunCompleteIn(update={"status": "FINISHED", "exit_code": 0})


# ── C5: complete_job 幂等重放 fencing_token 校验 ────────────────────────────

@pytest.mark.asyncio(loop_scope="module")
async def test_complete_job_idempotent_replay_same_token_returns_200():
    """第一次 complete（ACTIVE→RELEASED），第二次同 token 匹配 RELEASED lease → 200。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    token = f"{seed['device_id']}:1"
    try:
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


@pytest.mark.asyncio(loop_scope="module")
async def test_complete_job_idempotent_replay_wrong_token_returns_409():
    """终态重放仍需匹配历史 lease token，跨 worker/stale token 必须冲突。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    token = f"{seed['device_id']}:1"
    try:
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
        assert exc_info.value.detail["code"] == "STALE_COMPLETION_TOKEN"
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_complete_job_terminal_conflicting_payload_is_read_only():
    """Same-token terminal replay with different facts is a 409 conflict."""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lock_and_lease(seed)
    first_payload = _RunCompleteIn(
        update={
            "status": "FINISHED",
            "exit_code": 0,
            "log_summary": "first-completion",
        },
        artifact={"storage_uri": "file:///first.tar.gz", "checksum": "aaa"},
        fencing_token=token,
    )
    try:
        async with AsyncSessionLocal() as async_db:
            first = await complete_job(
                job_id=seed["job_id"],
                payload=first_payload,
                db=async_db,
                _=None,
            )
        assert first.data["status"] == JobStatus.COMPLETED.value

        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as exc:
                await complete_job(
                    job_id=seed["job_id"],
                    payload=_RunCompleteIn(
                        update={
                            "status": "FAILED",
                            "exit_code": 1,
                            "error_message": "conflicting replay",
                        },
                        artifact={
                            "storage_uri": "file:///conflict.tar.gz",
                            "checksum": "bbb",
                        },
                        fencing_token=token,
                    ),
                    db=async_db,
                    _=None,
                )
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "TERMINAL_PAYLOAD_CONFLICT"
        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            snapshot = db.query(StepTrace).filter(
                StepTrace.job_id == seed["job_id"],
                StepTrace.step_id == "__job__",
                StepTrace.event_type == "RUN_COMPLETE",
            ).one()
            persisted_payload = json.loads(snapshot.output)
            assert job.status == JobStatus.COMPLETED.value
            assert persisted_payload == {
                "update": first_payload.update,
                "artifact": first_payload.artifact,
            }
            conflict_audit = (
                db.query(AuditLog)
                .filter(
                    AuditLog.resource_type == "job",
                    AuditLog.resource_id == str(seed["job_id"]),
                    AuditLog.action == "terminal_payload_conflict",
                )
                .one()
            )
            assert conflict_audit.details["current_status"] == "COMPLETED"
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


# ── C5b: legacy status/steps fencing_token 校验 ─────────────────────────────

@pytest.mark.asyncio(loop_scope="module")
async def test_update_job_status_wrong_token_returns_409():
    """错误 fencing_token → /jobs/{id}/status 不得推进状态。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    try:
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as exc_info:
                await update_job_status(
                    job_id=seed["job_id"],
                    payload=JobStatusUpdate(status="FAILED", fencing_token="WRONG_TOKEN"),
                    db=async_db,
                    _=None,
                )
        assert exc_info.value.status_code == 409
        assert "fencing_token" in exc_info.value.detail.lower()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_update_job_status_invalid_transition_returns_structured_error():
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lock_and_lease(seed)
    try:
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as exc_info:
                await update_job_status(
                    job_id=seed["job_id"],
                    payload=JobStatusUpdate(status="UNKNOWN", fencing_token=token),
                    db=async_db,
                    _=None,
                )
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "INVALID_JOB_TRANSITION"
        assert exc_info.value.detail["message"] == "status endpoint only accepts RUNNING"
    finally:
        _cleanup_seed(seed)


def test_update_job_status_missing_token_raises_validation_error():
    """缺 fencing_token → Pydantic 拒收 JobStatusUpdate 构造。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        JobStatusUpdate(status="FAILED")


@pytest.mark.asyncio(loop_scope="module")
async def test_upload_step_traces_wrong_token_returns_409():
    """错误 fencing_token → /steps 不得写入 StepTrace 或推进 job 状态。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    try:
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as exc_info:
                await upload_step_traces(
                    traces=[
                        StepTraceIn(
                            job_id=seed["job_id"],
                            step_id="fenced-step",
                            event_type="FAILED",
                            status="FAILED",
                            fencing_token="WRONG_TOKEN",
                        )
                    ],
                    db=async_db,
                    _=None,
                )
        assert exc_info.value.status_code == 409
        assert "fencing_token" in exc_info.value.detail.lower()
    finally:
        _cleanup_seed(seed)


def test_upload_step_traces_missing_token_raises_validation_error():
    """缺 fencing_token → Pydantic 拒收 StepTraceIn 构造。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        StepTraceIn(
            job_id=1,
            step_id="missing-token",
            event_type="COMPLETED",
            status="COMPLETED",
        )


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("stage", ["init", "patrol", "teardown"])
async def test_upload_step_traces_failed_event_keeps_job_running(stage: str):
    """普通 step_trace 失败只记时间线，不得提前把 job 收敛成终态。"""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lock_and_lease(seed)
    try:
        async with AsyncSessionLocal() as async_db:
            result = await upload_step_traces(
                traces=[
                    StepTraceIn(
                        job_id=seed["job_id"],
                        step_id=f"{stage}-failed-step",
                        stage=stage,
                        event_type="FAILED",
                        status="FAILED",
                        error_message=f"{stage} replay failure",
                        fencing_token=token,
                    )
                ],
                db=async_db,
                _=None,
            )
        assert result.error is None
        assert result.data["inserted"] == 1
        assert result.data["total"] == 1

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.status == JobStatus.RUNNING.value
            assert job.status_reason != "reconciled_from_replay"

            trace = (
                db.query(StepTrace)
                .filter(
                    StepTrace.job_id == seed["job_id"],
                    StepTrace.step_id == f"{stage}-failed-step",
                    StepTrace.event_type == "FAILED",
                )
                .first()
            )
            assert trace is not None
            assert trace.stage == stage
            assert trace.error_message == f"{stage} replay failure"
        finally:
            db.close()

        async with AsyncSessionLocal() as async_db:
            extend_result = await extend_job_lock(
                job_id=seed["job_id"],
                payload=_ExtendLockIn(fencing_token=token),
                db=async_db,
                _=None,
            )
        assert extend_result.error is None
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_step_trace_event_id_distinguishes_attempts_and_deduplicates_replay():
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lock_and_lease(seed)
    try:
        traces = [
            StepTraceIn(
                job_id=seed["job_id"],
                step_id="retryable-step",
                stage="init",
                event_type="FAILED",
                status="FAILED",
                trace_event_id=f"job:{seed['job_id']}:attempt:{attempt}",
                fencing_token=token,
            )
            for attempt in (1, 2)
        ]
        async with AsyncSessionLocal() as async_db:
            first = await upload_step_traces(traces=traces, db=async_db, _=None)
        assert first.data["inserted"] == 2

        async with AsyncSessionLocal() as async_db:
            replay = await upload_step_traces(
                traces=[traces[1]], db=async_db, _=None,
            )
        assert replay.data["inserted"] == 0
    finally:
        _cleanup_seed(seed)


# ── C6: session_watchdog release_lease ──────────────────────────────────────

@pytest.mark.asyncio(loop_scope="module")
async def test_watchdog_host_timeout_keeps_lease_active():
    """Phase 4c: host heartbeat timeout → UNKNOWN, lease stays ACTIVE.

    Watchdog no longer calls release_lease — Reconciler is the sole handler
    of lease expiration.
    """
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

        async with AsyncSessionLocal() as async_db:
            hosts_off, jobs_unknown = await _check_host_heartbeat_timeouts(async_db)
            await async_db.commit()

        assert hosts_off >= 1
        assert jobs_unknown >= 1

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
            assert lease.status == LeaseStatus.ACTIVE.value, (
                f"Phase 4c: lease must stay ACTIVE after host timeout; got {lease.status}"
            )

            job = db.get(JobInstance, seed["job_id"])
            assert job.status == JobStatus.UNKNOWN.value
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_reconciler_releases_expired_lease_lock_expiration():
    """Phase 4c: expired ACTIVE lease → Reconciler handles (watchdog function removed).

    Reconciler is now the sole handler of lease expiration.
    _check_device_lock_expiration has been deleted in Phase 4c.
    """
    from backend.scheduler.device_lease_reconciler import _reconcile_expired_leases

    seed = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lock_and_lease(seed)
    try:
        # Expire the LEASE
        db = SessionLocal()
        try:
            db.query(DeviceLease).filter(
                DeviceLease.device_id == seed["device_id"],
                DeviceLease.job_id == seed["job_id"],
            ).update({"expires_at": datetime(2020, 1, 1, tzinfo=timezone.utc)})
            db.commit()
        finally:
            db.close()

        async with AsyncSessionLocal() as async_db:
            unknown, failed, terminal = await _reconcile_expired_leases(async_db)
            await async_db.commit()

        # Phase 1: expired ACTIVE + RUNNING → UNKNOWN
        assert unknown >= 1, f"Expected UNKNOWN transition; got unknown={unknown}"

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
            assert lease.status == LeaseStatus.ACTIVE.value, (
                f"Phase 1: lease stays ACTIVE during grace; got {lease.status}"
            )
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2c API integration tests
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_get_pending_jobs_returns_gone():
    """GET /jobs/pending is a hard protocol cut, not a compatibility path."""
    from fastapi import Response as FapiResponse

    seed = _seed_job(status=JobStatus.PENDING.value)
    try:
        r = FapiResponse()
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as exc:
                await get_pending_jobs(
                    host_id=seed["host_id"], limit=5,
                    response=r, db=async_db, _=None,
                )
        assert exc.value.status_code == 410
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_get_pending_jobs_never_claims():
    """The removed endpoint never creates a fencing token."""
    seed = _seed_job(status=JobStatus.PENDING.value)
    try:
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as exc:
                await get_pending_jobs(
                    host_id=seed["host_id"], limit=5,
                    db=async_db, _=None,
                )
        assert exc.value.status_code == 410

        # Phase 6d: device_leases is the sole source of truth.
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_claim_jobs_skip_on_active_lease():
    """设备已有 ACTIVE lease 时 claim_jobs 跳过该设备上的 PENDING job (Phase 2c)."""
    from unittest.mock import patch

    from backend.core.metrics import claim_lease_failed_total

    seed = _seed_job(status=JobStatus.PENDING.value)
    _setup_lock_and_lease(seed)
    try:
        with patch.object(claim_lease_failed_total, "inc") as mock_inc:
            async with AsyncSessionLocal() as async_db:
                result = await claim_jobs(
                    payload=ClaimRequest(
                        host_id=seed["host_id"], capacity=5, agent_version="2.0.0",
                    ),
                    db=async_db, _=None,
                )
            mock_inc.assert_not_called()
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


@pytest.mark.asyncio(loop_scope="module")
async def test_claim_jobs_claims_after_expired_lease():
    """Phase 4b blocking lease: expired ACTIVE lease blocks claim (0 jobs returned).

    The auto-recycle (Phase 2c Step 2.5) is removed.  Only Reconciler can
    release grace-held leases.  Claim must skip the blocked device.
    """
    from datetime import timedelta

    seed = _seed_job(status=JobStatus.PENDING.value)
    old_lease_id = None
    # Create expired ACTIVE lease using sync session — blocks the device
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
        # Phase 6d: projection columns (lock_run_id / lock_expires_at) are
        # decommissioned; the reconciler reads device_leases directly.
        db.commit()
    finally:
        db.close()

    try:
        async with AsyncSessionLocal() as async_db:
            result = await claim_jobs(
                payload=ClaimRequest(
                    host_id=seed["host_id"], capacity=5, agent_version="2.0.0",
                ),
                db=async_db, _=None,
            )
        # Phase 4b: expired ACTIVE lease is a blocking grace-held lease.
        # The device is excluded by the pre-filter, so claim returns 0.
        assert len(result.data) == 0, (
            f"Phase 4b: expired ACTIVE lease must block claim; got {len(result.data)}"
        )

        # The old lease stays ACTIVE (not auto-recycled to EXPIRED)
        db = SessionLocal()
        try:
            old_lease = db.get(DeviceLease, old_lease_id)
            assert old_lease is not None
            assert old_lease.status == LeaseStatus.ACTIVE.value, (
                f"Phase 4b: grace-held lease stays ACTIVE; got {old_lease.status}"
            )
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2c watchdog: reads DeviceLease NOT Device.lock_run_id
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_reconciler_reads_device_leases_not_device_table():
    """Phase 4c: expired ACTIVE lease → Reconciler Phase 1, even when lock_run_id=NULL.

    _check_device_lock_expiration deleted in Phase 4c. Reconciler is the
    sole handler — it reads DeviceLease table, not Device.lock_run_id.
    """
    from backend.scheduler.device_lease_reconciler import _reconcile_expired_leases

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
        # Phase 6d: projection columns are decommissioned and always NULL —
        # this scenario is now the default state, no extra setup needed.
        db.commit()
    finally:
        db.close()

    try:
        async with AsyncSessionLocal() as async_db:
            unknown, failed, terminal = await _reconcile_expired_leases(async_db)
            await async_db.commit()

        # Reconciler Phase 1: expired ACTIVE + RUNNING → UNKNOWN
        assert unknown >= 1, (
            "Reconciler must detect expired lease even when lock_run_id=NULL"
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
            assert dl.status == LeaseStatus.ACTIVE.value, (
                f"Phase 1: lease stays ACTIVE during grace; got {dl.status}"
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
        plan_ids = set()
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
        if "plan_run_id" in seed:
            run_ids.add(seed["plan_run_id"])
        if "plan_id" in seed:
            plan_ids.add(seed["plan_id"])

        # Delete leases by device_id (for job_id=None leases) and by job_id
        for did in device_ids:
            db.query(DeviceLease).filter(DeviceLease.device_id == did).delete()
        for jid in job_ids:
            db.query(DeviceLease).filter(DeviceLease.job_id == jid).delete()
            db.query(StepTrace).filter(StepTrace.job_id == jid).delete()
            db.query(JobInstance).filter(JobInstance.id == jid).delete()
        for rid in run_ids:
            db.query(PlanRun).filter(PlanRun.id == rid).delete()
        for pid in plan_ids:
            db.query(PlanStep).filter(PlanStep.plan_id == pid).delete()
            db.query(Plan).filter(Plan.id == pid).delete()
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
            job_b.plan_run_id = seed_a["plan_run_id"]
            job_b.plan_id = seed_a["plan_id"]
        db_sync.flush()
        # Delete seed_b's orphan records (no longer referenced)
        run_b = db_sync.get(PlanRun, seed_b["plan_run_id"])
        plan_b = db_sync.get(Plan, seed_b["plan_id"])
        host_b = db_sync.get(Host, seed_b["host_id"])
        for obj in (run_b, plan_b, host_b):
            if obj:
                db_sync.delete(obj)
        db_sync.commit()
    finally:
        db_sync.close()

    merged_seed = {
        "host_id": host_id,
        "device_id_a": seed_a["device_id"], "device_id_b": seed_b["device_id"],
        "plan_id": seed_a["plan_id"],
        "plan_run_id": seed_a["plan_run_id"],
        "job_id_a": seed_a["job_id"], "job_id_b": seed_b["job_id"],
    }

    try:
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
        plan = Plan(
            name=f"plan-{suffix}", description="", failure_threshold=0.1,
                        created_by="pytest",
        )
        db.add_all([host, dev_a, dev_b, plan])
        db.flush()


        run = PlanRun(
            plan_id=plan.id,
            status="RUNNING",
            failure_threshold=0.1, plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL", triggered_by="pytest",
        )
        db.add(run)
        db.flush()

        job_a = JobInstance(
            plan_run_id=run.id, plan_id=plan.id,
            device_id=dev_a.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now, updated_at=now,
        )
        job_b = JobInstance(
            plan_run_id=run.id, plan_id=plan.id,
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
            "plan_id": plan.id, "plan_run_id": run.id, "job_id_a": job_a.id, "job_id_b": job_b.id,
        }
    finally:
        db.close()

    try:
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
async def test_capacity_directly_limits_claim():
    """Agent capacity=1 -> only 1 job claimed regardless of free devices."""
    suffix = uuid4().hex[:8]
    host_id = f"dwh-{suffix}"
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{suffix}",
            status=HostStatus.ONLINE.value, created_at=now,
        )
        dev_a = Device(serial=f"CA-{suffix}", host_id=host_id, status="BUSY", tags=[], created_at=now, adb_connected=True, adb_state="device")
        dev_b = Device(serial=f"CB-{suffix}", host_id=host_id, status="ONLINE", tags=[], created_at=now, adb_connected=True, adb_state="device")
        dev_c = Device(serial=f"CC-{suffix}", host_id=host_id, status="ONLINE", tags=[], created_at=now, adb_connected=True, adb_state="device")
        plan = Plan(
            name=f"plan-{suffix}", description="", failure_threshold=0.1,
                        created_by="pytest",
        )
        db.add_all([host, dev_a, dev_b, dev_c, plan])
        db.flush()


        run = PlanRun(
            plan_id=plan.id,
            status="RUNNING",
            failure_threshold=0.1, plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL", triggered_by="pytest",
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
            plan_run_id=run.id, plan_id=plan.id,
            device_id=dev_b.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now, updated_at=now,
        )
        job_c = JobInstance(
            plan_run_id=run.id, plan_id=plan.id,
            device_id=dev_c.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now + timedelta(seconds=1), updated_at=now,
        )
        db.add_all([job_b, job_c])
        db.commit()

        seed = {
            "host_id": host_id, "device_id_a": dev_a.id, "device_id_b": dev_b.id,
            "device_id_c": dev_c.id, "plan_id": plan.id, "plan_run_id": run.id,
            "job_id_b": job_b.id, "job_id_c": job_c.id,
        }
    finally:
        db.close()

    try:
        async with AsyncSessionLocal() as async_db:
            claimed, _ = await _claim_jobs_for_host(
                async_db, host_id, capacity=1,
            )
            assert len(claimed) == 1, (
                f"Agent capacity=1 must limit to 1 claimed; got {len(claimed)}"
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
    """capacity=0 -> empty list, job status unchanged, host lock released."""
    suffix = uuid4().hex[:8]
    host_id = f"dwh-{suffix}"
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{suffix}",
            status=HostStatus.ONLINE.value, created_at=now,
        )
        dev_a = Device(serial=f"ZA-{suffix}", host_id=host_id, status="BUSY", tags=[], created_at=now, adb_connected=True, adb_state="device")
        dev_b = Device(serial=f"ZB-{suffix}", host_id=host_id, status="ONLINE", tags=[], created_at=now, adb_connected=True, adb_state="device")
        plan = Plan(
            name=f"plan-{suffix}", description="", failure_threshold=0.1,
                        created_by="pytest",
        )
        db.add_all([host, dev_a, dev_b, plan])
        db.flush()


        run = PlanRun(
            plan_id=plan.id,
            status="RUNNING",
            failure_threshold=0.1, plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL", triggered_by="pytest",
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
            plan_run_id=run.id, plan_id=plan.id,
            device_id=dev_b.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now, updated_at=now,
        )
        db.add(job_b)
        db.commit()

        seed = {
            "host_id": host_id, "device_id_a": dev_a.id, "device_id_b": dev_b.id,
            "plan_id": plan.id, "plan_run_id": run.id, "job_id_b": job_b.id,
        }
    finally:
        db.close()

    try:
        async with AsyncSessionLocal() as async_db:
            claimed, _ = await _claim_jobs_for_host(
                async_db, host_id, capacity=0,
            )
            assert claimed == [], "capacity=0 must return empty list"

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
    """Phase 4b: expired ACTIVE lease blocks claim (grace-held blocking lease).

    The device is excluded by the pre-filter even though the lease is expired.
    Only Reconciler can release it.
    """
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
        async with AsyncSessionLocal() as async_db:
            claimed, _ = await _claim_jobs_for_host(
                async_db, seed["host_id"], capacity=10,
            )
            # Phase 4b: expired ACTIVE lease blocks the device
            assert len(claimed) == 0, (
                f"Phase 4b: expired ACTIVE lease must block claim; got {len(claimed)}"
            )
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
            status=HostStatus.ONLINE.value, created_at=now,
        )
        dev_a = Device(serial=f"PA-{suffix}", host_id=host_id, status="ONLINE", tags=[], created_at=now, adb_connected=True, adb_state="device")
        dev_b = Device(serial=f"PB-{suffix}", host_id=host_id, status="ONLINE", tags=[], created_at=now, adb_connected=True, adb_state="device")
        dev_c = Device(serial=f"PC-{suffix}", host_id=host_id, status="ONLINE", tags=[], created_at=now, adb_connected=True, adb_state="device")
        plan = Plan(
            name=f"plan-{suffix}", description="", failure_threshold=0.1,
                        created_by="pytest",
        )
        db.add_all([host, dev_a, dev_b, dev_c, plan])
        db.flush()


        run = PlanRun(
            plan_id=plan.id,
            status="RUNNING",
            failure_threshold=0.1, plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL", triggered_by="pytest",
        )
        db.add(run)
        db.flush()

        job_a = JobInstance(
            plan_run_id=run.id, plan_id=plan.id,
            device_id=dev_a.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now, updated_at=now,
        )
        job_b = JobInstance(
            plan_run_id=run.id, plan_id=plan.id,
            device_id=dev_b.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now + timedelta(seconds=1), updated_at=now,
        )
        job_c = JobInstance(
            plan_run_id=run.id, plan_id=plan.id,
            device_id=dev_c.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now + timedelta(seconds=2), updated_at=now,
        )
        db.add_all([job_a, job_b, job_c])
        db.commit()

        seed = {
            "host_id": host_id,
            "device_id_a": dev_a.id, "device_id_b": dev_b.id, "device_id_c": dev_c.id,
            "plan_id": plan.id, "plan_run_id": run.id,
            "job_a": job_a.id, "job_b": job_b.id, "job_c": job_c.id,
        }
    finally:
        db.close()

    try:
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
    """Host row lock serialization: two concurrent claims with capacity=1
    each must each claim a different device."""
    import asyncio

    suffix = uuid4().hex[:8]
    host_id = f"dwh-{suffix}"
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{suffix}",
            status=HostStatus.ONLINE.value, created_at=now,
        )
        dev_a = Device(serial=f"CC1-{suffix}", host_id=host_id, status="ONLINE", tags=[], created_at=now, adb_connected=True, adb_state="device")
        dev_b = Device(serial=f"CC2-{suffix}", host_id=host_id, status="ONLINE", tags=[], created_at=now, adb_connected=True, adb_state="device")
        plan = Plan(
            name=f"plan-{suffix}", description="", failure_threshold=0.1,
                        created_by="pytest",
        )
        db.add_all([host, dev_a, dev_b, plan])
        db.flush()


        run = PlanRun(
            plan_id=plan.id,
            status="RUNNING",
            failure_threshold=0.1, plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL", triggered_by="pytest",
        )
        db.add(run)
        db.flush()

        job_a = JobInstance(
            plan_run_id=run.id, plan_id=plan.id,
            device_id=dev_a.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now, updated_at=now,
        )
        job_b = JobInstance(
            plan_run_id=run.id, plan_id=plan.id,
            device_id=dev_b.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=now + timedelta(seconds=1), updated_at=now,
        )
        db.add_all([job_a, job_b])
        db.commit()

        seed = {
            "host_id": host_id, "device_id_a": dev_a.id, "device_id_b": dev_b.id,
            "plan_id": plan.id, "plan_run_id": run.id, "job_id_a": job_a.id, "job_id_b": job_b.id,
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

        tasks = [_claim(AsyncSessionLocal()), _claim(AsyncSessionLocal())]
        await asyncio.gather(*tasks)

        total_claimed = sum(len(r) for r in results)
        assert total_claimed == 2, (
            f"Each concurrent claim with capacity=1 must claim a different device (2 total); got {total_claimed}"
        )
        assert all(len(r) <= 1 for r in results), (
            f"No single claim may exceed its capacity=1; got {[len(r) for r in results]}"
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
            assert len(active_leases) == 2, (
                f"Must have exactly 2 ACTIVE JOB leases; got {len(active_leases)}"
            )
            lease_device_ids = {l.device_id for l in active_leases}
            assert len(lease_device_ids) == 2, (
                "Leases must be on distinct devices (no device double-lease)"
            )
        finally:
            db_verify.close()
    finally:
        _cleanup_custom(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_overclaim_clamped_to_free_device_count():
    """capacity=100 with 10 free devices → claim must not exceed 10 (#9)."""
    suffix = uuid4().hex[:8]
    host_id = f"oc-{suffix}"
    now = datetime.now(timezone.utc)
    N_DEVICES = 10

    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"oc-h-{suffix}",
            status=HostStatus.ONLINE.value, created_at=now,
        )
        plan = Plan(
            name=f"oc-plan-{suffix}", description="", failure_threshold=0.1,
            created_by="pytest",
        )
        db.add_all([host, plan])
        db.flush()

        run = PlanRun(
            plan_id=plan.id,
            status="RUNNING",
            failure_threshold=0.1,
            plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL", triggered_by="pytest",
        )
        db.add(run)
        db.flush()

        devices = []
        jobs = []
        for i in range(N_DEVICES):
            dev = Device(
                serial=f"OC{i}-{suffix}", host_id=host_id, status="ONLINE",
                tags=[], created_at=now, adb_connected=True, adb_state="device",
            )
            db.add(dev)
            db.flush()
            devices.append(dev)
            job = JobInstance(
                plan_run_id=run.id, plan_id=plan.id,
                device_id=dev.id, host_id=host_id,
                status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
                created_at=now + timedelta(seconds=i), updated_at=now,
            )
            db.add(job)
            db.flush()
            jobs.append(job)

        db.commit()
        seed = {"host_id": host_id, "plan_id": plan.id, "plan_run_id": run.id}
        for d in devices:
            seed[f"device_{d.id}"] = d.id
        for j in jobs:
            seed[f"job_{j.id}"] = j.id
    finally:
        db.close()

    try:
        async with AsyncSessionLocal() as db:
            claimed, _ = await _claim_jobs_for_host(
                db, host_id, capacity=100,
            )
            assert len(claimed) <= N_DEVICES, (
                f"Claim with capacity=100 must not exceed {N_DEVICES} free devices; got {len(claimed)}"
            )
    finally:
        _cleanup_custom(seed)


# ══════════════════════════════════════════════════════════════════════════════
# ADR-0019 Phase 3a: heartbeat stores agent identity
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
async def test_claim_jobs_uses_real_agent_instance_id():
    """claim_jobs 传入真实 agent_instance_id → DeviceLease.agent_instance_id 为 uuid4（非 host_id）。"""
    seed = _seed_job(status=JobStatus.PENDING.value)
    real_instance_id = uuid4().hex
    try:
        async with AsyncSessionLocal() as async_db:
            result = await claim_jobs(
                payload=ClaimRequest(
                    host_id=seed["host_id"], capacity=5,
                    agent_instance_id=real_instance_id,
                    agent_version="2.0.0",
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
    """agent_instance_id remains optional; version is part of the new protocol."""
    req = ClaimRequest(host_id="host-1", capacity=5, agent_version="2.0.0")
    assert req.agent_instance_id == ""


def test_claim_protocol_enforces_minimum_agent_version():
    assert _agent_version_is_supported("2.0.0", "2.0.0")
    assert _agent_version_is_supported("2.1.0", "2.0.9")
    assert not _agent_version_is_supported("2.1", "2.0.9")
    assert not _agent_version_is_supported("1.9.9", "2.0.0")
    assert not _agent_version_is_supported("unknown", "2.0.0")


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


@pytest.mark.asyncio(loop_scope="module")
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


@pytest.mark.asyncio(loop_scope="module")
async def test_recovery_sync_same_instance_is_noop():
    """同一 live instance 重复 sync 不得启动第二个本地 worker。"""
    suffix = uuid4().hex[:8]
    host_id = f"rec-host-{suffix}"
    instance_id = uuid4().hex
    boot_id = uuid4().hex
    _seed_recovery_host(host_id, boot_id=boot_id, instance_id=instance_id)

    # Create a job + device + ACTIVE lease via _seed_job + claim
    seed = _seed_job(status=JobStatus.RUNNING.value)
    try:
        actual_serial = ""
        # Update the job's host_id to our recovery host
        db_sync = SessionLocal()
        try:
            job = db_sync.get(JobInstance, seed["job_id"])
            device = db_sync.get(Device, seed["device_id"])
            device.host_id = host_id
            device.status = "BUSY"
            job.host_id = host_id
            actual_serial = device.serial
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

        async with AsyncSessionLocal() as async_db:
            result = await recovery_sync(payload, db=async_db, _=None)

        assert result.error is None
        actions = result.data["actions"]
        assert len(actions) == 1
        assert actions[0]["action"] == "NOOP"
        assert actions[0]["reason"] == "same_instance_worker_already_owned"
        assert actions[0]["fencing_token"] == f"{seed['device_id']}:1"
        assert actions[0]["device_serial"] == actual_serial
        assert actions[0]["job_payload"] is None
    finally:
        _cleanup_seed(seed)
        _cleanup_recovery_host(host_id)


@pytest.mark.asyncio(loop_scope="module")
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
                fencing_token=f"{seed['device_id']}:1",
            )],
        )

        async with AsyncSessionLocal() as async_db:
            result = await recovery_sync(payload, db=async_db, _=None)

        assert result.error is None
        actions = result.data["actions"]
        assert len(actions) == 1
        assert actions[0]["action"] == "RESUME"
        assert actions[0]["reason"] == "same_boot_instance_takeover"

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


@pytest.mark.asyncio(loop_scope="module")
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
                fencing_token=f"{seed['device_id']}:1",
            )],
        )

        async with AsyncSessionLocal() as async_db:
            result = await recovery_sync(payload, db=async_db, _=None)

        assert result.error is None
        actions = result.data["actions"]
        assert len(actions) == 1
        assert actions[0]["action"] == "RESUME"
        assert actions[0]["reason"] == "same_boot_instance_takeover"

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


@pytest.mark.asyncio(loop_scope="module")
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
                fencing_token=f"{seed['device_id']}:1",
            )],
        )

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


@pytest.mark.asyncio(loop_scope="module")
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

    async with AsyncSessionLocal() as async_db:
        result = await recovery_sync(payload, db=async_db, _=None)

    assert result.error is None
    actions = result.data["actions"]
    assert len(actions) == 1
    assert actions[0]["action"] == "ABORT_LOCAL"
    assert actions[0]["reason"] == "no_active_lease"
    _cleanup_recovery_host(host_id)


@pytest.mark.asyncio(loop_scope="module")
async def test_recovery_sync_device_serial_mismatch_abort_local():
    """上报的 device_serial 与原 job/device SN 不一致时，不允许恢复。"""
    suffix = uuid4().hex[:8]
    host_id = f"rec-serial-{suffix}"
    instance_id = uuid4().hex
    boot_id = uuid4().hex
    _seed_recovery_host(host_id, boot_id=boot_id, instance_id=instance_id)

    seed = _seed_job(status=JobStatus.RUNNING.value)
    try:
        db_sync = SessionLocal()
        try:
            job = db_sync.get(JobInstance, seed["job_id"])
            device = db_sync.get(Device, seed["device_id"])
            device.host_id = host_id
            device.status = "BUSY"
            job.host_id = host_id
            db_sync.commit()

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
            actual_serial = device.serial
        finally:
            db_sync.close()

        payload = _RecoverySyncIn(
            host_id=host_id,
            agent_instance_id=instance_id,
            boot_id=boot_id,
            active_jobs=[_ActiveJobEntry(
                job_id=seed["job_id"],
                device_id=seed["device_id"],
                fencing_token=f"{seed['device_id']}:1",
                device_serial=f"{actual_serial}-WRONG",
            )],
        )

        async with AsyncSessionLocal() as async_db:
            result = await recovery_sync(payload, db=async_db, _=None)

        assert result.error is None
        actions = result.data["actions"]
        assert len(actions) == 1
        assert actions[0]["action"] == "ABORT_LOCAL"
        assert actions[0]["reason"] == "recovery_ownership_mismatch"

        db_sync2 = SessionLocal()
        try:
            lease = (
                db_sync2.query(DeviceLease)
                .filter(DeviceLease.job_id == seed["job_id"])
                .first()
            )
            job = db_sync2.get(JobInstance, seed["job_id"])
            assert lease is not None and lease.status == LeaseStatus.ACTIVE.value
            assert job.status == JobStatus.RUNNING.value
        finally:
            db_sync2.close()
    finally:
        _cleanup_seed(seed)
        _cleanup_recovery_host(host_id)


@pytest.mark.asyncio(loop_scope="module")
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


@pytest.mark.asyncio(loop_scope="module")
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


@pytest.mark.asyncio(loop_scope="module")
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

    async with AsyncSessionLocal() as async_db:
        result = await recovery_sync(payload, db=async_db, _=None)

    assert result.error is None
    outbox_actions = result.data["outbox_actions"]
    assert len(outbox_actions) == 1
    assert outbox_actions[0]["action"] == "NOOP"
    assert outbox_actions[0]["reason"] == "job_not_found"
    _cleanup_recovery_host(host_id)


@pytest.mark.asyncio(loop_scope="module")
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
                fencing_token=f"{seed['device_id']}:1",
            )],
        )

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


@pytest.mark.asyncio(loop_scope="module")
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

        cap = {"available_slots": 5,
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

        # Heartbeat response capacity view (93b9935 后仅含 online_healthy_devices)
        assert response_data["capacity"] == {"online_healthy_devices": 0}  # no devices in payload
    finally:
        db.rollback()
        db.query(Host).filter(Host.id == host_id).delete()
        db.commit()
        db.close()


def test_hosts_api_returns_capacity_health():
    """_host_to_out() 从 host.extra 正确提取 capacity/health."""
    from backend.api.routes.hosts import _host_to_out

    suffix = uuid4().hex[:8]
    host_id = f"hout-host-{suffix}"

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        cap = {"available_slots": 3,
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
        )
        db.add(host)
        db.commit()
        db.refresh(host)

        host_out = _host_to_out(host)

        assert host_out.capacity == cap, f"capacity mismatch: {host_out.capacity}"
        assert host_out.health == health, f"health mismatch: {host_out.health}"
    finally:
        db.rollback()
        db.query(Host).filter(Host.id == host_id).delete()
        db.commit()
        db.close()


@pytest.mark.asyncio(loop_scope="module")
async def test_claim_filters_unhealthy_devices():
    """adb_connected=false 的设备不进入 claim 候选.

    两个设备共用一个 Plan/PlanRun，
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
            status=HostStatus.ONLINE.value,
        )
        db_sync.add(host)
        db_sync.commit()

        try:
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
    finally:
        db.rollback()
        db.query(Host).filter(Host.id == host_id).delete()
        db.commit()
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# Phase 4b: Watchdog / Reconciler / Recovery Sync 补充测试
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_watchdog_does_not_release_grace_held_lease():
    """Phase 4b: session_watchdog_once() 不再释放过期 ACTIVE lease（仅 Reconciler 可释放）。"""
    from backend.tasks.session_watchdog import session_watchdog_once

    seed = _seed_job(status=JobStatus.RUNNING.value)
    try:
        past = datetime.now(timezone.utc) - timedelta(seconds=3600)
        db = SessionLocal()
        try:
            lease = DeviceLease(
                device_id=seed["device_id"], job_id=seed["job_id"],
                host_id=seed["host_id"],
                lease_type=LeaseType.JOB.value, status=LeaseStatus.ACTIVE.value,
                fencing_token=f"{seed['device_id']}:1", lease_generation=1,
                agent_instance_id=seed["host_id"],
                acquired_at=past - timedelta(seconds=7200),
                renewed_at=past, expires_at=past,
            )
            db.add(lease)
            dev = db.get(Device, seed["device_id"])
            dev.status = "BUSY"
            db.commit()
            lease_id = lease.id
        finally:
            db.close()

        await session_watchdog_once()

        db2 = SessionLocal()
        try:
            l = db2.get(DeviceLease, lease_id)
            assert l is not None
            assert l.status == LeaseStatus.ACTIVE.value, (
                f"Watchdog must NOT release grace-held lease; got {l.status}"
            )
        finally:
            db2.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_heartbeat_blocking_lease_reduces_available_slots():
    """Phase 4b: 过期 ACTIVE lease 计入 heartbeat backend_available_slots 且阻塞 claim。"""
    from sqlalchemy import func

    seed = _seed_job(status=JobStatus.RUNNING.value)
    try:
        past = datetime.now(timezone.utc) - timedelta(seconds=3600)
        db = SessionLocal()
        try:
            lease = DeviceLease(
                device_id=seed["device_id"], job_id=seed["job_id"],
                host_id=seed["host_id"],
                lease_type=LeaseType.JOB.value, status=LeaseStatus.ACTIVE.value,
                fencing_token=f"{seed['device_id']}:1", lease_generation=1,
                agent_instance_id=seed["host_id"],
                acquired_at=past - timedelta(seconds=7200),
                renewed_at=past, expires_at=past,
            )
            db.add(lease)
            dev = db.get(Device, seed["device_id"])
            dev.status = "BUSY"
            db.commit()
        finally:
            db.close()

        # 1. Heartbeat view: expired ACTIVE lease is counted
        async with AsyncSessionLocal() as async_db:
            active_count = (await async_db.execute(
                select(func.count()).select_from(DeviceLease).where(
                    DeviceLease.host_id == seed["host_id"],
                    DeviceLease.lease_type == LeaseType.JOB.value,
                    DeviceLease.status == LeaseStatus.ACTIVE.value,
                )
            )).scalar_one()
            assert active_count == 1, f"Expired ACTIVE lease must be counted; got {active_count}"

        # 2. Claim: blocking lease prevents claim
        async with AsyncSessionLocal() as async_db:
            claimed, tokens = await _claim_jobs_for_host(async_db, seed["host_id"], capacity=10)
            assert len(claimed) == 0, (
                f"Expired ACTIVE lease must block claim; got {len(claimed)} claimed"
            )
            await async_db.rollback()
    finally:
        _cleanup_seed(seed)




@pytest.mark.asyncio(loop_scope="module")
async def test_recovery_sync_unknown_within_grace_resumes():
    """Phase 4b D4: UNKNOWN job + ACTIVE lease + same boot + within grace → RESUME + UNKNOWN→RUNNING。"""
    suffix = uuid4().hex[:8]
    host_id = f"rec-uwg-{suffix}"
    instance_id = uuid4().hex
    boot_id = uuid4().hex
    _seed_recovery_host(host_id, boot_id=boot_id, instance_id=instance_id)

    seed = _seed_job(status=JobStatus.UNKNOWN.value)
    try:
        now = datetime.now(timezone.utc)
        actual_serial = ""
        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            job.ended_at = now - timedelta(seconds=60)  # within grace
            job.host_id = host_id
            db.commit()

            device = db.get(Device, seed["device_id"])
            device.host_id = host_id
            device.status = "BUSY"
            actual_serial = device.serial
            db.commit()

            lease = DeviceLease(
                device_id=seed["device_id"], job_id=seed["job_id"],
                host_id=host_id,
                lease_type=LeaseType.JOB.value, status=LeaseStatus.ACTIVE.value,
                fencing_token=f"{seed['device_id']}:1", lease_generation=1,
                agent_instance_id=instance_id,
                acquired_at=now - timedelta(seconds=3600),
                renewed_at=now - timedelta(seconds=3600),
                expires_at=now - timedelta(seconds=1800),  # expired
            )
            db.add(lease)
            db.commit()
        finally:
            db.close()

        payload = _RecoverySyncIn(
            host_id=host_id,
            agent_instance_id=instance_id,
            boot_id=boot_id,
            active_jobs=[_ActiveJobEntry(
                job_id=seed["job_id"],
                device_id=seed["device_id"],
                fencing_token=f"{seed['device_id']}:1",
            )],
        )

        async with AsyncSessionLocal() as async_db:
            result = await recovery_sync(payload, db=async_db, _=None)
            await async_db.commit()

        assert result.error is None
        actions = result.data["actions"]
        assert len(actions) == 1
        assert actions[0]["action"] == "RESUME"
        assert actions[0]["reason"] == "recovery_resume_unknown"
        assert actions[0]["device_serial"] == actual_serial

        db2 = SessionLocal()
        try:
            j = db2.get(JobInstance, seed["job_id"])
            assert j.status == JobStatus.RUNNING.value
            l = (db2.query(DeviceLease)
                 .filter(DeviceLease.device_id == seed["device_id"],
                         DeviceLease.job_id == seed["job_id"])
                 .first())
            assert l is not None and l.expires_at > now
        finally:
            db2.close()
    finally:
        _cleanup_seed(seed)
        _cleanup_recovery_host(host_id)


@pytest.mark.asyncio(loop_scope="module")
async def test_recovery_sync_unknown_grace_expired_cleanup():
    """Phase 4b D4 reverse: UNKNOWN job + grace expired → CLEANUP。"""
    suffix = uuid4().hex[:8]
    host_id = f"rec-uge-{suffix}"
    instance_id = uuid4().hex
    boot_id = uuid4().hex
    _seed_recovery_host(host_id, boot_id=boot_id, instance_id=instance_id)

    seed = _seed_job(status=JobStatus.UNKNOWN.value)
    try:
        now = datetime.now(timezone.utc)
        past_grace = now - timedelta(seconds=600)  # past grace
        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            job.ended_at = past_grace
            job.host_id = host_id
            db.commit()

            device = db.get(Device, seed["device_id"])
            device.host_id = host_id
            device.status = "BUSY"
            db.commit()

            lease = DeviceLease(
                device_id=seed["device_id"], job_id=seed["job_id"],
                host_id=host_id,
                lease_type=LeaseType.JOB.value, status=LeaseStatus.ACTIVE.value,
                fencing_token=f"{seed['device_id']}:1", lease_generation=1,
                agent_instance_id=instance_id,
                acquired_at=now - timedelta(seconds=7200),
                renewed_at=now - timedelta(seconds=3600),
                expires_at=now - timedelta(seconds=1800),  # expired
            )
            db.add(lease)
            db.commit()
        finally:
            db.close()

        payload = _RecoverySyncIn(
            host_id=host_id,
            agent_instance_id=instance_id,
            boot_id=boot_id,
            active_jobs=[_ActiveJobEntry(
                job_id=seed["job_id"],
                device_id=seed["device_id"],
                fencing_token=f"{seed['device_id']}:1",
            )],
        )

        async with AsyncSessionLocal() as async_db:
            result = await recovery_sync(payload, db=async_db, _=None)
            await async_db.commit()

        assert result.error is None
        actions = result.data["actions"]
        assert len(actions) == 1
        assert actions[0]["action"] == "CLEANUP"
        assert actions[0]["reason"] == "unknown_grace_expired"

        db2 = SessionLocal()
        try:
            l = (db2.query(DeviceLease)
                 .filter(DeviceLease.device_id == seed["device_id"],
                         DeviceLease.job_id == seed["job_id"])
                 .first())
            assert l is not None and l.status == LeaseStatus.RELEASED.value
        finally:
            db2.close()
    finally:
        _cleanup_seed(seed)
        _cleanup_recovery_host(host_id)
