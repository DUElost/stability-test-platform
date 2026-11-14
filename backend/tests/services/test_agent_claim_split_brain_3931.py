"""#3931：Agent 认领响应丢失（脑裂）时控制面的同实例未确认认领自愈测试。

守什么：
- 旧调用方不传 active_job_ids（None）时保持零变更，不干扰已有 RUNNING 作业；
- 同 agent_instance_id 上报 active_job_ids 且遗漏了超过宽限秒数、无任何执行确认信号
  （无 heartbeat / progress / execution_state / disarm_guard / StepTrace）的 RUNNING 认领：
  - PlanRun 仍在 RUNNING 且设备健康 → 轮换 fencing_token 并随本次 claim 重投；
  - 已有 StepTrace 或 last_execution_heartbeat_at 的在跑作业 → 绝不误回收或重投；
  - PlanRun 已终态 / 设备离线 → 立即释放 lease 并转 UNKNOWN(claim_unconfirmed)。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("DATABASE_URL", "").startswith("sqlite"),
    reason="认领路径需要 PostgreSQL（FOR UPDATE SKIP LOCKED / 租约部分唯一索引）",
)

from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine  # noqa: E402
from backend.models.device_lease import DeviceLease  # noqa: E402
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType  # noqa: E402
from backend.models.host import Device, Host  # noqa: E402
from backend.models.job import JobInstance, StepTrace  # noqa: E402
from backend.models.plan import Plan  # noqa: E402
from backend.models.plan_run import PlanRun  # noqa: E402
from backend.services.agent_claim import (  # noqa: E402
    _UNCONFIRMED_CLAIM_REDELIVER_GRACE_SECONDS,
    claim_jobs_for_host,
)
from backend.services.agent_job_heartbeat import (  # noqa: E402
    ExtendLockIn,
    extend_agent_job_lock,
)

PIPELINE = {
    "lifecycle": {
        "init": [{"step_id": "s1", "action": "builtin:noop", "timeout_seconds": 10}],
        "teardown": [],
    }
}


@pytest.fixture
def claim_seed():
    created = {"jobs": [], "devices": [], "runs": [], "plans": [], "hosts": []}
    suffix = uuid4().hex[:8]
    host_id = f"sb-host-{suffix}"
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    db.add(Host(id=host_id, hostname=f"h-{suffix}", status=HostStatus.ONLINE.value, created_at=now))
    plan = Plan(name=f"sb-{suffix}", description="split-brain test", created_by="pytest")
    db.add(plan)
    db.flush()
    created["hosts"].append(host_id)
    created["plans"].append(plan.id)

    def add_running_claim(
        *,
        run_status: str = "RUNNING",
        online: bool = True,
        age_seconds: int = 30,
        agent_instance_id: str = "inst-1",
        with_hb: bool = False,
        with_trace: bool = False,
    ):
        n = len(created["devices"])
        run = PlanRun(
            plan_id=plan.id, status=run_status,
            plan_snapshot={"plan_id": plan.id}, run_type="MANUAL", triggered_by="pytest",
        )
        db.add(run)
        db.flush()
        created["runs"].append(run.id)

        device = Device(
            serial=f"SB-{suffix}-{n}", host_id=host_id, tags=[], created_at=now,
            status="ONLINE" if online else "OFFLINE",
            adb_connected=online,
            adb_state="device" if online else "offline",
            lease_generation=1,
        )
        db.add(device)
        db.flush()
        created["devices"].append(device.id)

        started = now - timedelta(seconds=age_seconds)
        job = JobInstance(
            plan_run_id=run.id, plan_id=plan.id, device_id=device.id, host_id=host_id,
            status=JobStatus.RUNNING.value, pipeline_def=PIPELINE,
            created_at=started, updated_at=started, started_at=started,
            last_execution_heartbeat_at=started if with_hb else None,
        )
        db.add(job)
        db.flush()
        created["jobs"].append(job.id)

        lease = DeviceLease(
            device_id=device.id, job_id=job.id, host_id=host_id,
            lease_type=LeaseType.JOB.value, status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{device.id}:1", lease_generation=1,
            agent_instance_id=agent_instance_id,
            acquired_at=started, renewed_at=started,
            expires_at=now + timedelta(seconds=500),
        )
        db.add(lease)
        if with_trace:
            db.add(StepTrace(
                job_id=job.id, step_id="s1", stage="init",
                event_type="STARTED", status="RUNNING", original_ts=started,
            ))
        db.commit()
        return device.id, job.id, lease.fencing_token

    yield host_id, add_running_claim, db
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
async def test_unconfirmed_orphan_claim_redelivered_with_rotated_token(claim_seed):
    host_id, add_claim, db = claim_seed
    device_id, job_id, old_token = add_claim(
        age_seconds=_UNCONFIRMED_CLAIM_REDELIVER_GRACE_SECONDS + 10,
        agent_instance_id="inst-a",
    )

    await async_engine.dispose()
    # Legacy caller (active_job_ids=None) leaves existing claim untouched.
    async with AsyncSessionLocal() as adb:
        claimed, _ = await claim_jobs_for_host(
            adb, host_id, capacity=2, agent_instance_id="inst-a",
        )
        assert claimed == []

    # Same agent instance reports active_job_ids=[] -> redelivered with rotated token.
    async with AsyncSessionLocal() as adb:
        claimed, tokens = await claim_jobs_for_host(
            adb, host_id, capacity=2, agent_instance_id="inst-a", active_job_ids=[],
        )
        assert [j.id for j in claimed] == [job_id]
        assert tokens[job_id] == f"{device_id}:2"
        assert tokens[job_id] != old_token

    db.expire_all()
    lease = db.query(DeviceLease).filter(DeviceLease.job_id == job_id).one()
    assert lease.status == LeaseStatus.ACTIVE.value
    assert lease.fencing_token == f"{device_id}:2"
    assert lease.renewed_at == lease.acquired_at


@pytest.mark.asyncio(loop_scope="module")
async def test_confirmed_running_jobs_never_redelivered_even_if_omitted(claim_seed):
    host_id, add_claim, _db = claim_seed
    add_claim(age_seconds=30, agent_instance_id="inst-a", with_hb=True)
    add_claim(age_seconds=30, agent_instance_id="inst-a", with_trace=True)

    await async_engine.dispose()
    async with AsyncSessionLocal() as adb:
        claimed, _ = await claim_jobs_for_host(
            adb, host_id, capacity=5, agent_instance_id="inst-a", active_job_ids=[],
        )
        assert claimed == []


@pytest.mark.asyncio(loop_scope="module")
async def test_unconfirmed_orphan_claim_on_offline_device_is_released(claim_seed):
    host_id, add_claim, db = claim_seed
    _device_id, job_id, _ = add_claim(
        online=False,
        age_seconds=_UNCONFIRMED_CLAIM_REDELIVER_GRACE_SECONDS + 10,
        agent_instance_id="inst-a",
    )

    await async_engine.dispose()
    async with AsyncSessionLocal() as adb:
        claimed, _ = await claim_jobs_for_host(
            adb, host_id, capacity=2, agent_instance_id="inst-a", active_job_ids=[],
        )
        assert claimed == []

    db.expire_all()
    job = db.get(JobInstance, job_id)
    lease = db.query(DeviceLease).filter(DeviceLease.job_id == job_id).one()
    assert job.status == JobStatus.UNKNOWN.value
    assert job.status_reason == "claim_unconfirmed"
    assert lease.status == LeaseStatus.RELEASED.value


@pytest.mark.asyncio(loop_scope="module")
async def test_renewed_lease_via_extend_lock_is_never_redelivered(claim_seed):
    host_id, add_claim, db = claim_seed
    _device_id, job_id, old_token = add_claim(
        age_seconds=_UNCONFIRMED_CLAIM_REDELIVER_GRACE_SECONDS + 10,
        agent_instance_id="inst-a",
    )

    await async_engine.dispose()
    async with AsyncSessionLocal() as adb:
        await extend_agent_job_lock(adb, job_id, ExtendLockIn(fencing_token=old_token))

    # Even with zero heartbeat/trace and empty active_job_ids, renewed lease protects job.
    async with AsyncSessionLocal() as adb:
        claimed, tokens = await claim_jobs_for_host(
            adb, host_id, capacity=2, agent_instance_id="inst-a", active_job_ids=[],
        )
        assert claimed == []
        assert tokens == {}

    db.expire_all()
    lease = db.query(DeviceLease).filter(DeviceLease.job_id == job_id).one()
    assert lease.fencing_token == old_token
    assert lease.renewed_at > lease.acquired_at


@pytest.mark.asyncio(loop_scope="module")
async def test_expired_active_lease_is_not_redelivered_or_refreshed(claim_seed):
    host_id, add_claim, db = claim_seed
    _device_id, job_id, old_token = add_claim(
        age_seconds=_UNCONFIRMED_CLAIM_REDELIVER_GRACE_SECONDS + 10,
        agent_instance_id="inst-a",
    )
    expired_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    db.query(DeviceLease).filter(DeviceLease.job_id == job_id).update({"expires_at": expired_at})
    db.commit()

    await async_engine.dispose()
    async with AsyncSessionLocal() as adb:
        claimed, tokens = await claim_jobs_for_host(
            adb, host_id, capacity=2, agent_instance_id="inst-a", active_job_ids=[],
        )
        assert claimed == []
        assert tokens == {}

    db.expire_all()
    lease = db.query(DeviceLease).filter(DeviceLease.job_id == job_id).one()
    assert lease.status == LeaseStatus.ACTIVE.value
    assert lease.fencing_token == old_token
    assert lease.expires_at <= datetime.now(timezone.utc)
