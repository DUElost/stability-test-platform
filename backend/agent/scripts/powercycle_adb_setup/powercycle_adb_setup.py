# -*- coding: utf-8 -*-
"""宿主侧 ADB 驱动开关机专项——初始化（init 阶段）。

无需安装测试 APK、无需 ROM platform 签名与 android.permission.REBOOT 权限：
1. 校验参数并等待设备系统就绪（get-state==device 且 sys.boot_completed==1）；
2. best-effort 配置插电常亮并解锁回主界面；
3. 读取内核 ``/proc/sys/kernel/random/boot_id`` 作为真重启判定基线；
4. 在 Agent 本机初始化按 ``STP_JOB_ID + serial`` 隔离的状态文件（不在 init 触发
   首轮重启，避免多机并行过 INIT→PATROL barrier 时提前掉线）。

STP_STEP_PARAMS:
{
    "project": "legacy",
    "test_times": 100,                   // 目标循环次数（0=不限次数，按 Plan 总时长跑到点）
    "wait_seconds": 30,                  // 开机解锁到主界面后至下次重启的间隔秒数（>=1）
    "boot_timeout_seconds": 300,         // 单轮等待重启开机的超时上限（>=30）
    "reboot_confirm_timeout_seconds": 30,// 下发 reboot 后等待 boot_id 变化或离线的宽限（>=5）
    "unlock_to_home": true,              // 开机后是否执行唤醒 + 解锁 + 回主界面
    "ready_timeout_seconds": 90          // init 等待设备系统就绪的上限秒数（>=10）
}
"""
from __future__ import annotations

import sys
import time
from typing import Any, Callable

from _lib import (
    _DEFAULT_BOOT_TIMEOUT_SECONDS,
    _DEFAULT_PROJECT,
    _DEFAULT_READY_TIMEOUT_SECONDS,
    _DEFAULT_REBOOT_CONFIRM_TIMEOUT_SECONDS,
    _DEFAULT_TEST_TIMES,
    _DEFAULT_WAIT_SECONDS,
    device_serial,
    job_id,
    output_result,
    param_or_env,
    params,
    parse_bool,
    parse_int,
    progress_stamp,
    read_boot_id,
    save_state,
    set_device_stayon_best_effort,
    unlock_to_home,
    wait_system_ready,
)


def _resolve_config(cfg: dict[str, Any]) -> dict[str, Any]:
    project = str(
        param_or_env(cfg, "project", "STP_POWER_CYCLE_PROJECT", _DEFAULT_PROJECT)
    ).strip() or _DEFAULT_PROJECT
    test_times = parse_int(
        param_or_env(cfg, "test_times", "STP_POWER_CYCLE_TEST_TIMES", _DEFAULT_TEST_TIMES),
        name="test_times",
        minimum=0,
    )
    wait_seconds = parse_int(
        param_or_env(cfg, "wait_seconds", "STP_POWER_CYCLE_WAIT_SECONDS", _DEFAULT_WAIT_SECONDS),
        name="wait_seconds",
        minimum=1,
    )
    boot_timeout_seconds = parse_int(
        param_or_env(
            cfg,
            "boot_timeout_seconds",
            "STP_POWER_CYCLE_BOOT_TIMEOUT_SECONDS",
            _DEFAULT_BOOT_TIMEOUT_SECONDS,
        ),
        name="boot_timeout_seconds",
        minimum=30,
    )
    reboot_confirm_timeout_seconds = parse_int(
        param_or_env(
            cfg,
            "reboot_confirm_timeout_seconds",
            "STP_POWER_CYCLE_REBOOT_CONFIRM_TIMEOUT_SECONDS",
            _DEFAULT_REBOOT_CONFIRM_TIMEOUT_SECONDS,
        ),
        name="reboot_confirm_timeout_seconds",
        minimum=5,
    )
    ready_timeout_seconds = parse_int(
        param_or_env(
            cfg,
            "ready_timeout_seconds",
            "STP_POWER_CYCLE_READY_TIMEOUT_SECONDS",
            _DEFAULT_READY_TIMEOUT_SECONDS,
        ),
        name="ready_timeout_seconds",
        minimum=10,
    )
    unlock = parse_bool(
        param_or_env(cfg, "unlock_to_home", "STP_POWER_CYCLE_UNLOCK_TO_HOME", True),
        default=True,
    )
    return {
        "project": project,
        "test_times": test_times,
        "wait_seconds": wait_seconds,
        "boot_timeout_seconds": boot_timeout_seconds,
        "reboot_confirm_timeout_seconds": reboot_confirm_timeout_seconds,
        "ready_timeout_seconds": ready_timeout_seconds,
        "unlock_to_home": unlock,
    }


