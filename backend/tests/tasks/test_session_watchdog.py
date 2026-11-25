"""Tests for session_watchdog checks — ADR-0019 Phase 4c updated."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.core.database import SessionLocal, async_engine
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.tasks.session_watchdog import session_watchdog_once


PIPELINE_DEF = {
    "stages": {"prepare": [], "execute": [], "post_process": []}
}


def _seed_job_with_host(
    host_heartbeat: datetime,
    job_status: str = JobStatus.RUNNING.value,
    job_ended_at=None,
) -> dict:
    suffix = uuid4().hex[:8]
    host_id = f"wd-host-{suffix}"
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{suffix}",
            status=HostStatus.ONLINE.value,
            last_heartbeat=host_heartbeat,
            created_at=now,
        )
        device = Device(
            serial=f"S-{suffix}", host_id=host_id, status="BUSY", tags=[],
            created_at=now,
        )
        db.add_all([host, device])
        db.flush()

        plan = Plan(
            name=f"wf-{suffix}", description="t", created_by="test",
        )
        db.add(plan)
        db.flush()

        step = PlanStep(
            plan_id=plan.id, step_key="default",
            script_name="dummy", script_version="v1.0.0",
            stage="init", sort_order=0,
        )
        db.add(step)
        db.flush()

        run = PlanRun(
            plan_id=plan.id, status="RUNNING",
            triggered_by="test", started_at=now,
            plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL",
        )
        db.add(run)
        db.flush()

        job = JobInstance(
            plan_run_id=run.id, plan_id=plan.id, device_id=device.id,
            host_id=host_id, status=job_status, pipeline_def=PIPELINE_DEF,
            created_at=now, updated_at=now, started_at=now,
            ended_at=job_ended_at,
        )
        db.add(job)
        db.flush()

        db.commit()
        return {
            "host_id": host_id, "device_id": device.id,
            "job_id": job.id, "run_id": run.id,
        }
    finally:
        db.close()


def _get_job(job_id: int) -> JobInstance:
    db = SessionLocal()
    try:
        j = db.get(JobInstance, job_id)
        db.expunge(j)
        return j
    finally:
        db.close()


def _get_host(host_id: str) -> Host:
    db = SessionLocal()
    try:
        h = db.get(Host, host_id)
        db.expunge(h)
        return h
    finally:
        db.close()


@pytest.mark.asyncio
async def test_host_timeout_marks_offline_and_jobs_unknown():
    """Host with stale heartbeat → OFFLINE, RUNNING jobs → UNKNOWN."""
    old_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=300)
    seed = _seed_job_with_host(host_heartbeat=old_heartbeat)

    await async_engine.dispose()
    await session_watchdog_once()

    host = _get_host(seed["host_id"])
    assert host.status == HostStatus.OFFLINE.value

    # #1258：心跳超时是 host_heartbeat_missed_total 的生产者（仪表板依赖）
    from prometheus_client import REGISTRY

    missed = REGISTRY.get_sample_value(
        "stability_host_heartbeat_missed_total", {"host_id": seed["host_id"]},
    )
    assert missed == 1.0

    job = _get_job(seed["job_id"])
    assert job.status == JobStatus.UNKNOWN.value


@pytest.mark.asyncio
async def test_watchdog_host_timeout_keeps_lease_active():
    """Phase 4c: host timeout → UNKNOWN but lease stays ACTIVE.

    Watchdog no longer calls release_lease — Reconciler is the sole handler
    of lease expiration.
    """
    old_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=300)
    future_expiry = datetime.now(timezone.utc) + timedelta(seconds=600)
    seed = _seed_job_with_host(
        host_heartbeat=old_heartbeat,
    )

    # Create an ACTIVE lease for the job
    db = SessionLocal()
    try:
        lease = DeviceLease(
            device_id=seed["device_id"],
            job_id=seed["job_id"],
            host_id=seed["host_id"],
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{seed['device_id']}:1",
            lease_generation=1,
            agent_instance_id=seed["host_id"],
            acquired_at=datetime.now(timezone.utc),
            renewed_at=datetime.now(timezone.utc),
            expires_at=future_expiry,
        )
        db.add(lease)
        db.commit()
    finally:
        db.close()

    await async_engine.dispose()
    await session_watchdog_once()

    # Job → UNKNOWN
    job = _get_job(seed["job_id"])
    assert job.status == JobStatus.UNKNOWN.value

    # Lease must still be ACTIVE (not released by watchdog)
    db = SessionLocal()
    try:
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
            f"Lease must stay ACTIVE after host timeout; got {dl.status}"
        )

        # Phase 6d: device_leases is the sole source of truth.  Projection
        # columns (device.lock_run_id / lock_expires_at) are decommissioned;
        # the ACTIVE-lease assertion above fully covers the semantic.
    finally:
        db.close()



@pytest.mark.asyncio
async def test_complete_committed_before_watchdog_locks_is_not_overwritten():
    """#792：候选查询前 /complete 已提交 COMPLETED → watchdog 不处理该行、
    终态不被覆写。

    注：本场景在修复前亦通过（候选查询自带 status==RUNNING 过滤，查询前
    提交的行根本不会入选）——真正的 lost-update 窗口在**查询后、commit
    前**，黑盒无法确定性注入（修复后的行锁会让注入的并发提交阻塞）。
    本用例作「过滤语义不被移除」的回归；锁复读语义与 reconciler/recycler
    同源，见 Agent Note 的验证说明。
    """
    from backend.core.database import AsyncSessionLocal
    from backend.services.state_machine import JobStateMachine
    from backend.tasks.session_watchdog import _check_host_heartbeat_timeouts

    old_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=300)
    seed = _seed_job_with_host(host_heartbeat=old_heartbeat)

    await async_engine.dispose()
    async with AsyncSessionLocal() as db:
        # 模拟 watchdog 候选扫描：job 已进入本 session identity map（RUNNING）
        candidate = await db.get(JobInstance, seed["job_id"])
        assert candidate.status == JobStatus.RUNNING.value

        # /complete 在另一连接提交 COMPLETED
        sync_db = SessionLocal()
        try:
            j = sync_db.get(JobInstance, seed["job_id"])
            JobStateMachine.transition(j, JobStatus.COMPLETED, "complete_concurrent")
            j.ended_at = datetime.now(timezone.utc)
            sync_db.commit()
        finally:
            sync_db.close()

        _, jobs_unknown = await _check_host_heartbeat_timeouts(db)
        await db.rollback()

    assert jobs_unknown == 0, "已提交终态不得再转 UNKNOWN"
    job = _get_job(seed["job_id"])
    assert job.status == JobStatus.COMPLETED.value, "已提交终态被 watchdog 覆写"


def _get_device(device_id: int) -> Device:
    db = SessionLocal()
    try:
        d = db.get(Device, device_id)
        db.expunge(d)
        return d
    finally:
        db.close()


@pytest.mark.asyncio
async def test_watchdog_cascades_devices_without_any_get_and_preserves_facts():
    """#3325 B1：无任何 GET 访问时 watchdog 持久化主机与设备离线，并保留 ADB/退役事实且不影响无主设备。"""
    now = datetime.now(timezone.utc)
    old_hb = now - timedelta(seconds=600)
    last_seen_at = now - timedelta(hours=2)
    retired_at = now - timedelta(days=1)
    suffix = uuid4().hex[:8]
    host_id = f"wd-cas-{suffix}"

    db = SessionLocal()
    try:
        host = Host(
            id=host_id,
            hostname=f"hc-{suffix}",
            status=HostStatus.ONLINE.value,
            last_heartbeat=old_hb,
            created_at=now,
        )
        dev_normal = Device(
            serial=f"SC-NORM-{suffix}",
            host_id=host_id,
            status="ONLINE",
            adb_state="device",
            adb_connected=True,
            last_seen=last_seen_at,
            tags=[],
            created_at=now,
        )
        dev_retired = Device(
            serial=f"SC-RET-{suffix}",
            host_id=host_id,
            status="ONLINE",
            adb_state="unauthorized",
            adb_connected=False,
            last_seen=last_seen_at,
            retired_at=retired_at,
            retired_by="admin",
            retire_reason="broken",
            tags=[],
            created_at=now,
        )
        dev_orphan = Device(
            serial=f"SC-ORPH-{suffix}",
            host_id=None,
            status="ONLINE",
            tags=[],
            created_at=now,
        )
        db.add_all([host, dev_normal, dev_retired, dev_orphan])
        db.commit()
        ids = (dev_normal.id, dev_retired.id, dev_orphan.id)
    finally:
        db.close()

    await async_engine.dispose()
    await session_watchdog_once(now=now)

    assert _get_host(host_id).status == HostStatus.OFFLINE.value

    d_norm = _get_device(ids[0])
    assert d_norm.status == "OFFLINE"
    assert d_norm.adb_state == "device"
    assert d_norm.adb_connected is True
    assert d_norm.last_seen is not None

    d_ret = _get_device(ids[1])
    assert d_ret.status == "OFFLINE"
    assert d_ret.adb_state == "unauthorized"
    assert d_ret.retired_at is not None
    assert d_ret.retired_by == "admin"
    assert d_ret.retire_reason == "broken"

    # 无主设备不受主机级联影响
    assert _get_device(ids[2]).status == "ONLINE"


