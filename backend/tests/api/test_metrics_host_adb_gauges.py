"""#2754：``/metrics`` 的 per-host adb_state 分桶计数（真实生产者 + 三条口径）。

fleet 级 ``stability_device_online{status}`` 只有总数——host .81 的 15/16 台 adb offline
在平台上不可见（host ONLINE、心跳新鲜）。本文件锁住新增 gauge 的四件事：
分桶值、缺桶落 0、未知/空值归 ``other``、退役 host 与无 host 归属的设备不进指标。
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.models.enums import DeviceStatus, HostStatus
from backend.models.host import Device, Host


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _host(db, host_id: str, *, retired: bool = False) -> Host:
    # host.ip 有唯一约束（host_ip_key）——每台派生一个不同地址，别共用常量。
    octet = (sum(ord(ch) for ch in host_id) % 200) + 10
    host = Host(
        id=host_id, hostname=host_id, status=HostStatus.ONLINE.value,
        ip=f"10.44.0.{octet}", ssh_user="root", ssh_port=22, extra={},
        last_heartbeat=_now(), created_at=_now(),
    )
    if retired:
        host.retired_at = _now()
    db.add(host)
    return host


def _device(db, serial: str, host_id: str | None, adb_state: str | None) -> Device:
    db.add(Device(
        serial=serial, host_id=host_id, status=DeviceStatus.ONLINE.value,
        adb_state=adb_state, tags=[], created_at=_now(),
    ))


def test_per_host_adb_state_buckets_with_zeros(client, db_session, monkeypatch):
    """在册 host 的四个桶全部落值（含 0）——缺 series 会让波次判据失去基线。"""
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    _host(db_session, "adb-h1")
    _host(db_session, "adb-h2")          # 有 host、零设备
    for i in range(3):
        _device(db_session, f"adb-ok-{i}", "adb-h1", "device")
    _device(db_session, "adb-off-1", "adb-h1", "offline")
    _device(db_session, "adb-un-1", "adb-h1", "unauthorized")
    db_session.commit()

    body = client.get("/metrics").text

    assert 'stability_host_device_adb_state{host_id="adb-h1",state="device"} 3.0' in body
    assert 'stability_host_device_adb_state{host_id="adb-h1",state="offline"} 1.0' in body
    assert 'stability_host_device_adb_state{host_id="adb-h1",state="unauthorized"} 1.0' in body
    assert 'stability_host_device_adb_state{host_id="adb-h1",state="other"} 0.0' in body
    for state in ("device", "offline", "unauthorized", "other"):
        assert (
            f'stability_host_device_adb_state{{host_id="adb-h2",state="{state}"}} 0.0' in body
        ), f"零设备 host 的 {state} 桶缺失"


def test_unknown_and_blank_adb_state_collapse_into_other(client, db_session, monkeypatch):
    """adb 原始状态是自由字符串——不归桶则基数交给运气、选择器要逐值列举（漏一个静默不告）。"""
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    _host(db_session, "adb-h3")
    _device(db_session, "adb-x1", "adb-h3", "no permissions")
    _device(db_session, "adb-x2", "adb-h3", "")
    _device(db_session, "adb-x3", "adb-h3", None)
    db_session.commit()

    body = client.get("/metrics").text

    assert 'stability_host_device_adb_state{host_id="adb-h3",state="other"} 3.0' in body
    assert 'stability_host_device_adb_state{host_id="adb-h3",state="device"} 0.0' in body
    # 未归桶的原始值不得成为标签值（基数与选择器双重失守）
    assert 'state="no permissions"' not in body


def test_retired_host_and_ownerless_devices_are_excluded(client, db_session, monkeypatch):
    """退役 = 不再是容量（ADR-0038 D5）；无 host 归属的设备不进 per-host 视角。"""
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    _host(db_session, "adb-retired", retired=True)
    _device(db_session, "adb-r1", "adb-retired", "offline")
    # 孤儿设备（host_id 为 NULL）：`device_host_id_fkey` 允许 NULL，
    # 指向不存在 host 的行则被 FK 直接拒掉——所以「host 不存在」这一形态在库里不可表示。
    _device(db_session, "adb-orphan", None, "offline")
    _host(db_session, "adb-h4")
    _device(db_session, "adb-h4-1", "adb-h4", "device")
    db_session.commit()

    body = client.get("/metrics").text

    assert 'host_id="adb-retired"' not in body, "退役 host 仍在指标里（会永远盯着不存在的机器）"
    assert 'stability_host_device_adb_state{host_id="adb-h4",state="device"} 1.0' in body
