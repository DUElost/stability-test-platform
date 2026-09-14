"""unisoc_signal_trigger v1.0.2：探针面必须对准**权威根**（#73 修正的回归保护）。

v1.0.0/v1.0.1 默认盯 ``/data/uniview``（框架侧目录），真机永远看不到事件；
本用例把"权威根 + 元数据文件名"钉住：若有人改回旧值，这里立刻转红。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "unisoc_signal_trigger" / "v1.0.2"
)

spec = importlib.util.spec_from_file_location(
    "unisoc_signal_trigger_v102", _SCRIPT_DIR / "unisoc_signal_trigger.py"
)
ust = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ust)


def test_default_root_is_authoritative_ylog_path():
    """真机 + toolkit 双确认：事件根是 /data/ylog/uniview_exception。"""
    assert "/data/ylog/uniview_exception" in ust._DEFAULT_ROOTS


def test_framework_side_roots_are_not_probed():
    """框架侧目录 /data/uniview 不是事件源，不得作为默认探针面。"""
    assert not any(
        r == "/data/uniview" or r.startswith("/data/uniview/") for r in ust._DEFAULT_ROOTS
    ), "旧根（框架侧）不得回归默认探针面"


def test_sprd_extra_sources_present():
    """toolkit `_scan_platform_sources()` 的 SPRD 附加源。"""
    for root in ("/data/anr", "/data/tombstones", "/data/ylog"):
        assert root in ust._DEFAULT_ROOTS


def test_dump_reads_authoritative_info_filename():
    """元数据文件名必须是 `unievent_info`（无 .json）。"""
    assert ust._INFO_FILENAME == "unievent_info"
