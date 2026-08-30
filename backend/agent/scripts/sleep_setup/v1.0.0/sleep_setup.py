# -*- coding: utf-8 -*-
"""Sleep 部署 + 启动（init 阶段，issue #462 P0a；G15 对齐 §3.1）。

移植自 stability_Sleep-Test/scripts/deploy.ps1 + lib.ps1
（Install-SleepTestApk / Set-SleepTestDeviceStability / Grant-SleepTestStorage /
Set-ZteAppSmartOptimizeAllowed / Set-SleepTestPrefs / Start-SleepTestTask）。
run.ps1 的续跑语义并入：``reset_count=false`` 保留 prefs current_count（断点续跑）。

配置解析：STP_STEP_PARAMS > STP_SLEEP_* env >
``{STP_AEE_NFS_ROOT}/sleep/{project}/test-config.properties``（可选）> 代码默认。

STP_STEP_PARAMS:
{
    "sleep_resources_dir": "/opt/stability-test-agent/agent/resources/sleep",
    "project": "legacy",
    "test_times": 100,          // 循环次数（对应 test.times）
    "wake_seconds": 60,         // 亮屏保持秒（对应 wake.seconds）
    "sleep_seconds": 300,       // 灭屏休眠秒（对应 sleep.seconds）
    "tester": "tester",
    "auto_resume": true,        // 被杀/开机后自动续跑
    "install_apks": true,
    "reset_count": true,        // false = 从 prefs current_count 续跑（run.ps1 -ResetCount 语义）
    "zte_optimize": true        // ZTE 智能优化白名单（写 UserStrategy.db，需 root；尽力而为）
}

输出 (stdout): {"success": true/false, "metrics": {...}}
metrics: {apk_sha256, test_times, wake_seconds, sleep_seconds, tester, auto_resume,
          adb_root, current_count, service_started}
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from _lib import (
    adb_shell,
    device_serial,
    grant_storage,
    install_apk,
    output_result,
    params,
    resources_dir,
    service_alive,
    set_device_stability,
    set_prefs,
    set_zte_smart_optimize_allowed,
    sha256_file,
    sleep_config,
    start_task,
    try_adb_root,
)

_APK_NAME = "AutoTestTool.apk"


def _run(cfg_raw: dict) -> dict:
    cfg = sleep_config(cfg_raw)

    apk = resources_dir(cfg) / _APK_NAME
    if not apk.is_file():
        raise FileNotFoundError(
            f"APK 不存在: {apk}（G14 分发未解锁前按 mtbf_resources_dir 先例带外部署）"
        )

    apk_sha = sha256_file(apk)
    root_ok = try_adb_root()

    if cfg["install_apks"]:
        install_apk(apk)

    set_device_stability()
    grant_storage()
    if cfg["zte_optimize"]:
        set_zte_smart_optimize_allowed()

    current_count = set_prefs(cfg)
    start_task()

    if not _wait_service(timeout_s=30):
        raise RuntimeError("SleepTestService 未在 30s 内启动（dumpsys 未见 SleepTestService）")

    return {
        "apk_sha256": apk_sha,
        "test_times": cfg["test_times"],
        "wake_seconds": cfg["wake_seconds"],
        "sleep_seconds": cfg["sleep_seconds"],
        "tester": cfg["tester"],
        "auto_resume": cfg["auto_resume"],
        "adb_root": root_ok,
        "current_count": current_count,
        "service_started": True,
    }


def _wait_service(timeout_s: int = 30) -> bool:
    """前台服务启动是异步的，轮询 dumpsys 直到可见。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if service_alive():
            return True
        time.sleep(2)
    return False


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