@pytest.mark.asyncio
async def test_watchdog_reconciles_null_degraded_and_existing_offline_hosts():
    """#3325 B1：覆盖 NULL 心跳、新鲜/过期 DEGRADED 与存量 OFFLINE 主机关联设备修补。"""
    from prometheus_client import REGISTRY

    now = datetime.now(timezone.utc)
    fresh_hb = now - timedelta(seconds=30)
    expired_hb = now - timedelta(seconds=600)
    suffix = uuid4().hex[:8]

    h_null_id = f"wd-null-{suffix}"
    h_deg_fresh_id = f"wd-degf-{suffix}"
    h_deg_exp_id = f"wd-dege-{suffix}"
    h_off_id = f"wd-off-{suffix}"

    db = SessionLocal()
    try:
        hosts = [
            Host(id=h_null_id, hostname=h_null_id, status=HostStatus.ONLINE.value, last_heartbeat=None, created_at=now),
            Host(id=h_deg_fresh_id, hostname=h_deg_fresh_id, status=HostStatus.DEGRADED.value, last_heartbeat=fresh_hb, created_at=now),
            Host(id=h_deg_exp_id, hostname=h_deg_exp_id, status=HostStatus.DEGRADED.value, last_heartbeat=expired_hb, created_at=now),
            Host(id=h_off_id, hostname=h_off_id, status=HostStatus.OFFLINE.value, last_heartbeat=expired_hb, created_at=now),
        ]
        devs = [
            Device(serial=f"D-NULL-{suffix}", host_id=h_null_id, status="ONLINE", tags=[], created_at=now),
            Device(serial=f"D-DEGF-{suffix}", host_id=h_deg_fresh_id, status="ONLINE", tags=[], created_at=now),
            Device(serial=f"D-DEGE-{suffix}", host_id=h_deg_exp_id, status="BUSY", tags=[], created_at=now),
            Device(serial=f"D-OFF-{suffix}", host_id=h_off_id, status="ONLINE", tags=[], created_at=now),
        ]
        db.add_all(hosts + devs)
        db.commit()
        dev_ids = [d.id for d in devs]
    finally:
        db.close()

    await async_engine.dispose()
    await session_watchdog_once(now=now)

    # 1. NULL 心跳 ONLINE 主机 → 离线 + 设备离线
    assert _get_host(h_null_id).status == HostStatus.OFFLINE.value
    assert _get_device(dev_ids[0]).status == "OFFLINE"

    # 2. 新鲜 DEGRADED 不等于离线 → 主机与设备均保持原状态
    assert _get_host(h_deg_fresh_id).status == HostStatus.DEGRADED.value
    assert _get_device(dev_ids[1]).status == "ONLINE"

    # 3. 过期 DEGRADED → 离线 + 设备离线
    assert _get_host(h_deg_exp_id).status == HostStatus.OFFLINE.value
    assert _get_device(dev_ids[2]).status == "OFFLINE"

    # 4. 存量 OFFLINE 主机 → 残留设备被修补为 OFFLINE，不误记新的 host_heartbeat_missed
    assert _get_host(h_off_id).status == HostStatus.OFFLINE.value
    assert _get_device(dev_ids[3]).status == "OFFLINE"
    assert REGISTRY.get_sample_value("stability_host_heartbeat_missed_total", {"host_id": h_off_id}) is None


