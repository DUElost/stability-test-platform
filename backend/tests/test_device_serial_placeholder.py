"""#1356：占位 serial 检测（host 漂移防护）。"""

from __future__ import annotations

from backend.core.device_serial import is_placeholder_serial


def test_known_placeholder_detected():
    # 实证值（2026-09-11 run 360/362 拦截设备）
    assert is_placeholder_serial("0123456789ABCDEF") is True
    assert is_placeholder_serial("0123456789abcdef") is True  # 大小写不敏感
    assert is_placeholder_serial("unknown") is True
    assert is_placeholder_serial("") is True
    assert is_placeholder_serial(None) is True


def test_repeated_char_detected():
    assert is_placeholder_serial("0000000000") is True
    assert is_placeholder_serial("ffffffff") is True


def test_real_serials_pass():
    assert is_placeholder_serial("A2WENX6817000227") is False
    assert is_placeholder_serial("6R0A57SSAE7000253") is False
    assert is_placeholder_serial("JS2620839909") is False


def test_whitespace_and_case_normalized():
    assert is_placeholder_serial("  0123456789ABCDEF  ") is True
    assert is_placeholder_serial(" A2WENX6817000227 ") is False


def test_device_out_marks_suspect():
    from backend.api.schemas.device import DeviceOut
    import datetime as dt
    d = DeviceOut(id=280, serial="0123456789ABCDEF", model="MLD_LX3",
                  status="ONLINE", last_seen=dt.datetime.now())
    assert d.serial_suspect is True
    d2 = DeviceOut(id=1, serial="A2WENX6817000227", status="ONLINE")
    assert d2.serial_suspect is False
