"""PlanRun 专项离线容忍策略单元测试（区分 Aging DDR/开关机 vs 休眠/Monkey）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.models.enums import JobStatus
from backend.services.plan_run_offline_policy import (
    DEFAULT_ABNORMAL_OFFLINE_SECONDS,
    is_prolonged_abnormal_offline,
    resolve_run_offline_policy,
)


def test_resolve_policy_distinguishes_offline_expected_vs_abnormal_specialties():
    # 休眠唤醒：非预期离线，默认 1800s
    sleep_snap = {
        "plan": {"specialty_key": "sleep"},
        "steps": [{"stage": "patrol", "script_name": "sleep_check", "params": {}}],
    }
    assert resolve_run_offline_policy(sleep_snap) == (False, DEFAULT_ABNORMAL_OFFLINE_SECONDS)

    # Monkey：非预期离线，支持快照覆盖 max_offline_seconds
    monkey_snap = {
        "plan": {"specialty_key": "monkey"},
        "steps": [{"stage": "patrol", "script_name": "monkey_check", "params": {"max_offline_seconds": 900}}],
    }
    assert resolve_run_offline_policy(monkey_snap) == (False, 900)

    # 显式 max_offline_seconds=0 关闭
    disabled_snap = {
        "plan": {"specialty_key": "sleep"},
        "steps": [{"stage": "patrol", "script_name": "sleep_check", "params": {"max_offline_seconds": 0}}],
    }
    assert resolve_run_offline_policy(disabled_snap) == (False, 0)

    # 开关机专项：离线属于正常测试过程
    pc_snap = {
        "plan": {"specialty_key": "power-cycle"},
        "steps": [{"stage": "patrol", "script_name": "powercycle_check", "params": {}}],
    }
    assert resolve_run_offline_policy(pc_snap) == (True, 0)

    # Aging DDR（specialty_key=ops 但含 aging_ddr_check）：离线属于正常测试过程
    aging_snap = {
        "plan": {"specialty_key": "ops"},
        "steps": [{"stage": "patrol", "script_name": "aging_ddr_check", "params": {"max_offline_seconds": 14400}}],
    }
    assert resolve_run_offline_policy(aging_snap) == (True, 0)

    # 刷机专项：离线属于正常过程
    flash_snap = {
        "plan": {"specialty_key": "ops"},
        "steps": [{"stage": "init", "script_name": "flash_firmware_unisoc_bsl", "params": {}}],
    }
    assert resolve_run_offline_policy(flash_snap) == (True, 0)


def test_is_prolonged_abnormal_offline_boundaries():
    now = datetime(2026, 11, 28, 10, 30, 0, tzinfo=timezone.utc)

    # 休眠/Monkey 离线 30 分钟 → 命中异常离线
    assert (
        is_prolonged_abnormal_offline(
            job_status=JobStatus.RUNNING.value,
            link_status="adb_error",
            device_last_seen=now - timedelta(seconds=1800),
            now=now,
            offline_expected=False,
            threshold_seconds=1800,
        )
        is True
    )
    assert (
        is_prolonged_abnormal_offline(
            job_status=JobStatus.RUNNING.value,
            link_status="offline",
            device_last_seen=now - timedelta(hours=10),
            now=now,
            offline_expected=False,
            threshold_seconds=1800,
        )
        is True
    )

    # 不足 30 分钟 → 不误报
    assert (
        is_prolonged_abnormal_offline(
            job_status=JobStatus.RUNNING.value,
            link_status="adb_error",
            device_last_seen=now - timedelta(seconds=1799),
            now=now,
            offline_expected=False,
            threshold_seconds=1800,
        )
        is False
    )

    # Aging DDR / 开关机（offline_expected=True）即使离线 40h 也不标 fail
    assert (
        is_prolonged_abnormal_offline(
            job_status=JobStatus.RUNNING.value,
            link_status="adb_error",
            device_last_seen=now - timedelta(hours=40),
            now=now,
            offline_expected=True,
            threshold_seconds=0,
        )
        is False
    )

    # Host 离线不误判为设备离线 fail
    assert (
        is_prolonged_abnormal_offline(
            job_status=JobStatus.RUNNING.value,
            link_status="host_offline",
            device_last_seen=now - timedelta(hours=10),
            now=now,
            offline_expected=False,
            threshold_seconds=1800,
        )
        is False
    )

    # 已终态 Job 不重复标异常离线
    assert (
        is_prolonged_abnormal_offline(
            job_status=JobStatus.FAILED.value,
            link_status="adb_error",
            device_last_seen=now - timedelta(hours=10),
            now=now,
            offline_expected=False,
            threshold_seconds=1800,
        )
        is False
    )
