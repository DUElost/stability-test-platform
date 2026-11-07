"""#3892 / ADR-0063 D4：派发准入只为 vbus 刷机计划放行 ``device_offline``。

守什么：
- 判定函数：只认 ``lifecycle.init[0]`` 是 ``script:flash_firmware`` 且 ``trigger_mode=vbus_cycle``；
- ``allow_device_offline`` 只跳过 device_offline 这一级，其后各级（host_offline 等）照常；
- prepare：vbus 计划的关机设备不记 device_offline 排队理由；非 vbus 计划照旧排队；
- 准入泵终检：vbus 计划物化作业；同一设备的非 vbus 计划回队（device_offline）；
- 永久理由（device_retired）不受放行影响。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.models.enums import DeviceStatus, HostStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.script import Script
from backend.services.admission_pump import (
    _RetryableAdmission,
    admission_transaction,
    claim_queued_plan_runs,
)
from backend.services.plan_dispatcher_core import PlanDispatchError
from backend.services.plan_dispatcher_sync import (
    _classify_dispatch_devices_sync,
    prepare_plan_run,
)
from backend.services.vbus_flash_admission import (
    is_vbus_flash_lifecycle,
    is_vbus_flash_pipeline,
    is_vbus_flash_snapshot,
)


def _step(action="script:flash_firmware", **params):
    return {"step_id": "s", "action": action, "version": "1.4.0", "params": params}


@pytest.mark.parametrize("lifecycle,expected", [
    ({"init": [_step(trigger_mode="vbus_cycle")]}, True),
    ({"init": [_step(trigger_mode="adb_reboot")]}, False),
    ({"init": [_step()]}, False),
    ({"init": [_step(action="script:flash_firmware_unisoc", trigger_mode="vbus_cycle")]}, False),
    ({"init": [_step(action="script:check_device"), _step(trigger_mode="vbus_cycle")]}, False),
    ({"init": [], "teardown": [_step(trigger_mode="vbus_cycle")]}, False),
    ({"init": [{"action": "script:flash_firmware", "params": "vbus_cycle"}]}, False),
    ({}, False),
    (None, False),
    ([], False),
])
def test_predicate(lifecycle, expected):
    assert is_vbus_flash_lifecycle(lifecycle) is expected
    assert is_vbus_flash_pipeline({"lifecycle": lifecycle}) is expected


def test_snapshot_predicate_never_raises():
    assert is_vbus_flash_snapshot(None) is False
    assert is_vbus_flash_snapshot({"steps": [{"stage": "bogus", "enabled": True}]}) is False


@pytest.fixture
def vbus_env(db_session):
    host = Host(id="h-vbus-adm", hostname="h-vbus-adm", status=HostStatus.ONLINE.value,
                ip="10.0.0.93", ssh_user="root", ssh_port=22,
                last_heartbeat=datetime.now(timezone.utc))
    device = Device(serial="S-vbus-adm", host_id=host.id, status=DeviceStatus.OFFLINE.value,
                    adb_connected=False, adb_state="offline")
    script = Script(name="flash_firmware", script_type="python", version="1.4.0",
                    nfs_path="/s/flash_firmware.py", content_sha256="abc",
                    default_params={"trigger_mode": "adb_reboot"})
    db_session.add_all([host, device, script])
    db_session.commit()

    def plan(trigger_mode):
        p = Plan(name=f"vbus-adm-{trigger_mode}")
        db_session.add(p)
        db_session.commit()
        db_session.add(PlanStep(plan_id=p.id, step_key="flash", script_name="flash_firmware",
                                script_version="1.4.0", stage="init", sort_order=0,
                                timeout_seconds=3600, retry=0,
                                params={"trigger_mode": trigger_mode}))
        db_session.commit()
        return p

    return host, device, plan


def test_allow_device_offline_only_skips_that_rung(db_session, vbus_env):
    host, device, _plan = vbus_env
    unavailable, _ = _classify_dispatch_devices_sync(db_session, [device.id])
    assert [u["reason"] for u in unavailable] == ["device_offline"]
    unavailable, _ = _classify_dispatch_devices_sync(
        db_session, [device.id], allow_device_offline=True)
    assert unavailable == []
    host.status = HostStatus.OFFLINE.value
    db_session.commit()
    unavailable, _ = _classify_dispatch_devices_sync(
        db_session, [device.id], allow_device_offline=True)
    assert [u["reason"] for u in unavailable] == ["host_offline"]


def test_device_retired_is_still_fatal(db_session, vbus_env):
    _host, device, plan = vbus_env
    device.retired_at = datetime.now(timezone.utc)
    db_session.commit()
    with pytest.raises(PlanDispatchError):
        prepare_plan_run(plan_id=plan("vbus_cycle").id, device_ids=[device.id],
                         triggered_by="pytest", db=db_session, run_type="MANUAL")


def test_vbus_plan_on_offline_device_is_admitted(db_session, vbus_env):
    _host, device, plan = vbus_env
    pr = prepare_plan_run(plan_id=plan("vbus_cycle").id, device_ids=[device.id],
                          triggered_by="pytest", db=db_session, run_type="MANUAL")
    assert pr.status == "QUEUED"
    assert not (pr.run_context or {}).get("queue_blockers")
    assert is_vbus_flash_snapshot(pr.plan_snapshot) is True
    attempt = dict(claim_queued_plan_runs(db_session))[pr.id]
    assert admission_transaction(db_session, pr.id, attempt) is True
    jobs = db_session.query(JobInstance).filter(JobInstance.plan_run_id == pr.id).all()
    assert len(jobs) == 1
    assert is_vbus_flash_pipeline(jobs[0].pipeline_def) is True


def test_non_vbus_plan_on_offline_device_still_queues(db_session, vbus_env):
    _host, device, plan = vbus_env
    pr = prepare_plan_run(plan_id=plan("adb_reboot").id, device_ids=[device.id],
                          triggered_by="pytest", db=db_session, run_type="MANUAL")
    assert pr.status == "QUEUED"
    assert pr.run_context["queue_blockers"][0]["reason"] == "device_offline"
    attempt = dict(claim_queued_plan_runs(db_session))[pr.id]
    with pytest.raises(_RetryableAdmission) as exc:
        admission_transaction(db_session, pr.id, attempt)
    assert exc.value.blockers[0]["reason"] == "device_offline"
