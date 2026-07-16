"""Tests for Agent Jobs API routes."""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.api.routes.agent_api import (
    _ExtendLockIn,
    _ExtendBatchIn,
    _ExtendBatchItemIn,
    _JobHeartbeatIn,
    _RunCompleteIn,
    _StepStatusIn,
    JobStatusUpdate,
    complete_job,
    extend_job_lock,
    extend_leases_batch,
    get_pending_jobs,
    job_heartbeat,
    update_job_status,
    update_job_step_status,
)
from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType, PlanRunStatus
from backend.models.device_lease import DeviceLease
from backend.models.host import Device, Host
from backend.models.job import JobInstance, StepTrace
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun


PIPELINE_DEF = {
    "lifecycle": {
        "init": [
            {
                "step_id": "check_device",
                "action": "script:check_device",
                "version": "1.0.0",
                "params": {},
                "timeout_seconds": 30,
                "retry": 0,
            }
        ],
        "teardown": [],
    }
}


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _seed_job(status: str = JobStatus.PENDING.value) -> dict:
    suffix = uuid4().hex[:8]
    host_id = f"agent-host-{suffix}"
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
            serial=f"SERIAL-{suffix}",
            host_id=host_id,
            status="ONLINE",
            adb_connected=True,
            adb_state="device",
            tags=[],
            created_at=now,
        )
        plan = Plan(
            name=f"plan-{suffix}",
            description="pytest plan",
            failure_threshold=0.1,
                        created_by="pytest",
        )
        db.add_all([host, device, plan])
        db.flush()

        plan_run = PlanRun(
            plan_id=plan.id,
            status="RUNNING",
            failure_threshold=0.1,
            plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL",
            triggered_by="pytest",
        )
        db.add(plan_run)
        db.flush()

        job = JobInstance(
            plan_run_id=plan_run.id,
            plan_id=plan.id,
            device_id=device.id,
            host_id=host_id,
            status=status,
            pipeline_def=PIPELINE_DEF,
            created_at=now,
            updated_at=now,
            started_at=now if status == JobStatus.RUNNING.value else None,
        )
        db.add(job)
        db.commit()

        return {
            "host_id": host_id,
            "device_id": device.id,
            "device_serial": device.serial,
            "plan_id": plan.id,
            "plan_run_id": plan_run.id,
            "job_id": job.id,
        }
    finally:
        db.close()


def _seed_host_only() -> dict:
    suffix = uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    host_id = f"agent-empty-{suffix}"
    db = SessionLocal()
    try:
        host = Host(
            id=host_id,
            hostname=f"empty-{suffix}",
            status=HostStatus.ONLINE.value,
            created_at=now,
        )
        db.add(host)
        db.commit()
        return {"host_id": host_id}
    finally:
        db.close()


def _setup_lease(seed: dict) -> str:
    """Create an ACTIVE DeviceLease for Phase 2b fencing_token validation.
    Also projects to device table (Phase 2c: projection is required).

    Returns the fencing_token that callers should pass to handlers.
    """
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=600)
    token = f"{seed['device_id']}:1"

    db = SessionLocal()
    try:
        lease = DeviceLease(
            device_id=seed["device_id"],
            job_id=seed["job_id"],
            host_id=seed["host_id"],
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=token,
            lease_generation=1,
            agent_instance_id=seed["host_id"],
            acquired_at=now,
            renewed_at=now,
            expires_at=expires,
        )
        db.add(lease)
        # Phase 2c: project to device table (release_lease/extend_lease require it)
        dev = db.query(Device).filter(Device.id == seed["device_id"]).first()
        if dev is not None:
            dev.status = "BUSY"
        db.commit()
        return token
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


