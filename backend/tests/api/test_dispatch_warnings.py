"""#1356 增强：派发前可疑设备提示（placeholder_serial / device_not_online）。"""

from __future__ import annotations

from backend.api.routes.plans import _dispatch_warnings
from backend.models.host import Device


def _mk(db, serial, status="ONLINE", host_id="101"):
    """host_id 用 sample_host fixture 的 id（FK 约束）。"""
    d = Device(serial=serial, host_id=host_id, status=status)
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def test_placeholder_serial_warns(db_session, sample_host):
    d = _mk(db_session, "0123456789ABCDEF")
    w = _dispatch_warnings(db_session, [d.id])
    assert any(x["code"] == "placeholder_serial" for x in w)
    assert w[0]["device_id"] == d.id


def test_real_serial_no_warning(db_session, sample_host):
    d = _mk(db_session, "A2WENX6817000227")
    assert _dispatch_warnings(db_session, [d.id]) == []


def test_offline_device_warns(db_session, sample_host):
    d = _mk(db_session, "REAL-SERIAL-OFFLINE", status="OFFLINE")
    w = _dispatch_warnings(db_session, [d.id])
    assert any(x["code"] == "device_not_online" for x in w)


def test_both_conditions_two_warnings(db_session, sample_host):
    d = _mk(db_session, "0123456789ABCDEF", status="OFFLINE")
    codes = {x["code"] for x in _dispatch_warnings(db_session, [d.id])}
    assert codes == {"placeholder_serial", "device_not_online"}


def test_empty_ids_returns_empty(db_session):
    assert _dispatch_warnings(db_session, []) == []
