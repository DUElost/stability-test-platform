"""Monkey 资源推送：将 AIMonkey 二进制 + 看门狗 + 黑名单推送至设备。

仅做资源部署，不启动 monkey 进程。post-check 验证所有关键文件已就位。

环境变量:
    STP_DEVICE_SERIAL      (required)
    STP_STEP_PARAMS        (optional JSON)

STP_STEP_PARAMS:
{
    "push_resources": true,
    "aimonkey_dir": "/opt/stability-test-agent/agent/resources/aimonkey/AIMonkeyTest_20260317"
}

输出 (stdout):
    {"success": true/false, "metrics": {...}}
"""

import os
import subprocess
import sys
import time
from pathlib import Path

from _adb import adb_path, device_serial, output_result, params

_AGENT_ROOT = Path(__file__).resolve().parents[3]
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from aimonkey_paths import resolve_aimonkey_bundle_dir  # noqa: E402


def _resolve_aimonkey_dir(cfg: dict) -> Path:
    return resolve_aimonkey_bundle_dir(cfg)


def _run_adb(serial: str, args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    result = subprocess.run(
        [adb_path(), "-s", serial] + args,
        capture_output=True, text=True, timeout=timeout,
    )
    return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()


def _push(serial: str, local: str, remote: str, timeout: int = 60) -> bool:
    if not os.path.exists(local):
        return False
    rc, _, _ = _run_adb(serial, ["push", local, remote], timeout=timeout)
    return rc == 0


def _shell(serial: str, cmd: str, timeout: int = 30) -> tuple[int, str]:
    rc, out, _ = _run_adb(serial, ["shell", cmd], timeout=timeout)
    return rc, out


def _file_exists(serial: str, path: str) -> bool:
    _, out = _shell(serial, f"test -f {path} && echo YES || echo NO", timeout=10)
    return "YES" in out


def _file_size(serial: str, path: str) -> int:
    _, out = _shell(serial, f"stat -c '%s' {path} 2>/dev/null || echo 0", timeout=10)
    try:
        return int(out.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0


def main():
    serial = device_serial()
    args = params()

    aimonkey_dir = _resolve_aimonkey_dir(args)
    if not aimonkey_dir.is_dir():
        output_result(False, error_message=f"AIMonkeyTest dir not found: {aimonkey_dir}")
        sys.exit(1)

    push_resources = bool(args.get("push_resources", True))

    t0 = time.time()
    required_files: list[tuple[str, str]] = []  # (local_name, remote_path)
    errors: list[str] = []

    # ── 必需文件 ──
    # aim 启动脚本
    local_aim = str(aimonkey_dir / "aim")
    if os.path.exists(local_aim):
        required_files.append(("aim", "/data/local/tmp/aim"))
    else:
        errors.append("aim not found in resources")

    # aimonkey.apk → monkey.apk
    local_apk = str(aimonkey_dir / "aimonkey.apk")
    if os.path.exists(local_apk):
        required_files.append(("aimonkey.apk", "/data/local/tmp/monkey.apk"))
    else:
        errors.append("aimonkey.apk not found")

    # aim.jar
    local_jar = str(aimonkey_dir / "aim.jar")
    if os.path.exists(local_jar):
        required_files.append(("aim.jar", "/data/local/tmp/aim.jar"))
    else:
        errors.append("aim.jar not found")

    # MonkeyTest.sh 看门狗
    local_sh = str(aimonkey_dir / "MonkeyTestAi.sh")
    if os.path.exists(local_sh):
        required_files.append(("MonkeyTestAi.sh", "/data/local/tmp/MonkeyTest.sh"))
    else:
        errors.append("MonkeyTestAi.sh not found")

    # blacklist.txt
    local_bl = str(aimonkey_dir / "blacklist.txt")
    if os.path.exists(local_bl):
        required_files.append(("blacklist.txt", "/sdcard/blacklist.txt"))

    if errors:
        output_result(False, error_message="missing resources: " + "; ".join(errors))
        sys.exit(1)

    # ── 预检设备连通性 ──
    rc, _ = _shell(serial, "echo ready", timeout=10)
    if rc != 0:
        output_result(False, error_message=f"Device {serial} not reachable")
        sys.exit(1)

    # ── 推送所有必需文件 ──
    pushed = 0
    for local_name, remote_path in required_files:
        local_path = str(aimonkey_dir / local_name)
        if _push(serial, local_path, remote_path):
            pushed += 1
        else:
            errors.append(f"push failed: {local_name}")

    # 设置权限
    _shell(serial, "chmod 755 /data/local/tmp/aim", timeout=10)

    # ── 可选：架构库 + aimwd + 媒体资源 ──
    for arch_dir in ["arm64-v8a", "armeabi-v7a"]:
        local_arch = aimonkey_dir / arch_dir
        if local_arch.is_dir():
            _push(serial, str(local_arch), "/data/local/tmp/")

    local_aimwd = aimonkey_dir / "aimwd"
    if local_aimwd.is_dir():
        _push(serial, str(local_aimwd), "/data/local/tmp/")
        _shell(serial, "chmod 777 /data/local/tmp/aimwd", timeout=10)

    # 媒体资源
    if push_resources:
        resource_dir = aimonkey_dir / "resource"
        if resource_dir.is_dir():
            _shell(serial, "mkdir -p /sdcard/resource", timeout=10)
            for root, _dirs, files in os.walk(resource_dir):
                for f in files:
                    _push(serial, os.path.join(root, f), f"/sdcard/resource/{f}", timeout=300)

    _shell(serial, "mkdir -p /sdcard/systeminfo", timeout=10)

    # ── Post-check: 验证关键文件已就位 ──
    check_errors = []
    for local_name, remote_path in required_files:
        if not _file_exists(serial, remote_path):
            check_errors.append(f"missing: {remote_path}")
        else:
            size = _file_size(serial, remote_path)
            if size == 0:
                check_errors.append(f"zero-size: {remote_path}")

    elapsed = round(time.time() - t0, 1)

    if errors or check_errors:
        output_result(
            False,
            error_message="; ".join(errors + check_errors),
            metrics={"pushed": pushed, "checked": len(required_files) - len(check_errors), "duration_s": elapsed},
        )
        sys.exit(1)
    else:
        output_result(
            True,
            metrics={"pushed": pushed, "all_verified": True, "duration_s": elapsed},
        )


if __name__ == "__main__":
    main()
