"""#3892 / ADR-0063 D4 第 5–6 条：关机设备上的 vbus 刷机作业认领。

守什么：
- 旧 Agent（不带 vbus_slots）认领不到关机设备上的任何作业——行为与现行一致；
- 带 vbus_slots=1 时，关机设备上最早 PENDING 作业是 vbus 刷机的才会被认领，
  且不占 capacity（capacity=0 也能认领）；
- 关机设备最早作业不是 vbus 刷机时不得被认领（它在等设备上线）；
- 退役设备、已有 ACTIVE 租约的设备、unauthorized（ERROR）设备不放行；
- 本 host 已有在跑的 vbus 刷机作业时不再发新的（单主机严格串行）；
- 在线设备上的 vbus 作业也只走 vbus 名额：普通名额不认领它、它也不挤占普通名额
  （复核 #3901 P1-2）。
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
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType  # noqa: E402
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

    def add(pipeline, *, online=False, retired=False, status=JobStatus.PENDING.value, offset=0,
            device_status=None, adb_state=None, execution_state=None):
        n = len(created["devices"])
        device = Device(
            serial=f"VBUS-{suffix}-{n}", host_id=host_id, tags=[], created_at=now,
            status=device_status or ("ONLINE" if online else "OFFLINE"),
            adb_connected=online,
            adb_state=adb_state or ("device" if online else "offline"),
            retired_at=now if retired else None,
        )
        db.add(device)
        db.flush()
        job = JobInstance(
            plan_run_id=run.id, plan_id=plan.id, device_id=device.id, host_id=host_id,
            status=status, pipeline_def=pipeline,
            created_at=now + timedelta(seconds=offset), updated_at=now,
            started_at=now if status == JobStatus.RUNNING.value else None,
            execution_state=execution_state,
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


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("idle_state", ["WAITING_BARRIER", "PATROL_SLEEP"])
async def test_vbus_job_past_flash_does_not_block_next_device(seeded, idle_state):
    # 阶段 B 真机验收 PlanRun 1242：A 刷完停在 INIT→PATROL barrier 等 B，
    # 守卫若把 A 算作在跑，B 永远认领不到，两边等到 barrier 超时。
    host_id, add, _db = seeded
    add(VBUS, online=True, status=JobStatus.RUNNING.value, offset=0,
        execution_state=idle_state)
    _d, second = add(VBUS, offset=1)
    assert await _claim(host_id, capacity=0, vbus_slots=1) == [second]


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("busy_state", [None, "EXECUTING_STEP", "WAITING_EXECUTION_SLOT"])
async def test_vbus_job_still_flashing_blocks_next_device(seeded, busy_state):
    # 刚认领（首个心跳未到）或仍在执行步骤的 vbus 作业照旧阻挡：单主机严格串行不变。
    host_id, add, _db = seeded
    add(VBUS, online=True, status=JobStatus.RUNNING.value, offset=0,
        execution_state=busy_state)
    add(VBUS, offset=1)
    assert await _claim(host_id, capacity=0, vbus_slots=1) == []


@pytest.mark.asyncio(loop_scope="module")
async def test_unauthorized_error_device_is_not_released(seeded):
    host_id, add, _db = seeded
    add(VBUS, device_status="ERROR", adb_state="unauthorized")
    assert await _claim(host_id, capacity=0, vbus_slots=1) == []


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("device_status,adb_state", [
    ("ERROR", "offline"),          # 只有 status 排除能挡住
    ("OFFLINE", "unauthorized"),   # 只有 adb_state 排除能挡住
])
async def test_error_and_unauthorized_are_each_excluded(seeded, device_status, adb_state):
    # 复核 #3901 备注 1：两个排除条件各自钉住。
    host_id, add, _db = seeded
    add(VBUS, device_status=device_status, adb_state=adb_state)
    assert await _claim(host_id, capacity=0, vbus_slots=1) == []



def test_mixed_queue_per_device_is_structurally_impossible(seeded):
    """复核 #3899 所提「同一离线设备较早非 vbus + 较晚 vbus」混合队列：
    ``uq_job_active_per_device`` 保证一台设备同时只有一条 PENDING/RUNNING 作业，
    该形态在库里建不出来；非 vbus 作业占着设备时的行为由
    ``test_offline_device_with_non_vbus_head_job_is_not_claimed`` 覆盖。"""
    from sqlalchemy.exc import IntegrityError

    host_id, add, db = seeded
    dev, early = add(ADB_FLASH)
    first = db.get(JobInstance, early)
    run2 = PlanRun(plan_id=first.plan_id, status="RUNNING",
                   plan_snapshot={"plan_id": first.plan_id},
                   run_type="MANUAL", triggered_by="pytest")
    db.add(run2)
    db.flush()
    db.add(JobInstance(plan_run_id=run2.id, plan_id=first.plan_id, device_id=dev,
                       host_id=host_id, status=JobStatus.PENDING.value, pipeline_def=VBUS))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


@pytest.mark.asyncio(loop_scope="module")
async def test_normal_path_cannot_bypass_running_vbus_guard(seeded):
    # 复核 #3901 P1-2 的复现：在线设备 A 有 PENDING vbus，关机设备 B 的 vbus 在跑。
    host_id, add, _db = seeded
    add(VBUS, online=True, offset=1)
    add(VBUS, status=JobStatus.RUNNING.value, offset=0)
    assert await _claim(host_id, capacity=1, vbus_slots=1) == []
    assert await _claim(host_id, capacity=1, vbus_slots=0) == []


@pytest.mark.asyncio(loop_scope="module")
async def test_online_vbus_job_uses_vbus_slot_only(seeded):
    host_id, add, _db = seeded
    _d, job = add(VBUS, online=True)
    # 旧 Agent（不带 vbus_slots）认领不到 vbus 作业，无论设备在线与否。
    assert await _claim(host_id, capacity=1, vbus_slots=0) == []
    assert await _claim(host_id, capacity=0, vbus_slots=1) == [job]


@pytest.mark.asyncio(loop_scope="module")
async def test_vbus_job_does_not_starve_normal_capacity(seeded):
    host_id, add, _db = seeded
    add(VBUS, online=True, offset=0)
    _d, normal = add(ADB_FLASH, online=True, offset=1)
    assert await _claim(host_id, capacity=1, vbus_slots=0) == [normal]


@pytest.mark.asyncio(loop_scope="module")
async def test_two_online_vbus_jobs_claim_at_most_one(seeded):
    host_id, add, _db = seeded
    _d1, first = add(VBUS, online=True, offset=0)
    add(VBUS, online=True, offset=1)
    assert await _claim(host_id, capacity=2, vbus_slots=1) == [first]


@pytest.mark.asyncio(loop_scope="module")
async def test_device_with_active_lease_is_not_released(seeded):
    # 复核 #3901 建议 2：docstring 所述「ACTIVE 租约不放行」补上用例。
    host_id, add, db = seeded
    dev, _job = add(VBUS)
    now = datetime.now(timezone.utc)
    db.add(DeviceLease(device_id=dev, host_id=host_id, lease_type=LeaseType.MAINTENANCE.value,
                       status=LeaseStatus.ACTIVE.value, fencing_token=f"t-{uuid4().hex[:8]}",
                       lease_generation=1, agent_instance_id="pytest",
                       acquired_at=now, expires_at=now + timedelta(hours=1)))
    db.commit()
    assert await _claim(host_id, capacity=0, vbus_slots=1) == []
