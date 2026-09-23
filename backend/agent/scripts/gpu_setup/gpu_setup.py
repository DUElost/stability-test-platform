# -*- coding: utf-8 -*-
"""GPU 部署 + 启动（init 阶段，issue #462 P0c；G15 对齐 §3.3）。

v1.2.3（#3069，2026-09-22）：修 v1.2.2 的**崩溃式回归**——adb / 设备输出原先经
``subprocess.run(text=True)`` 严格 UTF-8 解码，设备侧一个非 UTF-8 字节（实测
``0xf9``）就抛 ``UnicodeDecodeError``（非 ``OSError``，调用点无从兜住），
导致整窗 init 全灭（run 496/500 各 ~460 台倒在读日志那一步）。
本版改为收字节 + ``decode_device_output()`` 宽容解码（``errors="replace"``），
坏字节变 U+FFFD，``classify_compat_failure`` 的签名匹配不受影响。

v1.2.2（#774，2026-09-20）：长循环前增加一轮 instrument **兼容边界探测**
（``compat_probe``，默认开）。命中 UiAutomation already registered /
BaseTestCase UiDevice NPE 等已知签名 → init 快速失败，避免 273/276 台把整窗
烧在 APK 框架崩溃上。根因仍在 transsion 测试框架——本版本只收平台侧「早发现」。

v1.2.1（#2756，2026-09-19）：_install_apk_stable 保留 push/pm 失败输出 +
重试前 wait-for-device 与短退避（STP_GPU_INSTALL_RETRY_BACKOFF_SECONDS）
——链交接瞬时 adb 风暴吸收（run 431 同族取证）。

v1.2.0（#2048）：补回 v1.1.0 从 v1.0.9 拷贝时丢失的 #755 修复——重试安装前
只卸「当前失败 APK」对应的包（`_retry_uninstall_pkgs_for_apk`），不再无条件卸
FULL+LITE（否则 `scripts-debug.apk` 失败会误卸刚装成功的 Antutu 包）。

v1.1.0（#1690）：长耗时段打 PROGRESS 戳——push/pm install（378MB 级 APK 数分钟）、
pre_reboot 等待循环（最长 ~240s）全程留戳，启用 stall_seconds 的 Plan 不再把
长步骤当停滞误杀（#872 的另一剖面）；capabilities.json 声明 progress_stamps。

移植自 stability_GPU-Test/runAll----20260228.bat + run_stress_gpu.sh（无 ps1 三件套，
编排直移）：RAM 分版 → 卸载旧包 → 装 3 APK → 设备准备（飞行模式防上传等）→
推送平台自产循环脚本 → 后台启动 instrument 循环。

STP_STEP_PARAMS:
{
    "gpu_resources_dir": "/opt/stability-test-agent/agent/resources/gpu",
    "project": "legacy",
    "lite_max_gb": 8,           // RAM <= 该值用 Antutu_v10_Lite（test_id=002）
    "rounds": 700,              // 循环轮数（bat 交互默认 2000 / sh 硬编码 700）
    "install_apks": true,
    "pre_reboot": true,         // #774：setup 前重启清 UiAutomation 残留
    "compat_probe": true        // #774：长循环前一轮 instrument 冒烟
}

输出 (stdout): {"success": true/false, "metrics": {...}}
metrics: {variant, test_id, ram_gb, lite_max_gb, rounds, apk_sha256, adb_root,
          antutu_pkg, gpu_run_started, compat_probe}
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

from _lib import (
    _RESULT_LOG,
    adb_shell,
    detect_ram_gb,
    dismiss_antutu_dialogs,
    gpu_config,
    install_apks,
    instrument_alive,
    launch_stress,
    output_result,
    params,
    prepare_device,
    progress_tick,
    push_device_script,
    resources_dir,
    run_compat_probe,
    select_variant,
    try_adb_root,
)


def _wait_started(timeout_s: int = 30) -> bool:
    """启动后等待 GPU_RUN_START 标记 + instrument 进程出现。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if instrument_alive():
            out = adb_shell(f"cat {_RESULT_LOG}", timeout=30)
            if "GPU_RUN_START" in out:
                return True
        time.sleep(3)
    return False


