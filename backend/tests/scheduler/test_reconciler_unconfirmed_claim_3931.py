"""#3931：未确认 RUNNING 认领的快速回收测试（不等 600s lease + 300s UNKNOWN grace）。"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("DATABASE_URL", "").startswith("sqlite"),
    reason="Reconciler tests require PostgreSQL",
)

from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine  # noqa: E402
from backend.core.job_timeout_config import UNCONFIRMED_CLAIM_TIMEOUT_SECONDS  # noqa: E402
from backend.core.metrics import reconciler_actions  # noqa: E402
from backend.models.device_lease import DeviceLease  # noqa: E402
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType  # noqa: E402
from backend.models.host import Device, Host  # noqa: E402
from backend.models.job import JobInstance, StepTrace  # noqa: E402
from backend.models.plan import Plan  # noqa: E402
from backend.models.plan_run import PlanRun  # noqa: E402
from backend.scheduler.device_lease_reconciler import (  # noqa: E402
    _reconcile_checks,
    _reconcile_stale_unknown_jobs,
    _reconcile_unconfirmed_claims,
)
from backend.services.agent_claim import (  # noqa: E402
    _UNCONFIRMED_CLAIM_REDELIVER_GRACE_SECONDS,
    claim_jobs_for_host,
)


@pytest.fixture
def unconfirmed_seed():
    created = {"jobs": [], "devices": [], "runs": [], "plans": [], "hosts": []}
    suffix = uuid4().hex[:8]
    host_id = f"uc-host-{suffix}"
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    db.add(Host(id=host_id, hostname=f"h-{suffix}", status=HostStatus.ONLINE.value, created_at=now))
    plan = Plan(name=f"uc-{suffix}", description="unconfirmed claim test", created_by="pytest")
    db.add(plan)
    db.flush()
    created["hosts"].append(host_id)
    created["plans"].append(plan.id)

    def add(
        *,
        age_seconds: int,
        job_status: str = JobStatus.RUNNING.value,
        status_reason: str | None = None,
        renewed_offset: int = 0,
        with_hb: bool = False,
        exec_state: str | None = None,
        with_trace: bool = False,
        with_lease: bool = True,
    ):
        n = len(created["devices"])
        run = PlanRun(
            plan_id=plan.id, status="RUNNING",
            plan_snapshot={"plan_id": plan.id}, run_type="MANUAL", triggered_by="pytest",
            started_at=now, total_job_count=1,
        )
        db.add(run)
        db.flush()
        created["runs"].append(run.id)

        device = Device(
            serial=f"UC-{suffix}-{n}", host_id=host_id, tags=[], created_at=now,
            status="ONLINE", adb_connected=True, adb_state="device", lease_generation=1,
        )
        db.add(device)
        db.flush()
        created["devices"].append(device.id)

        started = now - timedelta(seconds=age_seconds)
        job = JobInstance(
            plan_run_id=run.id, plan_id=plan.id, device_id=device.id, host_id=host_id,
            status=job_status, status_reason=status_reason,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            created_at=started, updated_at=started, started_at=started,
            ended_at=now if job_status == JobStatus.UNKNOWN.value else None,
            execution_state=exec_state,
            last_execution_heartbeat_at=started if with_hb else None,
        )
        db.add(job)
        db.flush()
        created["jobs"].append(job.id)

        if with_lease:
            db.add(DeviceLease(
                device_id=device.id, job_id=job.id, host_id=host_id,
                lease_type=LeaseType.JOB.value, status=LeaseStatus.ACTIVE.value,
                fencing_token=f"{device.id}:1", lease_generation=1,
                agent_instance_id="inst-uc",
                acquired_at=started,
                renewed_at=started + timedelta(seconds=renewed_offset),
                expires_at=now + timedelta(seconds=500),  # lease NOT yet expired
            ))
        if with_trace:
            db.add(StepTrace(
                job_id=job.id, step_id="s1", stage="init",
                event_type="STARTED", status="RUNNING", original_ts=started,
            ))
        db.commit()
        return run.id, device.id, job.id

    yield add, db
    db.rollback()
    if created["jobs"]:
        db.query(StepTrace).filter(StepTrace.job_id.in_(created["jobs"])).delete()
    if created["devices"]:
        db.query(DeviceLease).filter(DeviceLease.device_id.in_(created["devices"])).delete()
    if created["jobs"]:
        db.query(JobInstance).filter(JobInstance.id.in_(created["jobs"])).delete()
    if created["runs"]:
        db.query(PlanRun).filter(PlanRun.id.in_(created["runs"])).delete()
    if created["plans"]:
        db.query(Plan).filter(Plan.id.in_(created["plans"])).delete()
    if created["devices"]:
        db.query(Device).filter(Device.id.in_(created["devices"])).delete()
    if created["hosts"]:
        db.query(Host).filter(Host.id.in_(created["hosts"])).delete()
    db.commit()
    db.close()


@pytest.mark.asyncio(loop_scope="module")
async def test_unconfirmed_claim_reclaimed_before_lease_expiry(unconfirmed_seed):
    add, db = unconfirmed_seed
    run_id, _device_id, orphan_job_id = add(age_seconds=UNCONFIRMED_CLAIM_TIMEOUT_SECONDS + 15)
    # Confirmed / fresh controls must NOT be reclaimed:
    _r2, _d2, fresh_job_id = add(age_seconds=10)
    _r3, _d3, hb_job_id = add(age_seconds=UNCONFIRMED_CLAIM_TIMEOUT_SECONDS + 30, with_hb=True)
    _r4, _d4, renewed_job_id = add(age_seconds=UNCONFIRMED_CLAIM_TIMEOUT_SECONDS + 30, renewed_offset=60)
    _r5, _d5, trace_job_id = add(age_seconds=UNCONFIRMED_CLAIM_TIMEOUT_SECONDS + 30, with_trace=True)
    _r6, _d6, state_job_id = add(
        age_seconds=UNCONFIRMED_CLAIM_TIMEOUT_SECONDS + 30, exec_state="EXECUTING_STEP",
    )

    await async_engine.dispose()
    async with AsyncSessionLocal() as adb:
        count = await _reconcile_unconfirmed_claims(adb)
        assert count == 1

    db.expire_all()
    orphan = db.get(JobInstance, orphan_job_id)
    assert orphan.status == JobStatus.FAILED.value
    assert orphan.status_reason == "claim_unconfirmed"
    lease = db.query(DeviceLease).filter(DeviceLease.job_id == orphan_job_id).one()
    assert lease.status == LeaseStatus.RELEASED.value
    run = db.get(PlanRun, run_id)
    assert run.status == "PARTIAL_SUCCESS"

    for keep_id in (fresh_job_id, hb_job_id, renewed_job_id, trace_job_id, state_job_id):
        assert db.get(JobInstance, keep_id).status == JobStatus.RUNNING.value


@pytest.mark.asyncio(loop_scope="module")
async def test_stale_unknown_finalizes_claim_unconfirmed_without_grace_wait(unconfirmed_seed):
    add, db = unconfirmed_seed
    run_id, _device_id, job_id = add(
        age_seconds=20,
        job_status=JobStatus.UNKNOWN.value,
        status_reason="claim_unconfirmed",
        with_lease=False,
    )

    def _counter_val(reason: str) -> float:
        child = reconciler_actions.labels(action="to_failed", reason=reason)
        return float(child._value.get()) if hasattr(child, "_value") else 0.0

    before_unconfirmed = _counter_val("claim_unconfirmed")
    before_grace = _counter_val("unknown_grace_timeout")

    await async_engine.dispose()
    async with AsyncSessionLocal() as adb:
        count = await _reconcile_stale_unknown_jobs(adb)
        assert count == 1

    db.expire_all()
    job = db.get(JobInstance, job_id)
    assert job.status == JobStatus.FAILED.value
    assert job.status_reason == "claim_unconfirmed"
    assert db.get(PlanRun, run_id).status == "PARTIAL_SUCCESS"
    if hasattr(reconciler_actions.labels(action="to_failed", reason="claim_unconfirmed"), "_value"):
        assert _counter_val("claim_unconfirmed") == before_unconfirmed + 1.0
        assert _counter_val("unknown_grace_timeout") == before_grace


@pytest.mark.asyncio(loop_scope="module")
async def test_repeated_redelivery_response_drop_still_reclaimed_by_check_2b(unconfirmed_seed):
    add, db = unconfirmed_seed
    run_id, device_id, job_id = add(age_seconds=_UNCONFIRMED_CLAIM_REDELIVER_GRACE_SECONDS + 10)
    db.query(DeviceLease).filter(DeviceLease.job_id == job_id).update({"agent_instance_id": "inst-r"})
    db.commit()
    host_id = db.get(JobInstance, job_id).host_id

    # Redeliver once and drop the response on Agent side:
    await async_engine.dispose()
    async with AsyncSessionLocal() as adb:
        claimed, tokens = await claim_jobs_for_host(
            adb, host_id, capacity=2, agent_instance_id="inst-r", active_job_ids=[],
        )
        assert [j.id for j in claimed] == [job_id]
        assert tokens[job_id] == f"{device_id}:2"

    # Advance 121s after redelivery without Agent confirming or extending lease:
    stale_time = datetime.now(timezone.utc) - timedelta(seconds=UNCONFIRMED_CLAIM_TIMEOUT_SECONDS + 1)
    db.query(JobInstance).filter(JobInstance.id == job_id).update({"started_at": stale_time})
    db.commit()

    await async_engine.dispose()
    async with AsyncSessionLocal() as adb:
        count = await _reconcile_unconfirmed_claims(adb)
        assert count == 1

    db.expire_all()
    job = db.get(JobInstance, job_id)
    lease = db.query(DeviceLease).filter(DeviceLease.job_id == job_id).one()
    assert job.status == JobStatus.FAILED.value
    assert job.status_reason == "claim_unconfirmed"
    assert lease.status == LeaseStatus.RELEASED.value
    assert db.get(PlanRun, run_id).status == "PARTIAL_SUCCESS"


@pytest.mark.asyncio(loop_scope="module")
async def test_full_reconcile_checks_counts_claim_unconfirmed_once(unconfirmed_seed):
    add, db = unconfirmed_seed
    run_id, _device_id, job_id = add(age_seconds=UNCONFIRMED_CLAIM_TIMEOUT_SECONDS + 15)

    def _action_count() -> float:
        child = reconciler_actions.labels(action="to_failed", reason="claim_unconfirmed")
        return float(child._value.get()) if hasattr(child, "_value") else 0.0

    before = _action_count()

    await async_engine.dispose()
    await _reconcile_checks()

    db.expire_all()
    job = db.get(JobInstance, job_id)
    lease = db.query(DeviceLease).filter(DeviceLease.job_id == job_id).one()
    assert job.status == JobStatus.FAILED.value
    assert job.status_reason == "claim_unconfirmed"
    assert lease.status == LeaseStatus.RELEASED.value
    assert db.get(PlanRun, run_id).status == "PARTIAL_SUCCESS"
    if hasattr(reconciler_actions.labels(action="to_failed", reason="claim_unconfirmed"), "_value"):
        assert _action_count() == before + 1.0