@pytest.mark.asyncio
async def test_get_pending_jobs_legacy_endpoint_removed():
    seed = _seed_job(status=JobStatus.PENDING.value)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as exc_info:
                await get_pending_jobs(
                    host_id=seed["host_id"], limit=5, db=async_db, _=None,
                )
        assert exc_info.value.status_code == 410
        assert exc_info.value.detail["code"] == "LEGACY_CLAIM_ENDPOINT_REMOVED"
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_get_pending_jobs_legacy_endpoint_removed_for_empty_host():
    seed = _seed_host_only()
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as exc_info:
                await get_pending_jobs(
                    host_id=seed["host_id"], limit=10, db=async_db, _=None,
                )
        assert exc_info.value.status_code == 410
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_job_heartbeat_cannot_claim_pending_job():
    seed = _seed_job(status=JobStatus.PENDING.value)
    token = _setup_lease(seed)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as exc_info:
                await job_heartbeat(
                    job_id=seed["job_id"],
                    payload=_JobHeartbeatIn(
                        status="RUNNING", fencing_token=token,
                    ),
                    db=async_db,
                    _=None,
                )
        assert exc_info.value.status_code == 409

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.status == JobStatus.PENDING.value
            assert job.started_at is None
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_update_job_status_repeated_running_is_heartbeat_noop():
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await update_job_status(
                job_id=seed["job_id"],
                payload=JobStatusUpdate(
                    status="RUNNING", fencing_token=token,
                ),
                db=async_db,
                _=None,
            )
        assert result.data["status"] == JobStatus.RUNNING.value
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_job_heartbeat_refreshes_liveness_for_running_job(engine):
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    old_liveness = datetime.now(timezone.utc) - timedelta(hours=1)
    try:
        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            job.updated_at = old_liveness
            db.commit()
        finally:
            db.close()

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await job_heartbeat(
                job_id=seed["job_id"],
                payload=_JobHeartbeatIn(status="RUNNING", fencing_token=token),
                db=async_db,
                _=None,
            )
        assert result.error is None
        assert result.data["status"] == JobStatus.RUNNING.value

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert _as_utc(job.updated_at) > old_liveness
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_complete_job_maps_finished_to_completed():
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await complete_job(
                job_id=seed["job_id"],
                payload=_RunCompleteIn(update={"status": "FINISHED", "exit_code": 0}, fencing_token=token),
                db=async_db,
                _=None,
            )
        assert result.error is None
        assert result.data["status"] == JobStatus.COMPLETED.value

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            plan_run = db.get(PlanRun, seed["plan_run_id"])
            assert job is not None
            assert plan_run is not None
            assert job.status == JobStatus.COMPLETED.value
            assert job.ended_at is not None
            assert plan_run.status == "SUCCESS"
            assert plan_run.ended_at is not None
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_complete_job_persists_run_complete_snapshot():
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await complete_job(
                job_id=seed["job_id"],
                payload=_RunCompleteIn(
                    update={
                        "status": "FINISHED",
                        "exit_code": 0,
                        "log_summary": "risk=LOW;restarts=1",
                    },
                    artifact={
                        "storage_uri": "file:///tmp/report.tar.gz",
                        "size_bytes": 1024,
                        "checksum": "abc123",
                    },
                    fencing_token=token,
                ),
                db=async_db,
                _=None,
            )
        assert result.error is None
        assert result.data["status"] == JobStatus.COMPLETED.value

        db = SessionLocal()
        try:
            snapshot = (
                db.query(StepTrace)
                .filter(
                    StepTrace.job_id == seed["job_id"],
                    StepTrace.step_id == "__job__",
                    StepTrace.event_type == "RUN_COMPLETE",
                )
                .first()
            )
            assert snapshot is not None
            payload = json.loads(snapshot.output)
            assert payload["update"]["log_summary"] == "risk=LOW;restarts=1"
            assert payload["artifact"]["storage_uri"] == "file:///tmp/report.tar.gz"
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_complete_job_persists_error_message_to_status_reason():
    """ADR-0021: Agent /complete 通过 update.error_message 上送的失败原因
    必须落到 JobInstance.status_reason —— 这是前端 device matrix 抽屉/tooltip
    展示 "状态原因" 的数据源。

    端到端串联：pipeline_engine 生成 lifecycle_error → job_runner 写入
    complete_payload['error_message'] → api_client._build_complete_payload 包成
    {update: {error_message}} → agent_api.complete_job → JobStateMachine.transition
    → state_machine.py: job.status_reason = reason。
    """
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    reason = "lifecycle init failed: monkey_resource_push timeout"
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await complete_job(
                job_id=seed["job_id"],
                payload=_RunCompleteIn(
                    update={
                        "status": "FAILED",
                        "exit_code": 1,
                        "error_code": "PIPELINE_ERROR",
                        "error_message": reason,
                    },
                    fencing_token=token,
                ),
                db=async_db,
                _=None,
            )
        assert result.error is None
        assert result.data["status"] == JobStatus.FAILED.value

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.status == JobStatus.FAILED.value
            assert job.status_reason == reason, (
                f"Expected status_reason={reason!r}, got {job.status_reason!r} — "
                "Agent 失败原因没有透传到 DB，前端展示不出来"
            )
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_extend_lock_success(engine):
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    old_liveness = datetime.now(timezone.utc) - timedelta(hours=1)
    try:
        db = SessionLocal()
        try:
            device = db.get(Device, seed["device_id"])
            assert device is not None
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            job.updated_at = old_liveness
            db.commit()
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

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert _as_utc(job.updated_at) > old_liveness
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_extend_lock_conflict():
    """Phase 2c: extend_job_lock returns 409 when no ACTIVE lease exists."""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    try:
        # Release the lease so extend_lease returns False → 409
        db = SessionLocal()
        try:
            db.query(DeviceLease).filter(
                DeviceLease.device_id == seed["device_id"],
                DeviceLease.job_id == seed["job_id"],
            ).update({"status": LeaseStatus.RELEASED.value})
            db.commit()
        finally:
            db.close()

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as exc_info:
                await extend_job_lock(
                    job_id=seed["job_id"],
                    payload=_ExtendLockIn(fencing_token=token),
                    db=async_db, _=None,
                )
        assert exc_info.value.status_code == 409
    finally:
        _cleanup_seed(seed)