def _run(
    cfg_raw: dict[str, Any],
    *,
    now_fn: Callable[[], float] = time.time,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    cfg = _resolve_config(cfg_raw)
    serial = device_serial()
    cur_job_id = job_id()

    progress_stamp({"seq": 1, "step": "powercycle_adb_setup", "phase": "wait_ready"})
    ready, observed = wait_system_ready(
        cfg["ready_timeout_seconds"],
        now_fn=now_fn,
        sleep_fn=sleep_fn,
        on_poll=lambda seq, obs: progress_stamp(
            {"seq": seq + 1, "step": "powercycle_adb_setup", "phase": "wait_ready", "observed": obs}
        ),
    )
    if not ready:
        raise RuntimeError(
            f"设备在 {cfg['ready_timeout_seconds']}s 内未进入就绪态（最后观测: {observed}）"
        )

    stayon_ok = set_device_stayon_best_effort()
    unlock_ok = unlock_to_home() if cfg["unlock_to_home"] else False

    initial_boot_id = read_boot_id()
    if not initial_boot_id:
        raise RuntimeError(
            "无法读取设备内核 /proc/sys/kernel/random/boot_id，无法建立真重启判定基线"
        )

    started_at = now_fn()
    state = {
        "schema_version": 1,
        "driver": "host_adb",
        "job_id": cur_job_id,
        "serial": serial,
        "project": cfg["project"],
        "test_times": cfg["test_times"],
        "wait_seconds": cfg["wait_seconds"],
        "boot_timeout_seconds": cfg["boot_timeout_seconds"],
        "reboot_confirm_timeout_seconds": cfg["reboot_confirm_timeout_seconds"],
        "unlock_to_home": cfg["unlock_to_home"],
        "phase": "IDLE",
        "seq": 0,
        "current_cycle": 0,
        "cycles_done": 0,
        "reboot_failures": 0,
        "initial_boot_id": initial_boot_id,
        "last_boot_id": initial_boot_id,
        "reboot_sent_at": None,
        "ready_at": None,
        "started_at": started_at,
        "finished_at": None,
        "final_status": "",
        "failure_reason": "",
        "entries": [],
    }
    save_state(state, serial=serial)

    return {
        "driver": "host_adb",
        "project": cfg["project"],
        "test_times": cfg["test_times"],
        "wait_seconds": cfg["wait_seconds"],
        "boot_timeout_seconds": cfg["boot_timeout_seconds"],
        "reboot_confirm_timeout_seconds": cfg["reboot_confirm_timeout_seconds"],
        "unlock_to_home": cfg["unlock_to_home"],
        "initial_boot_id": initial_boot_id,
        "stayon_applied": stayon_ok,
        "unlock_applied": unlock_ok,
    }


def main() -> None:
    cfg = params()
    try:
        metrics = _run(cfg)
    except Exception as exc:  # noqa: BLE001 — 脚本顶层统一输出错误
        output_result(False, error_message=str(exc))
        sys.exit(1)
    output_result(True, metrics=metrics)


if __name__ == "__main__":
    main()
