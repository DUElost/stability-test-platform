"""Stop AIMonkey: terminate all monkey-related processes on device.

v1.0.1（#807）：设备可达预检 + ps/kill/force-stop 全链路 rc 判定——断连
窗口不再「remaining 查空 → 全绿」；kill / force-stop / extra 命令失败记
errors 并 output_result(False)。

Used as the first teardown step to ensure monkey is fully stopped
before pulling logs or cleaning up.  More aggressive than monkey_teardown
in that it also attempts to run device-side stopAIMonkey.py if present.

Environment:
    STP_DEVICE_SERIAL    (required)
    STP_ADB_PATH         (default: adb)
    STP_STEP_PARAMS      (optional JSON)

STP_STEP_PARAMS:
{
    "process_patterns": ["com.android.commands.monkey", "MonkeyTest.sh",
                         "offlinemonkey.sh", "MkWatchdog"],
    "extra_adb_commands": ["setprop sys.audio.monkeycontrl 0"],
    "force_stop_packages": ["com.android.commands.monkey", "com.transsion.MkWatchdog"],
    "run_device_script": true
}

Output (stdout):
    {"success": true/false, "metrics": {"killed_pids": [...], "errors": [...]}}
"""

import subprocess
import time

from _adb import adb_path, adb_shell_quiet, device_serial, output_result, params


def _ps_grep(serial: str, patterns: list[str], timeout: int = 10) -> tuple[int, list[dict]]:
    """Search processes matching any pattern, return (returncode, [{pid, name, line}])。

    ps 失败返回 rc 非零——调用方不得把「查不到」当成「没有进程」。
    """
    result = subprocess.run(
        [adb_path(), "-s", serial, "shell", "ps -ef || ps -A"],
        capture_output=True, text=True, timeout=timeout,
    )
    matches = []
    for line in (result.stdout or "").splitlines():
        if any(p in line for p in patterns) and "grep" not in line:
            parts = line.split()
            if len(parts) >= 2:
                matches.append(
                    {
                        "pid": parts[1],
                        "name": parts[-1] if len(parts) > 7 else " ".join(parts[7:]),
                        "line": line.strip(),
                    }
                )
    return result.returncode, matches


def _kill_pids(serial: str, pids: list[str], timeout: int = 10) -> tuple[list[str], list[str]]:
    """Kill processes by PID（rc 判定）。返回 (killed, errors)。"""
    killed = []
    errors = []
    for pid in pids:
        result = adb_shell_quiet(f"kill -9 {pid}", timeout=timeout)
        if result.returncode == 0:
            killed.append(pid)
        else:
            errors.append(f"kill -9 {pid}: rc={result.returncode}")
    return killed, errors


def _force_stop_packages(serial: str, packages: list[str]) -> tuple[list[str], list[str]]:
    """Force-stop Android packages（rc 判定）。返回 (stopped, errors)。"""
    stopped = []
    errors = []
    for pkg in packages:
        result = adb_shell_quiet(f"am force-stop {pkg}", timeout=5)
        if result.returncode == 0:
            stopped.append(pkg)
        else:
            errors.append(f"force-stop {pkg}: rc={result.returncode}")
    return stopped, errors


def _run_device_stop_script(serial: str) -> dict:
    """Try to run stopAIMonkey.py on device if it exists."""
    paths = [
        "/data/local/tmp/stopAIMonkey.py",
        "/sdcard/stopAIMonkey.py",
        "/data/local/tmp/scripts/stopAIMonkey.py",
    ]
    for path in paths:
        check = adb_shell_quiet(f"test -f {path} && echo EXISTS || echo MISSING", timeout=5)
        if "EXISTS" in (check.stdout or ""):
            out = adb_shell_quiet(f"python3 {path} 2>&1 || python {path} 2>&1", timeout=30)
            return {"ran": True, "path": path, "output": (out.stdout or "").strip()[:500]}
    return {"ran": False, "reason": "stopAIMonkey.py not found on device"}


def main():
    serial = device_serial()
    args = params()

    t0 = time.time()

    # 0. 设备可达硬预检（离线/断连窗口不得进入「查空 → 全绿」路径）
    if adb_shell_quiet("echo ready", timeout=10).returncode != 0:
        output_result(False, error_message=f"Device {serial} not reachable")
        return

    patterns = args.get(
        "process_patterns",
        [
            "com.android.commands.monkey",
            "MonkeyTest.sh",
            "offlinemonkey.sh",
            "MkWatchdog",
        ],
    )
    force_stop = args.get(
        "force_stop_packages",
        [
            "com.android.commands.monkey",
            "com.transsion.MkWatchdog",
        ],
    )
    extra_cmds = args.get(
        "extra_adb_commands",
        [
            "setprop sys.audio.monkeycontrl 0",
        ],
    )

    metrics = {
        "killed_pids": [],
        "stopped_packages": [],
        "device_script": None,
        "extra_cmds": [],
        "errors": [],
    }

    ps_rc, matches = _ps_grep(serial, patterns)
    if ps_rc != 0:
        metrics["errors"].append(f"ps -ef failed: rc={ps_rc}")

    if matches:
        pids = list(set(m["pid"] for m in matches))
        killed, kill_errors = _kill_pids(serial, pids)
        metrics["killed_pids"] = killed
        metrics["errors"].extend(kill_errors)

    stopped, stop_errors = _force_stop_packages(serial, force_stop)
    metrics["stopped_packages"] = stopped
    metrics["errors"].extend(stop_errors)

    for cmd in extra_cmds:
        result = adb_shell_quiet(cmd, timeout=5)
        if result.returncode == 0:
            metrics["extra_cmds"].append(cmd)
        else:
            metrics["errors"].append(f"cmd {cmd}: rc={result.returncode}")

    if args.get("run_device_script", True):
        metrics["device_script"] = _run_device_stop_script(serial)

    time.sleep(1)
    post_rc, remaining = _ps_grep(serial, ["com.android.commands.monkey"])
    if post_rc != 0:
        metrics["errors"].append(f"post-kill ps failed: rc={post_rc}; cannot verify")
        all_clear = False
    else:
        all_clear = len(remaining) == 0

    success = all_clear and not metrics["errors"]
    elapsed = round(time.time() - t0, 1)
    remaining_count = len(remaining) if post_rc == 0 else -1
    base_metrics = {
        **metrics,
        "duration_s": elapsed,
        "monkey_processes_found": len(matches),
        "monkey_processes_remaining": remaining_count,
    }
    if success:
        output_result(True, metrics=base_metrics)
    else:
        reason = "; ".join(metrics["errors"]) or (
            f"{remaining_count} monkey process(es) remaining"
        )
        output_result(False, error_message=reason, metrics=base_metrics)


if __name__ == "__main__":
    main()
