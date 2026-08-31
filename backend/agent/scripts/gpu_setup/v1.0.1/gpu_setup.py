# -*- coding: utf-8 -*-
"""GPU 部署 + 启动（init 阶段，issue #462 P0c；G15 对齐 §3.3）。

移植自 stability_GPU-Test/runAll----20260228.bat + run_stress_gpu.sh（无 ps1 三件套，
编排直移）：RAM 分版 → 卸载旧包 → 装 3 APK → 设备准备（飞行模式防上传等）→
推送平台自产循环脚本 → 后台启动 instrument 循环。

STP_STEP_PARAMS:
{
    "gpu_resources_dir": "/opt/stability-test-agent/agent/resources/gpu",
    "project": "legacy",
    "lite_max_gb": 8,           // RAM <= 该值用 Antutu_v10_Lite（test_id=002）
    "rounds": 700,              // 循环轮数（bat 交互默认 2000 / sh 硬编码 700）
    "install_apks": true
}

输出 (stdout): {"success": true/false, "metrics": {...}}
metrics: {variant, test_id, ram_gb, lite_max_gb, rounds, apk_sha256, adb_root,
          antutu_pkg, gpu_run_started}
"""
from __future__ import annotations

import sys
import time

from _lib import (
    _RESULT_LOG,
    adb_shell,
    detect_ram_gb,
    gpu_config,
    install_apks,
    instrument_alive,
    launch_stress,
    output_result,
    params,
    prepare_device,
    push_device_script,
    resources_dir,
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


def _run(cfg_raw: dict) -> dict:
    cfg = gpu_config(cfg_raw)
    rdir = resources_dir(cfg)

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
