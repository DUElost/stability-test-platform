"""#1686：链式衔接过滤离线设备（准入阻塞修复）。"""

from __future__ import annotations

from pathlib import Path


def _src() -> str:
    return (Path(__file__).resolve().parents[2] / "services/plan_chain_trigger.py").read_text(
        encoding="utf-8"
    )


def test_chain_filters_offline_async_and_sync():
    """两处（async / sync）都只带 ONLINE 设备——原实现原样继承父段全量。"""
    src = _src()
    assert src.count('if status == "ONLINE"') == 2, "async/sync 两路径都要过滤"


def test_chain_records_excluded_devices():
    """排除清单可观测（日志 + run_context）。"""
    src = _src()
    assert "plan_chain_trigger_excluded_offline" in src
    assert "chain_excluded_devices" in src


def test_chain_joins_device_for_status():
    """过滤依赖 Device.status（JOIN 查询）。"""
    src = _src()
    assert ".join(Device, Device.id == JobInstance.device_id)" in src