# ── P0: Host-level batch lease renewal ─────────────────────────────────────────


def _extract_batch(result):
    """Return {job_id: status} from an extend_leases_batch ApiResponse."""
    return {item.job_id: item.status for item in result.data.results}


@pytest.mark.asyncio
async def test_extend_batch_renews_running_job():
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    old_liveness = datetime.now(timezone.utc) - timedelta(hours=1)
    try:
        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            job.updated_at = old_liveness
            db.commit()
        finally:
            db.close()

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await extend_leases_batch(
                payload=_ExtendBatchIn(
                    host_id=seed["host_id"],
                    agent_instance_id=seed["host_id"],
                    leases=[_ExtendBatchItemIn(job_id=seed["job_id"], fencing_token=token)],
                ),
                db=async_db, _=None,
            )
        assert result.error is None
        results = {item.job_id: item for item in result.data.results}
        assert results[seed["job_id"]].status == "renewed"
        assert results[seed["job_id"]].expires_at

        # RUNNING keepalive: job.updated_at bumped (recycler heartbeat guard)
        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert _as_utc(job.updated_at) > old_liveness
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_extend_batch_classifies_each_item():
    """One request, three jobs, three distinct per-item outcomes — no item
    failure aborts the others (invariant: results are isolated)."""
    running = _seed_job(status=JobStatus.RUNNING.value)
    running_token = _setup_lease(running)
    stale = _seed_job(status=JobStatus.RUNNING.value)
    _setup_lease(stale)
    not_running = _seed_job(status=JobStatus.PENDING.value)  # no lease, PENDING
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await extend_leases_batch(
                payload=_ExtendBatchIn(
                    host_id=running["host_id"],
                    leases=[
                        _ExtendBatchItemIn(job_id=running["job_id"], fencing_token=running_token),
                        _ExtendBatchItemIn(job_id=stale["job_id"], fencing_token="9:99"),
                        _ExtendBatchItemIn(job_id=not_running["job_id"], fencing_token="x"),
                    ],
                ),
                db=async_db, _=None,
            )
        statuses = _extract_batch(result)
        assert statuses[running["job_id"]] == "renewed"
        assert statuses[stale["job_id"]] == "stale_token"
        assert statuses[not_running["job_id"]] == "job_not_running"
    finally:
        _cleanup_seed(running)
        _cleanup_seed(stale)
        _cleanup_seed(not_running)


@pytest.mark.asyncio
async def test_extend_batch_lease_missing_when_released():
    """RUNNING job whose ACTIVE lease was released → lease_missing (not renewed)."""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    try:
        db = SessionLocal()
        try:
            db.query(DeviceLease).filter(
                DeviceLease.job_id == seed["job_id"],
            ).update({"status": LeaseStatus.RELEASED.value})
            db.commit()
        finally:
            db.close()

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await extend_leases_batch(
                payload=_ExtendBatchIn(
                    host_id=seed["host_id"],
                    leases=[_ExtendBatchItemIn(job_id=seed["job_id"], fencing_token=token)],
                ),
                db=async_db, _=None,
            )
        assert _extract_batch(result)[seed["job_id"]] == "lease_missing"
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_extend_batch_empty_returns_empty():
    await async_engine.dispose()
    async with AsyncSessionLocal() as async_db:
        result = await extend_leases_batch(
            payload=_ExtendBatchIn(host_id="h-none", leases=[]),
            db=async_db, _=None,
        )
    assert result.error is None
    assert result.data.results == []


