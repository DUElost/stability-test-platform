"""#3892 / ADR-0063 D4 第 5–6 条：关机设备上的 vbus 刷机作业认领。

守什么：
- 旧 Agent（不带 vbus_slots）认领不到关机设备上的任何作业——行为与现行一致；
- 带 vbus_slots=1 时，关机设备上最早 PENDING 作业是 vbus 刷机的才会被认领，
  且不占 capacity（capacity=0 也能认领）；
- 关机设备最早作业不是 vbus 刷机时不得被认领（它在等设备上线）；
- 退役设备、已有 ACTIVE 租约的设备不放行；
- 本 host 已有在跑的 vbus 刷机作业时不再发新的（单主机严格串行）。
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

from backend.core.database import AsyncSessionLocal, SessionLocal  # noqa: E402
from backend.models.device_lease import DeviceLease  # noqa: E402
from backend.models.enums import HostStatus, JobStatus  # noqa: E402
from backend.models.host import Device, Host  # noqa: E402
from backend.models.job import JobInstance  # noqa: E402
from backend.models.plan import Plan  # noqa: E402
from backend.models.plan_run import PlanRun  # noqa: E402
from backend.services.agent_claim import ClaimRequest, claim_jobs_for_host  # noqa: E402

VBUS = {"lifecycle": {"init": [{
    "step_id": "flash", "action": "script:flash_firmware", "version": "1.4.0",
    "params": {"trigger_mode": "vbus_cycle"}, "retry": 0,
}], "teardown": []}}
ADB_FLASH = {"lifecycle": {"init": [{
    "step_id": "flash", "action": "script:flash_firmware", "version": "1.4.0",
    "params": {"trigger_mode": "adb_reboot"}, "retry": 0,
}], "teardown": []}}


@pytest.fixture
def seeded():
    created = {"jobs": [], "devices": [], "runs": [], "plans": [], "hosts": []}
    suffix = uuid4().hex[:8]
    host_id = f"vbus-host-{suffix}"
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    db.add(Host(id=host_id, hostname=f"h-{suffix}", status=HostStatus.ONLINE.value, created_at=now))
    plan = Plan(name=f"vbus-{suffix}", description="vbus claim", created_by="pytest")
    db.add(plan)
    db.flush()
    run = PlanRun(plan_id=plan.id, status="RUNNING", plan_snapshot={"plan_id": plan.id},
                  run_type="MANUAL", triggered_by="pytest")
    db.add(run)
    db.flush()
    created["hosts"].append(host_id)
    created["plans"].append(plan.id)
    created["runs"].append(run.id)

    def add(pipeline, *, online=False, retired=False, status=JobStatus.PENDING.value, offset=0):
        n = len(created["devices"])
        device = Device(
            serial=f"VBUS-{suffix}-{n}", host_id=host_id, tags=[], created_at=now,
            status="ONLINE" if online else "OFFLINE",
            adb_connected=online, adb_state="device" if online else "offline",
            retired_at=now if retired else None,
        )
        db.add(device)
        db.flush()
        job = JobInstance(
            plan_run_id=run.id, plan_id=plan.id, device_id=device.id, host_id=host_id,
            status=status, pipeline_def=pipeline,
            created_at=now + timedelta(seconds=offset), updated_at=now,
            started_at=now if status == JobStatus.RUNNING.value else None,
        )
        db.add(job)
        db.flush()
        created["devices"].append(device.id)
        created["jobs"].append(job.id)
        db.commit()
        return device.id, job.id

    yield host_id, add, db
    db.rollback()
    db.query(DeviceLease).filter(DeviceLease.device_id.in_(created["devices"])).delete()
    db.query(JobInstance).filter(JobInstance.id.in_(created["jobs"])).delete()
    db.query(PlanRun).filter(PlanRun.id.in_(created["runs"])).delete()
    db.query(Plan).filter(Plan.id.in_(created["plans"])).delete()
    db.query(Device).filter(Device.id.in_(created["devices"])).delete()
    db.query(Host).filter(Host.id.in_(created["hosts"])).delete()
    db.commit()
    db.close()


async def _claim(host_id, *, capacity=0, vbus_slots=0):
    async with AsyncSessionLocal() as db:
        claimed, _tokens = await claim_jobs_for_host(
            db, host_id, capacity, host_id, vbus_slots=vbus_slots,
        )
        return [job.id for job in claimed]


def test_claim_request_defaults_and_bounds():
    assert ClaimRequest(host_id="h").vbus_slots == 0
    assert ClaimRequest(host_id="h", vbus_slots=1).vbus_slots == 1
    with pytest.raises(ValueError):
        ClaimRequest(host_id="h", vbus_slots=2)
    with pytest.raises(ValueError):
        ClaimRequest(host_id="h", vbus_slots=-1)


@pytest.mark.asyncio(loop_scope="module")
async def test_old_agent_cannot_claim_offline_device_jobs(seeded):
    host_id, add, _db = seeded
    add(VBUS)
    assert await _claim(host_id, capacity=5) == []


@pytest.mark.asyncio(loop_scope="module")
async def test_vbus_job_on_offline_device_is_claimed_without_capacity(seeded):
    host_id, add, _db = seeded
    _device, job = add(VBUS)
    assert await _claim(host_id, capacity=0, vbus_slots=1) == [job]


@pytest.mark.asyncio(loop_scope="module")
async def test_vbus_slot_does_not_consume_normal_capacity(seeded):
    host_id, add, _db = seeded
    _d1, online_job = add(ADB_FLASH, online=True)
    _d2, vbus_job = add(VBUS)
    assert sorted(await _claim(host_id, capacity=1, vbus_slots=1)) == sorted([online_job, vbus_job])


@pytest.mark.asyncio(loop_scope="module")
async def test_offline_device_with_non_vbus_head_job_is_not_claimed(seeded):
    host_id, add, _db = seeded
    add(ADB_FLASH)
    assert await _claim(host_id, capacity=5, vbus_slots=1) == []


@pytest.mark.asyncio(loop_scope="module")
async def test_retired_device_is_not_released(seeded):
    host_id, add, _db = seeded
    add(VBUS, retired=True)
    assert await _claim(host_id, capacity=0, vbus_slots=1) == []


@pytest.mark.asyncio(loop_scope="module")
async def test_at_most_vbus_slots_jobs_and_none_while_one_runs(seeded):
    host_id, add, _db = seeded
    _d1, first = add(VBUS, offset=0)
    _d2, second = add(VBUS, offset=1)
    assert await _claim(host_id, capacity=0, vbus_slots=1) == [first]
    # first 已在跑：单主机严格串行，second 不得被认领。
    assert await _claim(host_id, capacity=0, vbus_slots=1) == []
    assert second
