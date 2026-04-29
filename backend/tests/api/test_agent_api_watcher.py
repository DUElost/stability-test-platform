"""Agent API watcher 契约测试 —— 覆盖 C1 扩展的三处端点。

端点：
  1. POST /agent/jobs/claim           : 响应带 device_serial + watcher_policy（来自 WorkflowDefinition）
  2. POST /agent/jobs/{id}/complete   : 接受 watcher_summary，回填 JobInstance.watcher_*
  3. POST /agent/log-signals          : 幂等 upsert (job_id, seq_no)，累加 log_signal_count

契约来源：backend/agent/watcher/contracts.py

注意：
  ingest_log_signals 端点使用 PostgreSQL 方言 `pg_insert(...).on_conflict_do_nothing(...)`，
  且所有用例通过全局 SessionLocal/AsyncSessionLocal 读写，SQLite in-memory 下两个 engine
  各自独立 → 故本文件仅在 TEST_DATABASE_URL 指向 PostgreSQL 时运行。
  本地快测 ALLOW_SQLITE_TESTS=1 路径下自动 skip，不产生 red。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

# 项目约定：API 层契约测试仅在 PostgreSQL 下运行（pg_insert + 跨 engine seed 验证）。
# 命令：TEST_DATABASE_URL=postgresql+psycopg://... python -m pytest <this>
pytestmark = pytest.mark.skipif(
    os.getenv("DATABASE_URL", "").startswith("sqlite"),
    reason="API 契约测试需要 PostgreSQL（pg_insert + 跨 engine 种子数据）；"
           "SQLite quick-test 模式下自动跳过。设 TEST_DATABASE_URL=postgresql+... 运行",
)

from backend.api.routes.agent_api import (
    ClaimRequest,
    LogSignalBatchIn,
    LogSignalIn,
    _RunCompleteIn,
    claim_jobs,
    complete_job,
    ingest_log_signals,
)
from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType, WorkflowStatus
from backend.models.device_lease import DeviceLease
from backend.models.host import Device, Host
from backend.models.job import JobInstance, JobLogSignal, StepTrace, TaskTemplate
from backend.models.workflow import WorkflowDefinition, WorkflowRun


PIPELINE_DEF = {
    "stages": {
        "prepare": [],
        "execute": [
            {"step_id": "dummy", "action": "builtin:noop", "timeout_seconds": 1}
        ],
        "post_process": [],
    }
}

DEFAULT_WATCHER_POLICY = {
    "on_unavailable": "degraded",
    "required_categories": ["ANR", "AEE"],
    "nfs_quota_mb": 1024,
}


# ----------------------------------------------------------------------
# Seed & cleanup helpers
# ----------------------------------------------------------------------

def _seed_job_with_policy(
    *,
    job_status: str = JobStatus.PENDING.value,
    watcher_policy: dict | None = DEFAULT_WATCHER_POLICY,
) -> dict:
    """种子数据：1 host + 1 device + 1 workflow(+policy) + 1 task_template + 1 workflow_run + 1 job。"""
    suffix = uuid4().hex[:8]
    host_id = f"watcher-host-{suffix}"
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        host = Host(
            id=host_id,
            hostname=f"wh-{suffix}",
            status=HostStatus.ONLINE.value,
            created_at=now,
        )
        device = Device(
            serial=f"SN-{suffix}",
            host_id=host_id,
            status="ONLINE",
            tags=[],
            created_at=now,
        )
        wf = WorkflowDefinition(
            name=f"wf-{suffix}",
            description="watcher-contract",
            failure_threshold=0.1,
            created_by="pytest",
            watcher_policy=watcher_policy,
            created_at=now,
            updated_at=now,
        )
        db.add_all([host, device, wf])
        db.flush()

        tpl = TaskTemplate(
            workflow_definition_id=wf.id,
            name=f"tpl-{suffix}",
            pipeline_def=PIPELINE_DEF,
            sort_order=0,
            created_at=now,
        )
        db.add(tpl)
        db.flush()

        run = WorkflowRun(
            workflow_definition_id=wf.id,
            status=WorkflowStatus.RUNNING.value,
            failure_threshold=0.1,
            triggered_by="pytest",
            started_at=now,
        )
        db.add(run)
        db.flush()

        job = JobInstance(
            workflow_run_id=run.id,
            task_template_id=tpl.id,
            device_id=device.id,
            host_id=host_id,
            status=job_status,
            pipeline_def=PIPELINE_DEF,
            created_at=now,
            updated_at=now,
            started_at=now if job_status == JobStatus.RUNNING.value else None,
        )
        db.add(job)
        db.commit()

        return {
            "host_id": host_id,
            "device_id": device.id,
            "device_serial": device.serial,
            "workflow_definition_id": wf.id,
            "task_template_id": tpl.id,
            "workflow_run_id": run.id,
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
            db.query(JobLogSignal).filter(JobLogSignal.job_id == job_id).delete()
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


def _setup_watcher_lease(seed: dict) -> str:
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
            dev.lock_run_id = seed["job_id"]
            dev.lock_expires_at = expires
        db.commit()
        return token
    finally:
        db.close()


# ----------------------------------------------------------------------
# C1.1: claim 响应带 device_serial + watcher_policy
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_claim_returns_device_serial_and_watcher_policy():
    """claim 成功时 JobOut 必须包含 device_serial + watcher_policy（取自 WorkflowDefinition）。"""
    seed = _seed_job_with_policy(watcher_policy=DEFAULT_WATCHER_POLICY)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await claim_jobs(
                payload=ClaimRequest(host_id=seed["host_id"], capacity=5),
                db=async_db,
                _=None,
            )
        assert result.error is None
        assert len(result.data) == 1
        item = result.data[0]
        # 基础字段
        assert item.id == seed["job_id"]
        assert item.device_id == seed["device_id"]
        # 新增契约字段：device_serial
        assert item.device_serial == seed["device_serial"]
        # 新增契约字段：watcher_policy（来自 WorkflowDefinition）
        assert item.watcher_policy == DEFAULT_WATCHER_POLICY
        # Job 已被原子转为 RUNNING
        assert item.status == JobStatus.RUNNING.value
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_claim_returns_null_watcher_policy_when_workflow_has_none():
    """WorkflowDefinition.watcher_policy 为空时 claim 响应 watcher_policy=None（不中断业务）。"""
    seed = _seed_job_with_policy(watcher_policy=None)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await claim_jobs(
                payload=ClaimRequest(host_id=seed["host_id"], capacity=5),
                db=async_db,
                _=None,
            )
        assert result.error is None
        assert len(result.data) == 1
        item = result.data[0]
        assert item.device_serial == seed["device_serial"]
        assert item.watcher_policy is None
    finally:
        _cleanup_seed(seed)


# ----------------------------------------------------------------------
# C1.2: complete 接受 watcher_summary 并回填 watcher_* 列
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_with_watcher_summary_persists_fields():
    """Agent 上报 watcher_summary 时，JobInstance.watcher_* 列全部写入。"""
    seed = _seed_job_with_policy(job_status=JobStatus.RUNNING.value)
    summary = {
        "watcher_id": "wch-test-001",
        "watcher_started_at": "2026-04-18T10:00:00+00:00",
        "watcher_stopped_at": "2026-04-18T10:15:30+00:00",
        "watcher_capability": "stub",
        "log_signal_count": 5,
        "watcher_stats": {"events_total": 12, "signals_emitted": 5},
    }
    token = _setup_watcher_lease(seed)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await complete_job(
                job_id=seed["job_id"],
                payload=_RunCompleteIn(
                    update={"status": "FINISHED", "exit_code": 0},
                    watcher_summary=summary,
                    fencing_token=token,
                ),
                db=async_db,
                _=None,
            )
        assert result.error is None
        assert result.data["status"] == JobStatus.COMPLETED.value

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            # Watcher 生命周期字段已回填
            assert job.watcher_capability == "stub"
            assert job.watcher_started_at is not None
            assert job.watcher_stopped_at is not None
            # log_signal_count 取 Agent 权威值（若大于 DB 现值，作为下限同步）
            assert job.log_signal_count == 5
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_complete_without_watcher_summary_keeps_columns_null():
    """未启用 watcher 的旧 Agent 上报不带 summary → watcher_* 列保持 None。"""
    seed = _seed_job_with_policy(job_status=JobStatus.RUNNING.value)
    token = _setup_watcher_lease(seed)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await complete_job(
                job_id=seed["job_id"],
                payload=_RunCompleteIn(
                    update={"status": "FINISHED", "exit_code": 0},
                    # 不传 watcher_summary
                    fencing_token=token,
                ),
                db=async_db,
                _=None,
            )
        assert result.error is None

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.watcher_capability is None
            assert job.watcher_started_at is None
            assert job.watcher_stopped_at is None
            # log_signal_count 默认 0（server_default）
            assert (job.log_signal_count or 0) == 0
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


# ----------------------------------------------------------------------
# C1.3: POST /agent/log-signals —— 幂等 + 计数累加
# ----------------------------------------------------------------------

def _make_signal(job_id: int, device_serial: str, host_id: str, seq_no: int, **overrides) -> LogSignalIn:
    base = dict(
        job_id=job_id,
        seq_no=seq_no,
        host_id=host_id,
        device_serial=device_serial,
        category="ANR",
        source="polling",
        path_on_device=f"/data/anr/trace_{seq_no:02d}",
        detected_at="2026-04-18T10:05:00+00:00",
    )
    base.update(overrides)
    return LogSignalIn(**base)


@pytest.mark.asyncio
async def test_log_signals_inserts_unique_per_seq_no():
    """同 (job_id, seq_no) 重复上送只入库一次（ON CONFLICT DO NOTHING）。"""
    seed = _seed_job_with_policy(job_status=JobStatus.RUNNING.value)
    sig1 = _make_signal(seed["job_id"], seed["device_serial"], seed["host_id"], seq_no=1)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            r1 = await ingest_log_signals(
                payload=LogSignalBatchIn(signals=[sig1]),
                db=async_db,
                _=None,
            )
        assert r1.error is None
        assert r1.data == {"inserted": 1, "total": 1}

        # 再发一次同 seq_no（不同 path，但幂等键仅 job_id+seq_no）
        sig1_dup = _make_signal(
            seed["job_id"], seed["device_serial"], seed["host_id"], seq_no=1,
            path_on_device="/data/anr/should_be_ignored",
        )
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            r2 = await ingest_log_signals(
                payload=LogSignalBatchIn(signals=[sig1_dup]),
                db=async_db,
                _=None,
            )
        assert r2.error is None
        assert r2.data == {"inserted": 0, "total": 1}

        # DB 中仍只有一行
        db = SessionLocal()
        try:
            rows = db.query(JobLogSignal).filter(JobLogSignal.job_id == seed["job_id"]).all()
            assert len(rows) == 1
            # 第一条的 path 未被第二次覆盖
            assert rows[0].path_on_device == "/data/anr/trace_01"
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_log_signals_increments_log_signal_count():
    """批量 3 条不同 seq_no → job_instance.log_signal_count += 3。"""
    seed = _seed_job_with_policy(job_status=JobStatus.RUNNING.value)
    signals = [
        _make_signal(seed["job_id"], seed["device_serial"], seed["host_id"], seq_no=i, category="ANR")
        for i in range(1, 4)
    ]
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await ingest_log_signals(
                payload=LogSignalBatchIn(signals=signals),
                db=async_db,
                _=None,
            )
        assert result.error is None
        assert result.data == {"inserted": 3, "total": 3}

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.log_signal_count == 3
        finally:
            db.close()

        # 再发一个 seq_no=2（重复）+ 一个 seq_no=4（新）→ count 应 +1
        extra = [
            _make_signal(seed["job_id"], seed["device_serial"], seed["host_id"], seq_no=2),
            _make_signal(seed["job_id"], seed["device_serial"], seed["host_id"], seq_no=4),
        ]
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            r2 = await ingest_log_signals(
                payload=LogSignalBatchIn(signals=extra),
                db=async_db,
                _=None,
            )
        assert r2.data == {"inserted": 1, "total": 2}

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job.log_signal_count == 4
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_log_signals_contract_violation_returns_400():
    """非法 category → 契约校验直接 400，整批不入库。"""
    from fastapi import HTTPException

    seed = _seed_job_with_policy(job_status=JobStatus.RUNNING.value)
    bad = _make_signal(
        seed["job_id"], seed["device_serial"], seed["host_id"], seq_no=1,
        category="INVALID_CATEGORY",
    )
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as excinfo:
                await ingest_log_signals(
                    payload=LogSignalBatchIn(signals=[bad]),
                    db=async_db,
                    _=None,
                )
        assert excinfo.value.status_code == 400
        assert "contract violation" in str(excinfo.value.detail).lower()

        # 未入库 + count 未变
        db = SessionLocal()
        try:
            assert db.query(JobLogSignal).filter(JobLogSignal.job_id == seed["job_id"]).count() == 0
            job = db.get(JobInstance, seed["job_id"])
            assert (job.log_signal_count or 0) == 0
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)