@pytest.mark.asyncio
async def test_extend_batch_rejects_oversized_batch(monkeypatch):
    import backend.api.routes.agent_api as agent_api
    monkeypatch.setattr(agent_api, "_LEASE_EXTEND_BATCH_MAX", 2)
    await async_engine.dispose()
    async with AsyncSessionLocal() as async_db:
        with pytest.raises(HTTPException) as exc_info:
            await extend_leases_batch(
                payload=_ExtendBatchIn(
                    host_id="h-1",
                    leases=[
                        _ExtendBatchItemIn(job_id=i, fencing_token="t") for i in range(3)
                    ],
                ),
                db=async_db, _=None,
            )
    assert exc_info.value.status_code == 413


@pytest.mark.asyncio
async def test_extend_batch_host_mismatch_is_fenced():
    """Ownership CAS: a valid token presented by the WRONG host must not renew."""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await extend_leases_batch(
                payload=_ExtendBatchIn(
                    host_id="some-other-host",
                    leases=[_ExtendBatchItemIn(job_id=seed["job_id"], fencing_token=token)],
                ),
                db=async_db, _=None,
            )
        assert _extract_batch(result)[seed["job_id"]] == "stale_token"
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_extend_batch_agent_instance_mismatch_is_fenced():
    """Ownership CAS: right host, right token, but a different agent instance
    (e.g. a zombie process from before a takeover) must not renew."""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)  # lease.agent_instance_id == seed["host_id"]
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await extend_leases_batch(
                payload=_ExtendBatchIn(
                    host_id=seed["host_id"],
                    agent_instance_id="zombie-instance-uuid",
                    leases=[_ExtendBatchItemIn(job_id=seed["job_id"], fencing_token=token)],
                ),
                db=async_db, _=None,
            )
        assert _extract_batch(result)[seed["job_id"]] == "stale_token"
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_extend_batch_empty_instance_id_still_renews():
    """Back-compat: an Agent that reports no agent_instance_id skips the
    instance binding (host + token still enforced) and renews normally."""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await extend_leases_batch(
                payload=_ExtendBatchIn(
                    host_id=seed["host_id"],
                    agent_instance_id="",
                    leases=[_ExtendBatchItemIn(job_id=seed["job_id"], fencing_token=token)],
                ),
                db=async_db, _=None,
            )
        assert _extract_batch(result)[seed["job_id"]] == "renewed"
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_extend_batch_swapped_tokens_renew_nothing():
    """Pair binding: two jobs on the same host with each other's (individually
    valid) tokens must BOTH be fenced. Separate job_id/token IN-lists would
    wrongly renew both — this asserts (job_id, fencing_token) is matched as a
    tuple, mirroring the DB-level CAS."""
    seed_a = _seed_job(status=JobStatus.RUNNING.value)
    token_a = _setup_lease(seed_a)
    seed_b = _seed_job(status=JobStatus.RUNNING.value)
    token_b = _setup_lease(seed_b)
    try:
        db = SessionLocal()
        try:
            # Put both leases under one host so host binding passes and only
            # the pair binding can reject.
            db.query(DeviceLease).filter(
                DeviceLease.job_id.in_([seed_a["job_id"], seed_b["job_id"]]),
            ).update(
                {"host_id": seed_a["host_id"], "agent_instance_id": seed_a["host_id"]},
                synchronize_session=False,
            )
            db.commit()
        finally:
            db.close()

        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await extend_leases_batch(
                payload=_ExtendBatchIn(
                    host_id=seed_a["host_id"],
                    leases=[
                        _ExtendBatchItemIn(job_id=seed_a["job_id"], fencing_token=token_b),
                        _ExtendBatchItemIn(job_id=seed_b["job_id"], fencing_token=token_a),
                    ],
                ),
                db=async_db, _=None,
            )
        statuses = _extract_batch(result)
        assert statuses[seed_a["job_id"]] == "stale_token"
        assert statuses[seed_b["job_id"]] == "stale_token"
    finally:
        # seed_b's lease was re-pointed at seed_a's host — clean it first so
        # deleting host A doesn't hit the device_leases FK.
        _cleanup_seed(seed_b)
        _cleanup_seed(seed_a)


@pytest.mark.asyncio
async def test_extend_batch_duplicate_job_id_dedupes_last_token_wins():
    """Duplicate job_ids collapse to one result; the LAST occurrence's token is
    the one evaluated (documented contract, not an error)."""
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await extend_leases_batch(
                payload=_ExtendBatchIn(
                    host_id=seed["host_id"],
                    leases=[
                        _ExtendBatchItemIn(job_id=seed["job_id"], fencing_token="0:999"),
                        _ExtendBatchItemIn(job_id=seed["job_id"], fencing_token=token),
                    ],
                ),
                db=async_db, _=None,
            )
        # One result entry, evaluated with the last (valid) token → renewed.
        assert len(result.data.results) == 1
        assert _extract_batch(result)[seed["job_id"]] == "renewed"
    finally:
        _cleanup_seed(seed)


