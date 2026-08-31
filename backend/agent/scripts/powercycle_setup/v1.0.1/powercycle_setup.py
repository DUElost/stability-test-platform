# -*- coding: utf-8 -*-
"""PowerCycle 部署 + 启动（init 阶段，issue #462 P0b；G15 对齐 §3.2）。

移植自 stability_PowerCycle-Test/scripts/deploy.ps1 + lib.ps1（AutoTestTool 后端）
（Install-PowerCycleApk / Test-PowerCycleSystemUid / Test-PowerCycleRebootPermission /
Set-PowerCycleDeviceStability / Grant-PowerCycleStorage / Set-PowerCyclePrefs /
Start-PowerCycleTask）。run.ps1 的续跑语义并入：``reset_count=false`` 保留 current_count。

P0 边界（G15 D3/D4）：固定 autotesttool 后端；只做 reboot 模式（poweroff 配置校验失败）；
PC pc-watchdog 不移植（设备离线由平台心跳 UNKNOWN/恢复链路兜底）。

配置解析：STP_STEP_PARAMS > STP_POWER_CYCLE_* env >
``{STP_AEE_NFS_ROOT}/power-cycle/{project}/test-config.properties``（可选）> 代码默认。

STP_STEP_PARAMS:
{
    "powercycle_resources_dir": "/opt/stability-test-agent/agent/resources/power-cycle",
    "project": "legacy",
    "test_times": 100,          // 循环次数（对应 test.times）
    "mode": "reboot",           // P0 只支持 reboot；poweroff 直接校验失败
    "power_off_minutes": 1,     // 仅 poweroff 模式有效（保留字段，reboot 模式不读）
    "wait_seconds": 3,          // 每次开机后、重启前的等待秒
    "tester": "tester",
    "auto_resume": true,        // 开机自动续跑
    "install_apks": true,
    "reset_count": true         // false = 从 prefs current_count 续跑
}

输出 (stdout): {"success": true/false, "metrics": {...}}
metrics: {apk_sha256, test_times, mode, wait_seconds, tester, auto_resume,
          adb_root, reboot_method, current_count, service_started}
reboot_method: granted | su（REBOOT 权限或 su 兜底；两者皆无 → fail-fast）
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from _lib import (
    check_reboot_permission,
    device_serial,
    grant_storage,
    install_apk,
    output_result,
    params,
    powercycle_config,
    resources_dir,
    service_alive,
    set_device_stability,
    set_prefs,
    sha256_file,
    start_task,
    try_adb_root,
)

_APK_NAME = "AutoTestTool.apk"


def _run(cfg_raw: dict) -> dict:
    cfg = powercycle_config(cfg_raw)

    apk = resources_dir(cfg) / _APK_NAME
    if not apk.is_file():
        raise FileNotFoundError(
            f"APK 不存在: {apk}（G14 分发未解锁前按 mtbf_resources_dir 先例带外部署）"
        )

    apk_sha = sha256_file(apk)
    root_ok = try_adb_root()

    if cfg["install_apks"]:
        install_apk(apk)

    reboot_method = check_reboot_permission()
    if reboot_method is None:
        raise RuntimeError(
            f"REBOOT 权限未授予且无 su：PowerCycle 无法重启设备。"
            f"请安装匹配该机型的 platform 签名 system APK（dumpsys package 确认 "
            f"android.permission.REBOOT: granted=true），userdebug 构建可用 su 兜底"
        )

    set_device_stability()
    grant_storage()

    current_count = set_prefs(cfg)
    start_task()

    if not _wait_service(timeout_s=30):
        raise RuntimeError("PowerCycleService 未在 30s 内启动（dumpsys 未见 PowerCycleService）")

    return {
        "apk_sha256": apk_sha,
        "test_times": cfg["test_times"],
        "mode": cfg["mode"],
        "wait_seconds": cfg["wait_seconds"],
        "tester": cfg["tester"],
        "auto_resume": cfg["auto_resume"],
        "adb_root": root_ok,
        "reboot_method": reboot_method,
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
