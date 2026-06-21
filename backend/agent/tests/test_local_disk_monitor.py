"""HddSpillMonitor 单元测试（ADR-0025 方案 C — HDD 溢出上送 15.4）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.agent.local_disk_monitor import HddSpillMonitor


@pytest.fixture(autouse=True)
def reset_singleton():
    HddSpillMonitor._reset_for_tests()
    yield
    HddSpillMonitor._reset_for_tests()


def test_below_threshold_no_spill(tmp_path):
    cifs = tmp_path / "cifs"
    cifs.mkdir()
    disk_fn = MagicMock(return_value={"usage_percent": 42.0})
    mon = HddSpillMonitor.instance().configure(
        hdd_root=str(tmp_path),
        cifs_root=str(cifs),
        spill_threshold_pct=80.0,
        disk_usage_fn=disk_fn,
    )

    n = mon.check_once()

    assert n == 0


def test_above_threshold_spills_oldest(tmp_path):
    hdd = tmp_path / "hdd" / "folder" / "SERIAL" / "aee_exp" / "2026_0601_db.01"
    hdd.mkdir(parents=True)
    (hdd / "__exp_main.txt").write_text("crash", encoding="utf-8")
    (hdd / "main.dbg").write_text("dbg", encoding="utf-8")

    cifs = tmp_path / "cifs"
    cifs.mkdir()

    call_count = {"n": 0}
    def _usage(*_a):
        call_count["n"] += 1
        return {"usage_percent": 90.0 if call_count["n"] <= 2 else 50.0}

    mon = HddSpillMonitor.instance().configure(
        hdd_root=str(tmp_path / "hdd"),
        cifs_root=str(cifs),
        spill_threshold_pct=80.0,
        target_pct=70.0,
        disk_usage_fn=_usage,
    )

    n = mon.check_once()

    assert n == 1
    cifs_events = list(cifs.glob("devices/**/__exp_main.txt"))
    assert len(cifs_events) == 1
    assert not hdd.exists()
    assert mon.snapshot_metrics()["spilled_total"] == 1


def test_no_event_dirs_just_warns(tmp_path):
    hdd = tmp_path / "hdd"
    hdd.mkdir()
    cifs = tmp_path / "cifs"
    cifs.mkdir()
    disk_fn = MagicMock(return_value={"usage_percent": 90.0})
    mon = HddSpillMonitor.instance().configure(
        hdd_root=str(hdd),
        cifs_root=str(cifs),
        spill_threshold_pct=80.0,
        disk_usage_fn=disk_fn,
    )

    n = mon.check_once()

    assert n == 0
