"""Monkey 测试停止与数据回收脚本。

停止设备上的 monkey 进程，拉取日志和系统信息。

环境变量:
    STP_DEVICE_SERIAL    (required)
    STP_ADB_PATH         (default: adb)
    STP_LOG_DIR          (default: /tmp) 产物存放目录
    STP_STEP_PARAMS      (optional JSON)

STP_STEP_PARAMS 结构:
{
    "process_names": ["com.android.commands.monkey.transsion", "com.android.commands.monkey",
                      "/data/local/tmp/MonkeyTest.sh"],
    "pull_paths": [
        {"device": "/sdcard/Monkeylog.txt", "local_name": "monkey_log.txt"},
        {"device": "/sdcard/systeminfo", "local_name": "systeminfo"},
        {"device": "/data/aee_exp", "local_name": "aee_exp"}
    ],
    "clear_logs": true
}

输出 (stdout):
    {"success": true/false, "metrics": {...}}
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from _adb import adb_path, adb_shell, adb_shell_quiet, device_serial, output_result, params


def _pull_dir(serial: str, device_path: str, local_dir: Path):
    """Pull a directory from device using adb pull."""
    local_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [adb_path(), "-s", serial, "pull", device_path, str(local_dir)],
        capture_output=True, text=True, timeout=120,
    )


def _pull_file(serial: str, device_path: str, local_path: Path):
    """Pull a single file from device."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [adb_path(), "-s", serial, "pull", device_path, str(local_path)],
        capture_output=True, text=True, timeout=60,
    )


def _kill_processes(serial: str, names: list[str]) -> dict:
    """Kill named processes on device."""
    killed = []
    try:
        result = subprocess.run(
            [adb_path(), "-s", serial, "shell", "ps -ef"],
            capture_output=True, text=True, timeout=10,
        )
        ps_output = result.stdout or ""
    except Exception:
        ps_output = ""

    for name in names:
        for line in ps_output.splitlines():
            if name in line:
                parts = line.split()
                if len(parts) >= 2:
                    pid = parts[1]
                    try:
                        adb_shell(f"kill -9 {pid}", timeout=10)
                        killed.append({"name": name, "pid": pid})
                    except Exception:
                        pass

    # Also force-stop monkey
    if "monkey" in str(names).lower():
        try:
            adb_shell("setprop sys.audio.monkeycontrl 0", timeout=5)
        except Exception:
            pass
        for pkg in ["com.android.commands.monkey", "com.transsion.MkWatchdog"]:
            try:
                adb_shell(f"am force-stop {pkg}", timeout=5)
            except Exception:
                pass

    return {"killed": killed}


def _clear_aee(serial: str) -> dict:
    """Clear AEE core dump properties."""
    props = [
        "persist.aee.core.dump",
        "persist.aee.core.direct",
    ]
    for p in props:
        try:
            adb_shell(f"setprop {p} disable", timeout=5)
        except Exception:
            pass
    return {"aee_disabled": True}


def main():
    serial = device_serial()
    args = params()

    log_dir = Path(os.environ.get("STP_LOG_DIR", "/tmp")).resolve()
    run_dir = log_dir / f"monkey_teardown_{serial}_{int(time.time())}"
    run_dir.mkdir(parents=True, exist_ok=True)

    process_names = args.get("process_names", [
        "com.android.commands.monkey.transsion",
        "com.android.commands.monkey",
        "/data/local/tmp/MonkeyTest.sh",
        "offlinemonkey.sh",
        "com.transsion.MkWatchdog",
    ])

    pull_paths = args.get("pull_paths", [
        {"device": "/sdcard/Monkeylog.txt", "local_name": "monkey_log.txt"},
        {"device": "/sdcard/systeminfo", "local_name": "systeminfo"},
        {"device": "/sdcard/Auto", "local_name": "auto_logs"},
    ])

    t0 = time.time()
    results = {}

    # 1. Kill processes
    results["killed"] = _kill_processes(serial, process_names)

    # 2. Wait briefly for processes to exit
    time.sleep(2)

    # 3. Pull logs
    pulled = []
    for entry in pull_paths:
        device_path = entry["device"]
        local_name = entry.get("local_name", Path(device_path).name)
        local_path = run_dir / local_name
        try:
            if entry.get("is_dir", True):
                _pull_dir(serial, device_path, local_path)
            else:
                _pull_file(serial, device_path, local_path)
            pulled.append({"device": device_path, "local": str(local_path)})
        except Exception as exc:
            pulled.append({"device": device_path, "error": str(exc)})
    results["pulled"] = pulled

    # 4. Pull AEE crash logs if available
    aee_paths = ["/data/aee_exp", "/data/vendor/mtklog/aee_exp"]
    for ap in aee_paths:
        local = run_dir / Path(ap).name
        try:
            _pull_dir(serial, ap, local)
        except Exception:
            pass

    # 5. Disable AEE props
    if args.get("clear_aee", True):
        results["aee"] = _clear_aee(serial)

    # 6. Clean up device
    if args.get("cleanup", False):
        try:
            adb_shell("rm -rf /data/local/tmp/MonkeyTest.sh", timeout=5)
            adb_shell("rm -rf /data/local/tmp/offlinemonkey.sh", timeout=5)
        except Exception:
            pass

    elapsed = round(time.time() - t0, 1)
    output_result(
        True,
        metrics={
            "serial": serial,
            "artifacts_dir": str(run_dir),
            "killed_count": len(results["killed"].get("killed", [])),
            "pulled_count": len([p for p in results["pulled"] if "error" not in p]),
            "duration_s": elapsed,
        },
    )


if __name__ == "__main__":
    main()
