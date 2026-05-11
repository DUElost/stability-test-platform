"""Monkey 一站式脚本：推送资源 + 启动 AIMonkey 测试。

替代 monkey_setup + monkey_launch，将 AIMonkeyTest 二进制（aim/aimwd/aimonkey.apk）
推送至设备，启动 MonkeyTest.sh 看门狗和 aimwd 守护进程。

可直接在节点上运行：
    STP_DEVICE_SERIAL=... python monkey_test.py

环境变量:
    STP_DEVICE_SERIAL      (required)
    STP_ADB_PATH           (default: adb)
    STP_STEP_PARAMS        (optional JSON)

STP_STEP_PARAMS:
{
    "push_resources": true,       // 推送 /sdcard/resource/ 媒体资源
    "blacklist": true,            // 使用黑名单
    "need_nohup": true,           // nohup 启动
    "aimonkey_dir": "/opt/stability-test-agent/resources/aimonkey/AIMonkeyTest_20260317"
}

输出 (stdout):
    {"success": true/false, "error_message": "...", "metrics": {...}}
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from _adb import adb_path, adb_shell, device_serial, output_result, params


# ── 资源解析 ──

def _resolve_aimonkey_dir(cfg: dict) -> Path:
    explicit = cfg.get("aimonkey_dir", "")
    if explicit and Path(explicit).is_dir():
        return Path(explicit)

    # 从脚本位置反推 agent 安装根目录
    script_dir = Path(__file__).resolve().parent  # v1.0.0/
    install_root = script_dir.parents[3]  # scripts/monkey_test/v1.0.0/ → agent root
    resource_dir = install_root / "resources" / "aimonkey" / "AIMonkeyTest_20260317"
    if resource_dir.is_dir():
        return resource_dir

    return resource_dir  # 返回默认路径，后续检查


def _run_adb(serial: str, args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """执行 adb 命令并返回 (returncode, stdout, stderr)。"""
    result = subprocess.run(
        [adb_path(), "-s", serial] + args,
        capture_output=True, text=True, timeout=timeout,
    )
    return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()


def _push_file(serial: str, local: str, remote: str, timeout: int = 60) -> bool:
    """推送单个文件到设备，返回是否成功。"""
    rc, out, err = _run_adb(serial, ["push", local, remote], timeout=timeout)
    return rc == 0


def _shell(serial: str, cmd: str, timeout: int = 30) -> tuple[int, str]:
    """执行 adb shell 命令，返回 (returncode, stdout)。"""
    rc, out, err = _run_adb(serial, ["shell", cmd], timeout=timeout)
    return rc, out


def _get_device_model(serial: str) -> str:
    """获取设备型号。"""
    _, model = _shell(serial, "getprop ro.product.model", timeout=10)
    return model.strip()


# ── 主流程 ──

def main():
    serial = device_serial()
    args = params()

    aimonkey_dir = _resolve_aimonkey_dir(args)
    if not aimonkey_dir.is_dir():
        output_result(False, error_message=f"AIMonkeyTest dir not found: {aimonkey_dir}")
        return

    push_resources = bool(args.get("push_resources", True))
    need_nohup = bool(args.get("need_nohup", True))
    blacklist = bool(args.get("blacklist", True))
    is_sleep = bool(args.get("sleep_mode", False))
    play_video = bool(args.get("play_video", False))
    memory_rw = bool(args.get("memory_rw", False))

    t0 = time.time()
    errors = []

    # ── 0. 设备预检 ──
    rc, _ = _shell(serial, "echo ready", timeout=10)
    if rc != 0:
        output_result(False, error_message=f"Device {serial} not reachable")
        return

    # ── 1. 获取设备型号 ──
    device_model = _get_device_model(serial)

    # ── 2. 推送 monkey 二进制 (aim / aimwd / aim.jar / arm64-v8a / armeabi-v7a) ──
    for name in ["aim", "aim.jar"]:
        local = str(aimonkey_dir / name)
        if os.path.exists(local):
            if not _push_file(serial, local, f"/data/local/tmp/{name}"):
                errors.append(f"push failed: {name}")

    # 推送架构库目录
    for arch_dir in ["arm64-v8a", "armeabi-v7a"]:
        local_arch = aimonkey_dir / arch_dir
        if local_arch.is_dir():
            if not _push_file(serial, str(local_arch), "/data/local/tmp/"):
                errors.append(f"push failed: {arch_dir}")

    # 推送 aimwd 守护进程目录
    local_aimwd = aimonkey_dir / "aimwd"
    if local_aimwd.is_dir():
        if not _push_file(serial, str(local_aimwd), "/data/local/tmp/"):
            errors.append(f"push failed: aimwd")

    # 推送 aimonkey.apk → monkey.apk
    local_apk = str(aimonkey_dir / "aimonkey.apk")
    if os.path.exists(local_apk):
        if not _push_file(serial, local_apk, "/data/local/tmp/monkey.apk"):
            errors.append("push failed: aimonkey.apk")

    # 设置权限
    for f in ["aim", "aimwd"]:
        _shell(serial, f"chmod 777 /data/local/tmp/{f}", timeout=10)

    # ── 3. 推送 MonkeyTest 看门狗脚本 ──
    _shell(serial, "mkdir -p /sdcard/systeminfo", timeout=10)

    if is_sleep:
        _push_file(serial, str(aimonkey_dir / "blacklist.txt"), "/sdcard/blacklist.txt")
        _push_file(serial, str(aimonkey_dir / "offlinemonkey.sh"), "/data/local/tmp/MonkeyTest.sh")
    else:
        _push_file(serial, str(aimonkey_dir / "blacklist.txt"), "/sdcard/blacklist.txt")

        if play_video or memory_rw:
            _push_file(serial, str(aimonkey_dir / "MonkeyTestAi_PlayVideo_MemoryRW.sh"),
                       "/data/local/tmp/MonkeyTest.sh")
        elif "AD11" in device_model:
            _push_file(serial, str(aimonkey_dir / "MonkeyTestAiAD11.sh"),
                       "/data/local/tmp/MonkeyTest.sh")
            if not os.path.exists(str(aimonkey_dir / "MonkeyTestAiAD11.sh")):
                # 回退到通用脚本
                _push_file(serial, str(aimonkey_dir / "MonkeyTestAi.sh"),
                           "/data/local/tmp/MonkeyTest.sh")
        else:
            _push_file(serial, str(aimonkey_dir / "MonkeyTestAi.sh"),
                       "/data/local/tmp/MonkeyTest.sh")

    # ── 4. 推送媒体资源 ──
    if push_resources:
        resource_dir = aimonkey_dir / "resource"
        if resource_dir.is_dir():
            _shell(serial, "mkdir -p /sdcard/resource", timeout=10)
            pushed_count = 0
            for root, _dirs, files in os.walk(resource_dir):
                for f in files:
                    local = os.path.join(root, f)
                    if _push_file(serial, local, f"/sdcard/resource/{f}", timeout=300):
                        pushed_count += 1
            print(f"[monkey_test] 已推送 {pushed_count} 个媒体资源", flush=True)

    # ── 5. 推送 play_video / memory_rw 相关文件 ──
    if play_video:
        _push_file(serial, str(aimonkey_dir / "monkey_video.3gpp"), "/sdcard/monkey_video.3gpp")
    if memory_rw:
        _push_file(serial, str(aimonkey_dir / "monkey_rw_test_64bit"), "/data/local/tmp/monkey_rw_test_64bit")
        _shell(serial, "chmod 777 /data/local/tmp/monkey_rw_test_64bit", timeout=10)

    # ── 6. 启动 Monkey 测试 ──
    if need_nohup:
        cmd = "cd /data/local/tmp; nohup sh /data/local/tmp/MonkeyTest.sh >/dev/null 2>&1 &"
        rc, _ = _shell(serial, cmd, timeout=60)
    else:
        cmd = "nohup sh /data/local/tmp/MonkeyTest.sh >/dev/null 2>&1 &"
        rc, _ = _shell(serial, cmd, timeout=60)

    if rc != 0:
        errors.append(f"MonkeyTest.sh start failed (rc={rc})")

    # 启动 aimwd 守护进程
    _shell(serial, "nohup /data/local/tmp/aimwd >/dev/null 2>&1 &", timeout=15)

    # ── 7. 确认 monkey 进程已启动 ──
    time.sleep(3)
    _, ps_out = _shell(serial, "ps -ef | grep monkey | grep -v grep | head -5", timeout=15)
    monkey_running = "monkey" in ps_out.lower()

    elapsed = round(time.time() - t0, 1)

    if errors:
        output_result(
            False,
            error_message="; ".join(errors),
            metrics={
                "serial": serial,
                "device_model": device_model,
                "aimonkey_dir": str(aimonkey_dir),
                "duration_s": elapsed,
                "monkey_running": monkey_running,
            },
        )
    else:
        output_result(
            True,
            metrics={
                "serial": serial,
                "device_model": device_model,
                "aimonkey_dir": str(aimonkey_dir),
                "duration_s": elapsed,
                "monkey_running": monkey_running,
                "push_resources": push_resources,
                "blacklist": blacklist,
            },
        )


if __name__ == "__main__":
    main()
