"""#3723 U2 / ADR-0061 D3.8：EXECUTING_DISARM 宽限续期。

反例（修复前红）：
- EXECUTING_DISARM + 进度新鲜仍被 abort reaper 回收
- 超上限或进度中断时仍不回收
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.scheduler.device_lease_reconciler import (
    _DISARM_SCRIPT_NAMES,
    _disarm_grace_defers_reap,
    _disarm_step_timeout_seconds,
)


def _job(**kwargs):
    now = datetime.now(timezone.utc)
    base = dict(
        execution_state="EXECUTING_DISARM",
        last_progress_at=now - timedelta(seconds=10),
        pipeline_def={
            "lifecycle": {
                "teardown": [
                    {
                        "step_id": "pc_finish",
                        "action": "script:powercycle_finish",
                        "timeout_seconds": 600,
                    }
                ]
            }
        },
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_disarm_script_names_cover_families():
    assert "powercycle_finish" in _DISARM_SCRIPT_NAMES
    assert "powercycle_disarm" in _DISARM_SCRIPT_NAMES
    assert "sleep_finish" in _DISARM_SCRIPT_NAMES


def test_disarm_timeout_from_pipeline():
    assert _disarm_step_timeout_seconds(_job()) == 600
    assert _disarm_step_timeout_seconds(_job(pipeline_def={"lifecycle": {"teardown": []}})) is None


def test_defers_when_executing_disarm_and_progress_fresh():
    now = datetime.now(timezone.utc)
    abort_at = now - timedelta(seconds=90)  # past normal 60s grace
    job = _job(last_progress_at=now - timedelta(seconds=5))
    assert _disarm_grace_defers_reap(job, abort_at, now) is True


def test_reaps_when_past_abort_plus_timeout():
    now = datetime.now(timezone.utc)
    abort_at = now - timedelta(seconds=700)  # > 600s timeout
    job = _job(last_progress_at=now - timedelta(seconds=5))
    assert _disarm_grace_defers_reap(job, abort_at, now) is False


def test_reaps_when_progress_stale():
    now = datetime.now(timezone.utc)
    abort_at = now - timedelta(seconds=90)
    # 两个协调心跳周期默认 60s；90s 视为中断
    job = _job(last_progress_at=now - timedelta(seconds=90))
    assert _disarm_grace_defers_reap(job, abort_at, now) is False


def test_reaps_when_not_executing_disarm():
    now = datetime.now(timezone.utc)
    abort_at = now - timedelta(seconds=90)
    job = _job(execution_state="EXECUTING_STEP", last_progress_at=now - timedelta(seconds=5))
    assert _disarm_grace_defers_reap(job, abort_at, now) is False


def test_reaps_when_no_progress():
    now = datetime.now(timezone.utc)
    abort_at = now - timedelta(seconds=90)
    job = _job(last_progress_at=None)
    assert _disarm_grace_defers_reap(job, abort_at, now) is False


# ── PG 集成：EXECUTING_DISARM 新鲜进度不回收；超上限回收 ──────────────────────

import os

pytestmark_pg = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="abort reaper SQL uses PG-native JSONB ::timestamptz cast",
)


@pytestmark_pg
@pytest.mark.asyncio(loop_scope="module")
async def test_reaper_defers_executing_disarm_with_fresh_progress():
    from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
    from backend.models.enums import HostStatus, JobStatus
    from backend.models.host import Device, Host
    from backend.models.job import JobInstance
    from backend.models.plan import Plan, PlanStep
    from backend.models.plan_run import PlanRun
    from backend.scheduler.device_lease_reconciler import _reconcile_aborted_running_jobs

    suffix = uuid4().hex[:8]
    host_id = f"ar-disarm-{suffix}"
    device_id = int(suffix[:8], 16) % 10_000_000
    now = datetime.now(timezone.utc)
    abort_at = now - timedelta(seconds=90)

    db = SessionLocal()
    try:
        host = Host(id=host_id, hostname=f"h-{host_id}", status=HostStatus.ONLINE.value, created_at=now)
        device = Device(
            id=device_id, serial=f"ARD-{device_id}", host_id=host_id,
            status="BUSY", tags=[], created_at=now, adb_connected=True, adb_state="device",
        )
        plan = Plan(name=f"ar-disarm-{device_id}", description="u2", created_by="pytest")
        db.add_all([host, device, plan])
        db.flush()
        db.add(PlanStep(
            plan_id=plan.id, step_key="fin", script_name="powercycle_finish",
            script_version="1.0.4", stage="teardown", sort_order=0, timeout_seconds=600,
        ))
        db.flush()
        run = PlanRun(
            plan_id=plan.id, status="RUNNING", triggered_by="pytest",
            plan_snapshot={"name": plan.name}, run_type="MANUAL", started_at=now,
            run_context={"abort_requested": {"at": abort_at.isoformat(), "reason": "test"}},
        )
        db.add(run)
        db.flush()
        job = JobInstance(
            plan_run_id=run.id, plan_id=plan.id, device_id=device.id, host_id=host_id,
            status=JobStatus.RUNNING.value,
            execution_state="EXECUTING_DISARM",
            last_progress_at=now - timedelta(seconds=5),
            pipeline_def={
                "lifecycle": {
                    "init": [],
                    "teardown": [{
                        "step_id": "fin",
                        "action": "script:powercycle_finish",
                        "timeout_seconds": 600,
                    }],
                }
            },
            created_at=now, updated_at=now, started_at=now,
        )
        db.add(job)
        db.flush()
        job_id = job.id
        db.commit()
    finally:
        db.close()

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as adb:
            count, _ = await _reconcile_aborted_running_jobs(adb)
            await adb.commit()
            assert count == 0
            row = await adb.get(JobInstance, job_id)
            assert row.status == JobStatus.RUNNING.value
    finally:
        db = SessionLocal()
        try:
            from backend.models.job import JobArtifact, StepTrace
            from backend.models.device_lease import DeviceLease
            from backend.models.resource_pool import ResourceAllocation
            db.execute(StepTrace.__table__.delete())
            db.execute(JobArtifact.__table__.delete())
            db.execute(DeviceLease.__table__.delete())
            db.execute(ResourceAllocation.__table__.delete())
            db.execute(JobInstance.__table__.delete())
            db.execute(PlanStep.__table__.delete())
            db.execute(PlanRun.__table__.delete())
            db.execute(Plan.__table__.delete())
            db.execute(Device.__table__.delete().where(Device.host_id == host_id))
            db.execute(Host.__table__.delete().where(Host.id == host_id))
            db.commit()
        finally:
            db.close()


@pytestmark_pg
@pytest.mark.asyncio(loop_scope="module")
async def test_reaper_reaps_executing_disarm_past_timeout_ceiling():
    from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
    from backend.models.enums import HostStatus, JobStatus
    from backend.models.host import Device, Host
    from backend.models.job import JobInstance
    from backend.models.plan import Plan, PlanStep
    from backend.models.plan_run import PlanRun
    from backend.scheduler.device_lease_reconciler import _reconcile_aborted_running_jobs

    suffix = uuid4().hex[:8]
    host_id = f"ar-disarm2-{suffix}"
    device_id = int(suffix[:8], 16) % 10_000_000
    now = datetime.now(timezone.utc)
    # abort 700s ago；timeout=600 → 已超上限
    abort_at = now - timedelta(seconds=700)

    db = SessionLocal()
    try:
        host = Host(id=host_id, hostname=f"h-{host_id}", status=HostStatus.ONLINE.value, created_at=now)
        device = Device(
            id=device_id, serial=f"ARD2-{device_id}", host_id=host_id,
            status="BUSY", tags=[], created_at=now, adb_connected=True, adb_state="device",
        )
        plan = Plan(name=f"ar-disarm2-{device_id}", description="u2", created_by="pytest")
        db.add_all([host, device, plan])
        db.flush()
        db.add(PlanStep(
            plan_id=plan.id, step_key="fin", script_name="powercycle_finish",
            script_version="1.0.4", stage="teardown", sort_order=0, timeout_seconds=600,
        ))
        db.flush()
        run = PlanRun(
            plan_id=plan.id, status="RUNNING", triggered_by="pytest",
            plan_snapshot={"name": plan.name}, run_type="MANUAL", started_at=now,
            run_context={"abort_requested": {"at": abort_at.isoformat(), "reason": "test"}},
        )
        db.add(run)
        db.flush()
        job = JobInstance(
            plan_run_id=run.id, plan_id=plan.id, device_id=device.id, host_id=host_id,
            status=JobStatus.RUNNING.value,
            execution_state="EXECUTING_DISARM",
            last_progress_at=now - timedelta(seconds=5),
            pipeline_def={
                "lifecycle": {
                    "init": [],
                    "teardown": [{
                        "step_id": "fin",
                        "action": "script:powercycle_finish",
                        "timeout_seconds": 600,
                    }],
                }
            },
            created_at=now, updated_at=now, started_at=now,
        )
        db.add(job)
        db.flush()
        job_id = job.id
        db.commit()
    finally:
        db.close()

    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as adb:
            count, items = await _reconcile_aborted_running_jobs(adb)
            await adb.commit()
            assert count == 1
            row = await adb.get(JobInstance, job_id)
            assert row.status == JobStatus.UNKNOWN.value
    finally:
        db = SessionLocal()
        try:
            from backend.models.job import JobArtifact, StepTrace
            from backend.models.device_lease import DeviceLease
            from backend.models.resource_pool import ResourceAllocation
            db.execute(StepTrace.__table__.delete())
            db.execute(JobArtifact.__table__.delete())
            db.execute(DeviceLease.__table__.delete())
            db.execute(ResourceAllocation.__table__.delete())
            db.execute(JobInstance.__table__.delete())
            db.execute(PlanStep.__table__.delete())
            db.execute(PlanRun.__table__.delete())
            db.execute(Plan.__table__.delete())
            db.execute(Device.__table__.delete().where(Device.host_id == host_id))
            db.execute(Host.__table__.delete().where(Host.id == host_id))
            db.commit()
        finally:
            db.close()
