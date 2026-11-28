# -*- coding: utf-8 -*-
"""宿主侧 ADB 驱动休眠唤醒专项——初始化（init 阶段）。

无需安装 AutoTestTool APK、无需 platform 签名与系统 Alarm 唤醒权限：
1. 等待设备系统就绪（get-state==device 且 sys.boot_completed==1）；
2. 关闭插电常亮（svc power stayon false / stay_on_while_plugged_in 0）并尝试禁用锁屏；
3. 唤醒并解锁至主界面，核验当前电源态进入 ``Awake``；
4. 在 Agent 本机初始化按 ``STP_JOB_ID + serial`` 隔离的状态文件（进入 ``WAIT_WAKE``
   态，由 patrol 统一驱动「亮屏保持 → 灭屏休眠 → 唤醒解锁」循环）。

STP_STEP_PARAMS:
{
    "project": "legacy",
    "test_times": 100,               // 目标循环次数（0=按 Plan 总时长跑到点）
    "wake_seconds": 60,              // 亮屏保持秒数（>=5）
    "sleep_seconds": 300,            // 灭屏休眠秒数（>=5）
    "unlock_to_home": true,          // 唤醒时是否执行解锁 + 回主界面
    "wake_timeout_seconds": 60,      // 等待唤醒回 Awake 的超时上限（>=10）
    "sleep_confirm_seconds": 15,     // 下发 KEYCODE_SLEEP 后等待进入 Asleep 的确认秒数（>=2）
    "ready_timeout_seconds": 90      // init 等待系统就绪上限（>=10）
}
"""
from __future__ import annotations

import sys
import time
from typing import Any, Callable

from _lib import (
    _DEFAULT_PROJECT,
    _DEFAULT_READY_TIMEOUT_SECONDS,
    _DEFAULT_SLEEP_CONFIRM_SECONDS,
    _DEFAULT_SLEEP_SECONDS,
    _DEFAULT_TEST_TIMES,
    _DEFAULT_WAKE_SECONDS,
    _DEFAULT_WAKE_TIMEOUT_SECONDS,
    configure_sleep_environment,
    device_serial,
    is_awake_state,
    job_id,
    output_result,
    param_or_env,
    params,
    parse_bool,
    parse_int,
    progress_stamp,
    read_wakefulness,
    save_state,
    trigger_wakeup,
    wait_system_ready,
)


def _resolve_config(cfg: dict[str, Any]) -> dict[str, Any]:
    project = str(
        param_or_env(cfg, "project", "STP_SLEEP_PROJECT", _DEFAULT_PROJECT)
    ).strip() or _DEFAULT_PROJECT
    test_times = parse_int(
        param_or_env(cfg, "test_times", "STP_SLEEP_TEST_TIMES", _DEFAULT_TEST_TIMES),
        name="test_times",
        minimum=0,
    )
    wake_seconds = parse_int(
        param_or_env(cfg, "wake_seconds", "STP_SLEEP_WAKE_SECONDS", _DEFAULT_WAKE_SECONDS),
        name="wake_seconds",
        minimum=5,
    )
    sleep_seconds = parse_int(
        param_or_env(cfg, "sleep_seconds", "STP_SLEEP_SLEEP_SECONDS", _DEFAULT_SLEEP_SECONDS),
        name="sleep_seconds",
        minimum=5,
    )
    wake_timeout_seconds = parse_int(
        param_or_env(cfg, "wake_timeout_seconds", "STP_SLEEP_WAKE_TIMEOUT_SECONDS", _DEFAULT_WAKE_TIMEOUT_SECONDS),
        name="wake_timeout_seconds",
        minimum=10,
    )
    sleep_confirm_seconds = parse_int(
        param_or_env(cfg, "sleep_confirm_seconds", "STP_SLEEP_SLEEP_CONFIRM_SECONDS", _DEFAULT_SLEEP_CONFIRM_SECONDS),
        name="sleep_confirm_seconds",
        minimum=2,
    )
    ready_timeout_seconds = parse_int(
        param_or_env(cfg, "ready_timeout_seconds", "STP_SLEEP_READY_TIMEOUT_SECONDS", _DEFAULT_READY_TIMEOUT_SECONDS),
        name="ready_timeout_seconds",
        minimum=10,
    )
    unlock = parse_bool(
        param_or_env(cfg, "unlock_to_home", "STP_SLEEP_UNLOCK_TO_HOME", True),
        default=True,
    )
    return {
        "project": project,
        "test_times": test_times,
        "wake_seconds": wake_seconds,
        "sleep_seconds": sleep_seconds,
        "wake_timeout_seconds": wake_timeout_seconds,
        "sleep_confirm_seconds": sleep_confirm_seconds,
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

    progress_stamp({"seq": 1, "step": "sleep_adb_setup", "phase": "wait_ready"})
    ready, observed = wait_system_ready(
        cfg["ready_timeout_seconds"],
        now_fn=now_fn,
        sleep_fn=sleep_fn,
        on_poll=lambda seq, obs: progress_stamp(
            {"seq": seq + 1, "step": "sleep_adb_setup", "phase": "wait_ready", "observed": obs}
        ),
    )
    if not ready:
        raise RuntimeError(
            f"设备在 {cfg['ready_timeout_seconds']}s 内未进入就绪态（最后观测: {observed}）"
        )

    stayon_disabled = configure_sleep_environment()
    trigger_wakeup(unlock=cfg["unlock_to_home"])
    sleep_fn(1.0)
    wakefulness = read_wakefulness()
    if not is_awake_state(wakefulness):
        trigger_wakeup(unlock=cfg["unlock_to_home"])
        sleep_fn(1.5)
        wakefulness = read_wakefulness()
    if not is_awake_state(wakefulness):
        raise RuntimeError(f"初始化唤醒验证失败：当前 mWakefulness={wakefulness or '<empty>'}")

    now = now_fn()
    state = {
        "schema_version": 1,
        "driver": "host_adb",
        "job_id": cur_job_id,
        "serial": serial,
        "project": cfg["project"],
        "test_times": cfg["test_times"],
        "wake_seconds": cfg["wake_seconds"],
        "sleep_seconds": cfg["sleep_seconds"],
        "wake_timeout_seconds": cfg["wake_timeout_seconds"],
        "sleep_confirm_seconds": cfg["sleep_confirm_seconds"],
        "unlock_to_home": cfg["unlock_to_home"],
        "phase": "WAIT_WAKE",
        "seq": 0,
        "current_cycle": 1,
        "cycles_done": 0,
        "wake_failures": 0,
        "sleep_anomalies": 0,
        "wake_started_at": now,
        "sleep_started_at": None,
        "started_at": now,
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
        "wake_seconds": cfg["wake_seconds"],
        "sleep_seconds": cfg["sleep_seconds"],
        "unlock_to_home": cfg["unlock_to_home"],
        "stayon_disabled": stayon_disabled,
        "initial_wakefulness": wakefulness,
    }


def main() -> None:
    cfg = params()
    try:
        metrics = _run(cfg)
    except Exception as exc:  # noqa: BLE001
        output_result(False, error_message=str(exc))
        sys.exit(1)
    output_result(True, metrics=metrics)


if __name__ == "__main__":
    main()
