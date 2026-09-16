"""仪表盘信号去重键的形态契约（#2285，承 #1956 / #2010 / #2080）。

纯函数，无 DB / 无 app 装配。要点：UNIVIEW 键**恒三段**，与 AEE / VENDOR_AEE 的
目录键不可能同形——此前两项身份皆缺时退化成两段，会把同目录的 AEE 行与 UNIVIEW 行
并成一条（#2010「签名变化就再发射一条」在消费侧的反向残留）。
"""

from __future__ import annotations

from backend.api.routes.plan_runs import _aee_event_dedup_key, _uniview_dedup_key

_DIR = "/srv/aee/NE.103000003"


def test_uniview_key_is_always_three_segments():
    """身份字段缺失也保留占位：形态恒定，不随字段有无漂移。"""
    assert _uniview_dedup_key(_DIR, {}) == f"nfs:{_DIR}##"
    assert _uniview_dedup_key(_DIR, {"event_subtype": None, "aee_ts": None}) == f"nfs:{_DIR}##"


def test_uniview_key_never_collides_with_aee_directory_key():
    """#2285 核心：同目录的 AEE 行与 UNIVIEW 行必须是两个身份（此前会并成一条）。"""
    uniview_no_identity = _uniview_dedup_key(_DIR, {})
    aee_dir_key = _aee_event_dedup_key(1, "AEE", "", {"nfs_path": _DIR})
    vendor_dir_key = _aee_event_dedup_key(2, "VENDOR_AEE", "", {"nfs_path": _DIR})

    assert aee_dir_key == f"nfs:{_DIR}"  # AEE 家族的形态不变
    assert uniview_no_identity != aee_dir_key
    assert uniview_no_identity != vendor_dir_key


def test_uniview_key_carries_event_identity():
    """同目录不同异常 → 不同键（#2080：目录内有多个异常，不能并回一条）。"""
    java = _uniview_dedup_key(_DIR, {"event_subtype": "Java Crash", "aee_ts": "2026-09-16 10:00:00"})
    anr = _uniview_dedup_key(_DIR, {"event_subtype": "ANR", "aee_ts": "2026-09-16 10:01:00"})
    repeated = _uniview_dedup_key(_DIR, {"event_subtype": "Java Crash", "aee_ts": "2026-09-16 10:00:00"})

    assert java == repeated  # 同一条事件重复拉取仍合一条
    assert java != anr


def test_uniview_key_strips_blank_identity_fields():
    """只有空白字符的身份字段按缺失处理（与消费侧 ``or "" .strip()`` 口径一致）。"""
    assert _uniview_dedup_key(_DIR, {"event_subtype": "   ", "aee_ts": ""}) == f"nfs:{_DIR}##"
    assert _uniview_dedup_key(_DIR, {"event_subtype": " ANR ", "aee_ts": ""}) == f"nfs:{_DIR}#ANR#"


# --- #2394-②：stable identity（serial 进键、日期根出局）---

_BASE = "/mnt/hdd/aee_events/uniview_watcher"


def test_uniview_key_stable_across_date_roots():
    """C9 闭合：同设备同事件跨日期根 → 同键（旧式全路径键会裂）。"""
    ident = {"event_subtype": "Java Crash", "aee_ts": "2026-09-16_14:22:13.390"}
    d16 = _uniview_dedup_key(f"{_BASE}/0916/SN-1/JE.103000004", ident, device_serial="SN-1")
    d17 = _uniview_dedup_key(f"{_BASE}/0917/SN-1/JE.103000004", ident, device_serial="SN-1")
    assert d16 == d17 == "uniview:SN-1#JE.103000004#Java Crash#2026-09-16_14:22:13.390"


def test_uniview_key_serial_isolates_same_named_dirs():
    """两台设备同名容器目录是两个身份（真实常态：每台都有 JE.103000004）。"""
    ident = {"event_subtype": "Java Crash", "aee_ts": "T"}
    a = _uniview_dedup_key(f"{_BASE}/0916/dev-A/JE.103000004", ident, device_serial="dev-A")
    b = _uniview_dedup_key(f"{_BASE}/0916/dev-B/JE.103000004", ident, device_serial="dev-B")
    assert a != b


def test_uniview_key_still_never_collides_with_aee_when_serialized():
    """四段新形态与 AEE 目录键依旧不同形（#2285 不变量随演进保持）。"""
    uniview = _uniview_dedup_key(_DIR, {"event_subtype": "NE"}, device_serial="SN-1")
    aee = _aee_event_dedup_key(1, "AEE", "", {"nfs_path": _DIR})
    assert uniview.startswith("uniview:") and aee.startswith("nfs:")
    assert uniview != _uniview_dedup_key(_DIR, {"event_subtype": "NE"})  # 回退式与主式不互撞