@pytest.mark.asyncio
async def test_watchdog_heartbeat_recovery_race_skips_recovered_host():
    """#3325 B1：候选后、落库前若心跳恢复为新鲜 ONLINE，锁行复验必须跳过而不覆盖。"""
    from backend.core.database import AsyncSessionLocal
    from backend.tasks.session_watchdog import _reconcile_host_heartbeat_timeouts

    now = datetime.now(timezone.utc)
    expired_hb = now - timedelta(seconds=600)
    suffix = uuid4().hex[:8]
    host_id = f"wd-race-{suffix}"

    db = SessionLocal()
    try:
        host = Host(
            id=host_id,
            hostname=host_id,
            status=HostStatus.ONLINE.value,
            last_heartbeat=expired_hb,
            created_at=now,
        )
        dev = Device(
            serial=f"D-RACE-{suffix}",
            host_id=host_id,
            status="ONLINE",
            tags=[],
            created_at=now,
        )
        db.add_all([host, dev])
        db.commit()
        dev_id = dev.id
    finally:
        db.close()

    await async_engine.dispose()
    async with AsyncSessionLocal() as adb:
        # 先让过期行进入 identity map，再由另一连接模拟心跳恢复提交
        stale_cached = await adb.get(Host, host_id)
        assert stale_cached is not None

        sync_db = SessionLocal()
        try:
            h = sync_db.get(Host, host_id)
            h.last_heartbeat = now - timedelta(seconds=5)
            h.status = HostStatus.ONLINE.value
            sync_db.commit()
        finally:
            sync_db.close()

        hosts_off, jobs_unk, devs_off = await _reconcile_host_heartbeat_timeouts(adb, now=now)
        await adb.rollback()

    assert hosts_off == 0
    assert jobs_unk == 0
    assert devs_off == 0
    assert _get_host(host_id).status == HostStatus.ONLINE.value
    assert _get_device(dev_id).status == "ONLINE"
