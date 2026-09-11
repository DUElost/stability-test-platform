"""Monkey 测试停止与数据回收脚本。

v1.0.1（#807）：设备可达预检 + 全链路 rc 判定——pull/kill/force-stop 失败记
errors 并 output_result(False)（附已发生清单），设备离线不再假全绿；
默认 pull 列表的 /sdcard/Auto（仅 offlinemonkey 睡眠模式创建）标记 optional。

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
        {"device": "/sdcard/systeminfo", "local_name": "systeminfo"}
    ],
    "clear_logs": true
}

输出 (stdout):
    {"success": true/false, "error_message": "...", "metrics": {...}}
"""

import os
import subprocess
import time
from pathlib import Path

from _adb import adb_path, adb_shell_quiet, device_serial, output_result, params


def _run_adb(serial: str, args: list[str], timeout: int = 60) -> int:
    """执行 adb 命令返回 returncode（供 pull / ps 等 rc 判定）。"""
    result = subprocess.run(
        [adb_path(), "-s", serial] + args,
        capture_output=True, text=True, timeout=timeout,
    )
    return result.returncode


def _pull_dir(serial: str, device_path: str, local_dir: Path) -> bool:
    """Pull a directory from device using adb pull. 返回 rc==0。"""
    local_dir.mkdir(parents=True, exist_ok=True)
    return _run_adb(serial, ["pull", device_path, str(local_dir)], timeout=120) == 0


def _pull_file(serial: str, device_path: str, local_path: Path) -> bool:
    """Pull a single file from device. 返回 rc==0。"""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    return _run_adb(serial, ["pull", device_path, str(local_path)], timeout=60) == 0


def _kill_processes(serial: str, names: list[str]) -> dict:
    """Kill named processes on device（rc 判定，失败记 errors）。"""
    killed = []
    errors = []
    try:
        ps = subprocess.run(
            [adb_path(), "-s", serial, "shell", "ps -ef"],
            capture_output=True, text=True, timeout=10,
        )
        if ps.returncode != 0:
            errors.append(f"ps -ef failed: rc={ps.returncode}")
            ps_output = ""
        else:
            ps_output = ps.stdout or ""
    except Exception as exc:
        errors.append(f"ps -ef exception: {exc}")
        ps_output = ""

    for name in names:
        for line in ps_output.splitlines():
            if name in line:
                parts = line.split()
                if len(parts) >= 2:
                    pid = parts[1]
                    result = adb_shell_quiet(f"kill -9 {pid}", timeout=10)
                    if result.returncode == 0:
                        killed.append({"name": name, "pid": pid})
                    else:
                        errors.append(f"kill -9 {pid} ({name}): rc={result.returncode}")

    # Also force-stop monkey
    if "monkey" in str(names).lower():
        adb_shell_quiet("setprop sys.audio.monkeycontrl 0", timeout=5)
        for pkg in ["com.android.commands.monkey", "com.transsion.MkWatchdog"]:
            result = adb_shell_quiet(f"am force-stop {pkg}", timeout=5)
            if result.returncode != 0:
                errors.append(f"force-stop {pkg}: rc={result.returncode}")

    return {"killed": killed, "errors": errors}


def _clear_aee(serial: str) -> dict:
    """Clear AEE core dump properties."""
    props = [
        "persist.aee.core.dump",
        "persist.aee.core.direct",
    ]
    for p in props:
        adb_shell_quiet(f"setprop {p} disable", timeout=5)
    return {"aee_disabled": True}


def main():
    serial = device_serial()
    args = params()

    log_dir = Path(os.environ.get("STP_LOG_DIR", "/tmp")).resolve()
    run_dir = log_dir / f"monkey_teardown_{serial}_{int(time.time())}"
    run_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    errors = []

    # 0. 设备可达硬预检（离线/断连窗口不得进入 rc 盲拉取路径）
    if adb_shell_quiet("echo ready", timeout=10).returncode != 0:
        output_result(False, error_message=f"Device {serial} not reachable")
        return

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
        # 仅 offlinemonkey 睡眠模式创建：缺失不计失败（#807）
        {"device": "/sdcard/Auto", "local_name": "auto_logs", "optional": True},
    ])

    results = {}

    # 1. Kill processes
    results["killed"] = _kill_processes(serial, process_names)
    errors.extend(results["killed"].get("errors", []))

    # 2. Wait briefly for processes to exit
    time.sleep(2)

    # 3. Pull logs（rc 判定；optional 项缺失跳过不计失败）
    pulled = []
    for entry in pull_paths:
        device_path = entry["device"]
        local_name = entry.get("local_name", Path(device_path).name)
        local_path = run_dir / local_name
        try:
            if entry.get("is_dir", True):
                ok = _pull_dir(serial, device_path, local_path)
            else:
                ok = _pull_file(serial, device_path, local_path)
            if ok:
                pulled.append({"device": device_path, "local": str(local_path)})
            elif entry.get("optional"):
                pulled.append({"device": device_path, "skipped": "absent"})
            else:
                pulled.append({"device": device_path, "error": "adb pull rc != 0"})
                errors.append(f"pull failed: {device_path}")
        except Exception as exc:
            pulled.append({"device": device_path, "error": str(exc)})
            errors.append(f"pull exception: {device_path}: {exc}")
    results["pulled"] = pulled

    # 4. Pull AEE crash logs if available（best-effort：路径二选一存在）
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
        adb_shell_quiet("rm -rf /data/local/tmp/MonkeyTest.sh", timeout=5)
        adb_shell_quiet("rm -rf /data/local/tmp/offlinemonkey.sh", timeout=5)

    elapsed = round(time.time() - t0, 1)
    metrics = {
        "serial": serial,
        "artifacts_dir": str(run_dir),
        "killed_count": len(results["killed"].get("killed", [])),
        "pulled_count": len([p for p in results["pulled"] if "error" not in p]),
        "duration_s": elapsed,
    }

    if errors:
        output_result(False, error_message="; ".join(errors), metrics=metrics)
        return

    output_result(True, metrics=metrics)


if __name__ == "__main__":
    main()
