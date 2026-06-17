"""LocalDiskMonitor 单元测试（ADR-0025 Sprint 2 / S2.2）。

覆盖：超阈触发溢出直至回落 / 低于阈值不动 / 无可溢出候选不死循环。
注入 disk_usage_fn + mock archiver；不触真实磁盘/归档。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.agent.local_disk_monitor import LocalDiskMonitor


@pytest.fixture(autouse=True)
def reset_singleton():
    LocalDiskMonitor._reset_for_tests()
    yield
    LocalDiskMonitor._reset_for_tests()


def _configure(*, archiver, usage_side_effect=None, usage_value=None):
    if usage_side_effect is not None:
        disk_fn = MagicMock(side_effect=usage_side_effect)
    else:
        disk_fn = MagicMock(return_value={"usage_percent": usage_value})
    mon = LocalDiskMonitor.instance().configure(
        archiver=archiver,
        base_dir="/fake/base",
        spill_threshold_pct=80.0,
        target_pct=70.0,
        disk_usage_fn=disk_fn,
    )
    return mon, disk_fn


def test_below_threshold_no_spill():
    archiver = MagicMock()
    mon, _ = _configure(archiver=archiver, usage_value=50.0)

    spilled = mon.check_once()

    assert spilled == 0
    archiver.spill_oldest.assert_not_called()


def test_over_threshold_spills_until_recovered():
    archiver = MagicMock()
    archiver.spill_oldest.return_value = 1
    # 顶部读 90(>80) → spill → 再读 65(<=70 target) → break
    mon, disk_fn = _configure(
        archiver=archiver,
        usage_side_effect=[{"usage_percent": 90.0}, {"usage_percent": 65.0}],
    )

    spilled = mon.check_once()

    assert spilled == 1
    archiver.spill_oldest.assert_called_once_with(max_jobs=1)
    assert mon.snapshot_metrics()["spill_cycles"] == 1


def test_no_candidate_does_not_loop_forever():
    archiver = MagicMock()
    archiver.spill_oldest.return_value = 0  # 无可溢出的已完成 job(全被活跃占用)
    mon, _ = _configure(archiver=archiver, usage_value=95.0)

    spilled = mon.check_once()

    assert spilled == 0
    # 调一次 spill 返回 0 即 break,不死循环
    archiver.spill_oldest.assert_called_once()


def test_spill_continues_until_target():
    archiver = MagicMock()
    archiver.spill_oldest.return_value = 1
    # 90 → spill → 85(>70 继续) → spill → 68(<=70 break)；共 2 次 spill
    mon, _ = _configure(
        archiver=archiver,
        usage_side_effect=[
            {"usage_percent": 90.0},
            {"usage_percent": 85.0},
            {"usage_percent": 68.0},
        ],
    )

    spilled = mon.check_once()

    assert spilled == 2
    assert archiver.spill_oldest.call_count == 2


def test_usage_read_failure_skips_spill():
    """读盘失败不得当作 0% 低水位，本轮不触发溢出。"""
    archiver = MagicMock()
    mon, disk_fn = _configure(
        archiver=archiver,
        usage_side_effect=OSError("statvfs failed"),
    )

    spilled = mon.check_once()

    assert spilled == 0
    archiver.spill_oldest.assert_not_called()
    assert mon.snapshot_metrics()["local_disk_usage_pct"] == 0.0