# ── White-box CAS interleavings ────────────────────────────────────────────────
# The endpoint's prelim classification catches these in the sequential case;
# these tests drive _cas_renew_leases directly to prove the DB-level CAS also
# rejects them when they happen AFTER the prelim SELECT (the race window).


@pytest.mark.asyncio
async def test_cas_rejects_rotated_token():
    """Race: prelim validated the OLD token, then the lease token rotated
    (recovery takeover). The old holder's CAS must hit zero rows — it must not
    renew the NEW owner's lease."""
    from backend.api.routes.agent_api import _cas_renew_leases

    seed = _seed_job(status=JobStatus.RUNNING.value)
    old_token = _setup_lease(seed)
    try:
        # Simulate the takeover happening after prelim: rotate the token.
        db = SessionLocal()
        try:
            db.query(DeviceLease).filter(
                DeviceLease.job_id == seed["job_id"],
            ).update({"fencing_token": f"{seed['device_id']}:2"})
            db.commit()
        finally:
            db.close()

        now = datetime.now(timezone.utc)
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            renewed = await _cas_renew_leases(
                async_db,
                pairs=[(seed["job_id"], old_token)],  # stale snapshot token
                host_id=seed["host_id"],
                agent_instance_id=seed["host_id"],
                now=now,
                new_expires=now + timedelta(seconds=600),
            )
            await async_db.rollback()
        assert renewed == set()

        # And the new owner's lease TTL was left untouched.
        db = SessionLocal()
        try:
            lease = db.query(DeviceLease).filter(
                DeviceLease.job_id == seed["job_id"],
            ).one()
            assert _as_utc(lease.expires_at) < now + timedelta(seconds=600)
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_cas_rejects_concurrently_terminal_job():
    """Race: prelim saw RUNNING, then the job reached a terminal state before
    the UPDATE. The CAS joins on Job.status==RUNNING and must hit zero rows —
    a finished job must not get a fresh lease TTL / keepalive."""
    from backend.api.routes.agent_api import _cas_renew_leases

    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    try:
        # Simulate the job completing after prelim.
        db = SessionLocal()
        try:
            db.query(JobInstance).filter(
                JobInstance.id == seed["job_id"],
            ).update({"status": JobStatus.COMPLETED.value})
            db.commit()
        finally:
            db.close()

        now = datetime.now(timezone.utc)
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            renewed = await _cas_renew_leases(
                async_db,
                pairs=[(seed["job_id"], token)],  # token itself is still valid
                host_id=seed["host_id"],
                agent_instance_id=seed["host_id"],
                now=now,
                new_expires=now + timedelta(seconds=600),
            )
            await async_db.rollback()
        assert renewed == set()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_cas_rejects_wrong_host_and_instance_at_write_time():
    """The CAS enforces host/instance binding independently of prelim."""
    from backend.api.routes.agent_api import _cas_renew_leases

    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    try:
        now = datetime.now(timezone.utc)
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            wrong_host = await _cas_renew_leases(
                async_db,
                pairs=[(seed["job_id"], token)],
                host_id="not-this-host",
                agent_instance_id=seed["host_id"],
                now=now,
                new_expires=now + timedelta(seconds=600),
            )
            await async_db.rollback()
            wrong_instance = await _cas_renew_leases(
                async_db,
                pairs=[(seed["job_id"], token)],
                host_id=seed["host_id"],
                agent_instance_id="zombie-instance",
                now=now,
                new_expires=now + timedelta(seconds=600),
            )
            await async_db.rollback()
        assert wrong_host == set()
        assert wrong_instance == set()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_update_job_step_status_upserts_trace():
    seed = _seed_job(status=JobStatus.RUNNING.value)
    token = _setup_lease(seed)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await update_job_step_status(
                job_id=seed["job_id"],
                step_id="check_device",
                payload=_StepStatusIn(
                    status="RUNNING",
                    started_at="2026-03-01T10:00:00Z",
                    error_message=None,
                    fencing_token=token,
                ),
                db=async_db,
                _=None,
            )
        assert result.error is None
        assert result.data["job_id"] == seed["job_id"]
        assert result.data["step_id"] == "check_device"
        assert result.data["status"] == "RUNNING"

        db = SessionLocal()
        try:
            trace = (
                db.query(StepTrace)
                .filter(
                    StepTrace.job_id == seed["job_id"],
                    StepTrace.step_id == "check_device",
                    StepTrace.event_type == "status_update",
                )
                .first()
            )
            assert trace is not None
            assert trace.stage == "execute"
            assert trace.status == "RUNNING"
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)