def _pre_reboot_device() -> None:
    """#774：GPU 测试前重启设备——清累积的 UiAutomation/系统残留。

    2026-09-01 全量实证：273/276 台 am instrument 崩溃（UiAutomationService
    already registered / BaseTestCase UiDevice NPE）——仅 .92 三台可跑。
    2026-09-08 复测：崩溃设备（如 AYCGNX6728006263）经多日重启后恢复可跑
    ——根因为设备累积状态（UiAutomation 注册残留等），重启即清。

    此处 setup 启动前 reboot + 等待 boot_completed（最长 180s）。
    注意：prefs/脚本在 reboot 后仍存在（/data 保留）；仅系统状态被清。
    """
    from _lib import adb_path, decode_device_output, device_serial
    serial = device_serial()
    # #1690：reboot + 等待 boot_completed（最长 ~240s）——轮询循环内打 PROGRESS
    # 戳，否则启用 stall_seconds 的 Plan 会把它当停滞误杀（#872 另一剖面）。
    progress_tick("pre_reboot", event="reboot")
    subprocess.run([adb_path(), "-s", serial, "reboot"], capture_output=True, timeout=30)
    time.sleep(5)
    deadline = time.time() + 180
    while time.time() < deadline:
        progress_tick("pre_reboot_wait")
        try:
            subprocess.run([adb_path(), "-s", serial, "wait-for-device"],
                           capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            # v1.0.7（#774 run 355 实证 2 台 init 失败）：并发大规模 reboot 后
            # wait-for-device 可能 >60s 未返回——原实现未捕获直接抛 init 失败。
            # 捕获后继续循环（deadline 180s 兜底）。
            time.sleep(5)
            continue
        boot = subprocess.run(
            [adb_path(), "-s", serial, "shell", "getprop sys.boot_completed"],
            capture_output=True, timeout=30,
        )
        if decode_device_output(boot.stdout).strip() == "1":
            # v1.0.6（#774 实证 run 353）：boot_completed=1 ≠ 系统完全就绪——
            # 立即 instrument 时 Antutu 3D/Unity 首启失败（AssertionError:
            # antutu app start test）——589 台并发 reboot 后更甚。settle 60s
            # 让系统服务/存储稳定后再进入 install。
            settle = int(os.environ.get("STP_GPU_REBOOT_SETTLE_SECONDS", "60"))
            progress_tick("pre_reboot_settle", seconds=settle)
            time.sleep(settle)
            return
        time.sleep(5)
    raise RuntimeError(f"GPU 前置重启后设备 {serial} 未在 180s 内完成 boot")


def _run(cfg_raw: dict) -> dict:
    cfg = gpu_config(cfg_raw)
    rdir = resources_dir(cfg)

    if cfg.get("pre_reboot", True):
        _pre_reboot_device()   # #774：重启清 UiAutomation 残留（全量兼容）

    ram_gb = detect_ram_gb()
    variant, meta = select_variant(ram_gb, cfg["lite_max_gb"])
    apk_dir = rdir / variant
    if not apk_dir.is_dir():
        raise FileNotFoundError(
            f"variant 目录不存在: {apk_dir}（G14 分发未解锁前按 mtbf_resources_dir 先例带外部署）"
        )

    root_ok = try_adb_root()

    apk_sha = {}
    if cfg["install_apks"]:
        apk_sha = install_apks(apk_dir, meta["apks"])

    prepare_device()
    dismiss_antutu_dialogs(meta["antutu_pkg"])   # #774：清 Antutu 首启弹窗
    if cfg.get("compat_probe", True):
        progress_tick("compat_probe", test_id=meta["test_id"])
        run_compat_probe(meta["test_id"])
    push_device_script()
    launch_stress(cfg["rounds"], meta["test_id"])

    if not _wait_started(timeout_s=30):
        raise RuntimeError("GPU 压测未在 30s 内启动（test_log.txt 无 GPU_RUN_START 或 instrument 进程未见）")

    return {
        "variant": variant,
        "test_id": meta["test_id"],
        "antutu_pkg": meta["antutu_pkg"],
        "ram_gb": ram_gb,
        "lite_max_gb": cfg["lite_max_gb"],
        "rounds": cfg["rounds"],
        "apk_sha256": apk_sha,
        "adb_root": root_ok,
        "gpu_run_started": True,
        "compat_probe": bool(cfg.get("compat_probe", True)),
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
