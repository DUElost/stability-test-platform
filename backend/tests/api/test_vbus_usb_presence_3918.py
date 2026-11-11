"""#3918 / ADR-0063 D7：设备列表的 vbus 槽位 USB 在位、陈旧豁免与 Plan 的 is_vbus_flash。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.agent.contracts.vbus_flash import is_vbus_flash_lifecycle
from backend.models.host import Device, Host
from backend.services.vbus_flash_admission import is_vbus_flash_plan_steps



def _now():
    # 取调用时刻：模块导入时取一次，整套后端测试跑 20 多分钟后心跳早已过期，host_is_live 判离线。
    return datetime.now(timezone.utc)


def _host(db, host_id, *, slots=None, status="ONLINE", heartbeat=None):
    extra = {"capacity": {"effective_slots": 0, **({"usb_power_slots": slots} if slots is not None else {})}}
    now = _now()
    db.add(Host(id=host_id, hostname=host_id, status=status, last_heartbeat=heartbeat or now,
                created_at=now, extra=extra))
    db.commit()


def _device(db, serial, host_id, *, status="OFFLINE", days_ago=0):
    now = _now()
    d = Device(serial=serial, host_id=host_id, status=status, tags=[], created_at=now,
               last_seen=now - timedelta(days=days_ago))
    db.add(d)
    db.commit()
    return d


def _by_serial(resp):
    return {d["serial"]: d for d in resp.json()}


def test_presence_matched_by_serial(client, db_session, admin_headers):
    _host(db_session, "vb-h1", slots={"VB-OFF": ["1-5.2:1", "0e8d:2046"], "VB-ON": ["1-5.2:3", "0e8d:201c"],
                                      "VB-EMPTY": ["1-5.2:4", None]})
    _host(db_session, "vb-h2")
    for serial in ("VB-OFF", "VB-ON", "VB-EMPTY", "VB-UNMAPPED"):
        _device(db_session, serial, "vb-h1")
    _device(db_session, "VB-OTHERHOST", "vb-h2")

    got = _by_serial(client.get("/api/v1/devices", headers=admin_headers))
    assert got["VB-OFF"]["usb_presence"]["state"] == "powered_off"
    assert got["VB-OFF"]["usb_presence"]["slot"] == "1-5.2:1"
    assert got["VB-OFF"]["usb_presence"]["vid_pid"] == "0e8d:2046"
    assert got["VB-OFF"]["usb_presence"]["host_online"] is True
    assert got["VB-ON"]["usb_presence"]["state"] == "adb_mode"
    assert got["VB-EMPTY"]["usb_presence"]["state"] == "absent"
    assert got["VB-UNMAPPED"]["usb_presence"] is None
    assert got["VB-OTHERHOST"]["usb_presence"] is None

    one = client.get(f"/api/v1/devices/{got['VB-OFF']['id']}", headers=admin_headers).json()
    assert one["usb_presence"]["state"] == "powered_off"


def test_powered_off_mapped_device_is_not_hidden_as_stale(client, db_session, admin_headers):
    _host(db_session, "vb-h3", slots={"VB-OLD-OFF": ["1-5.2:1", "0e8d:2046"], "VB-OLD-ON": ["1-5.2:2", "0e8d:201c"]})
    _host(db_session, "vb-h4", status="OFFLINE", slots={"VB-OLD-DOWNHOST": ["1-5.2:1", "0e8d:2046"]})
    _device(db_session, "VB-OLD-OFF", "vb-h3", days_ago=30)
    _device(db_session, "VB-OLD-ON", "vb-h3", days_ago=30)
    _device(db_session, "VB-OLD-UNMAPPED", "vb-h3", days_ago=30)
    _device(db_session, "VB-OLD-DOWNHOST", "vb-h4", days_ago=30)

    default = set(_by_serial(client.get("/api/v1/devices", headers=admin_headers)))
    assert "VB-OLD-OFF" in default
    assert {"VB-OLD-ON", "VB-OLD-UNMAPPED", "VB-OLD-DOWNHOST"}.isdisjoint(default)


def test_expired_heartbeat_host_is_not_live(client, db_session, admin_headers):
    # 复核 #3921 第 1 轮 P2：状态仍是 ONLINE、心跳已过期两小时的主机不算在线——
    # 徽标 host_online=false，陈旧豁免也不放出它的设备。
    stale_hb = datetime.now(timezone.utc) - timedelta(hours=2)
    _host(db_session, "vb-h5", heartbeat=stale_hb,
          slots={"VB-EXP-OLD": ["1-5.2:1", "0e8d:2046"], "VB-EXP-NEW": ["1-5.2:2", "0e8d:2046"]})
    _device(db_session, "VB-EXP-OLD", "vb-h5", days_ago=30)
    new = _device(db_session, "VB-EXP-NEW", "vb-h5")

    resp = client.get("/api/v1/devices", headers=admin_headers)
    default = _by_serial(resp)
    assert "VB-EXP-OLD" not in default
    assert default["VB-EXP-NEW"]["usb_presence"]["host_online"] is False
    one = client.get(f"/api/v1/devices/{new.id}", headers=admin_headers).json()
    assert one["usb_presence"]["host_online"] is False
    paged = client.get("/api/v1/devices", params={"skip": 0, "limit": 50}, headers=admin_headers).json()
    assert "VB-EXP-OLD" not in {d["serial"] for d in paged["items"]}
    assert paged["total"] == len(paged["items"])


def test_host_is_live_threshold():
    from backend.core.job_timeout_config import HOST_HEARTBEAT_TIMEOUT_SECONDS
    from backend.services.vbus_usb_presence import host_is_live

    now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    fresh = SimpleNamespace(status="ONLINE", last_heartbeat=now - timedelta(seconds=HOST_HEARTBEAT_TIMEOUT_SECONDS))
    expired = SimpleNamespace(status="ONLINE",
                              last_heartbeat=now - timedelta(seconds=HOST_HEARTBEAT_TIMEOUT_SECONDS + 1))
    naive = SimpleNamespace(status="ONLINE", last_heartbeat=(now - timedelta(seconds=5)).replace(tzinfo=None))
    assert host_is_live(fresh, now) is True
    assert host_is_live(expired, now) is False
    assert host_is_live(naive, now) is True
    assert host_is_live(SimpleNamespace(status="ONLINE", last_heartbeat=None), now) is False
    assert host_is_live(SimpleNamespace(status="OFFLINE", last_heartbeat=now), now) is False


def _step(stage, sort_order, script_name, params=None, enabled=True):
    return SimpleNamespace(stage=stage, sort_order=sort_order, script_name=script_name,
                           params=params, enabled=enabled)


_VBUS = {"trigger_mode": "vbus_cycle"}


@pytest.mark.parametrize("steps,expected", [
    ([_step("init", 10, "flash_firmware", _VBUS), _step("init", 20, "check_device")], True),
    ([_step("init", 20, "flash_firmware", _VBUS), _step("init", 10, "check_device")], False),
    ([_step("init", 10, "check_device", enabled=False), _step("init", 20, "flash_firmware", _VBUS)], True),
    ([_step("patrol", 1, "noop"), _step("init", 10, "flash_firmware", _VBUS)], True),
    ([_step("init", 10, "flash_firmware", {"trigger_mode": "adb"})], False),
    ([_step("init", 10, "flash_firmware", None)], False),
    ([], False),
])
def test_plan_steps_predicate_matches_dispatch_shape(steps, expected):
    assert is_vbus_flash_plan_steps(steps) is expected
    init = [{"action": f"script:{s.script_name}", "params": s.params}
            for s in sorted(steps, key=lambda s: (s.stage, s.sort_order)) if s.enabled and s.stage == "init"]
    assert is_vbus_flash_lifecycle({"init": init}) is expected


def test_plan_out_carries_is_vbus_flash():
    from backend.api.routes.plans import _plan_out

    plan = SimpleNamespace(id=1, name="p", description=None, patrol_interval_seconds=None, timeout_seconds=None,
                           barrier_timeout_seconds=None, barrier_max_wait_seconds=None,
                           auto_archive_interval_seconds=None, next_plan_id=None, watcher_policy=None,
                           project=None, specialty=None, suite=None, created_by=None,
                           created_at=_now(), updated_at=_now())

    def step(params):
        return SimpleNamespace(id=1, step_key="flash", script_name="flash_firmware", script_version="1.4.1",
                               stage="init", sort_order=10, timeout_seconds=None, stall_seconds=None,
                               retry=0, enabled=True, params=params)

    assert _plan_out(plan, [step(_VBUS)]).is_vbus_flash is True
    assert _plan_out(plan, [step({})]).is_vbus_flash is False
