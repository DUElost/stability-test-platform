"""Monkey check: verify monkey process is alive after launch.

Gate script — confirms monkey and watchdog processes exist before
proceeding to formal run phase.

Environment:
    STP_DEVICE_SERIAL    (required)
    STP_ADB_PATH         (default: adb)
    STP_STEP_PARAMS      (optional JSON)

STP_STEP_PARAMS:
{
    "process_names": ["com.android.commands.monkey"],
    "watchdog_script": "MonkeyTest.sh",
    "log_path": "/sdcard/Monkeylog.txt",
    "max_wait_seconds": 30,
    "retry_interval": 3,
    "check_log_growth": true
}

Output (stdout):
    {"success": true/false, "monkey_pid": "12345", "watchdog_pid": "...", "metrics": {...}}
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from _adb import adb_path, adb_shell, adb_shell_quiet, device_serial, output_result, params


def _ps_grep(serial: str, pattern: str, timeout: int = 10) -> list[dict]:
    """Search for processes matching pattern, return list of {pid, name, line}."""
    result = subprocess.run(
        [adb_path(), "-s", serial, "shell", f"ps -ef"],
        capture_output=True, text=True, timeout=timeout,
    )
    matches = []
    for line in (result.stdout or "").splitlines():
        if pattern in line and "grep" not in line:
            parts = line.split()
            if len(parts) >= 2:
                matches.append({
                    "pid": parts[1],
                    "name": parts[-1] if len(parts) > 7 else " ".join(parts[7:]),
                    "line": line.strip(),
                })
    return matches


def _file_stat(serial: str, path: str, timeout: int = 10) -> dict:
    """Get file stats via adb shell stat."""
    try:
        out = adb_shell(f"stat -c '%s|%Y' {path} 2>/dev/null || echo ''", timeout=timeout)
        out = out.strip()
        if not out:
            return {"exists": False, "size_bytes": 0, "mtime": 0}
        parts = out.split("|")
        return {
            "exists": True,
            "size_bytes": int(parts[0]) if parts[0].isdigit() else 0,
            "mtime": int(parts[1]) if len(parts) > 1 and parts[1].strip().isdigit() else 0,
        }
    except Exception:
        return {"exists": False, "size_bytes": 0, "mtime": 0}


def _kill_duplicate_monkeys(serial: str, pids: list[str], keep_first: bool = True):
    """Kill duplicate monkey processes, keeping only the first one."""
    killed = []
    start = 1 if keep_first else 0
    for pid in pids[start:]:
        try:
            adb_shell(f"kill -9 {pid}", timeout=5)
            killed.append(pid)
        except Exception:
            pass
    return killed


def check_monkey_alive(serial: str, process_names: list[str]) -> dict:
    """Check if monkey process is running on device."""
    result = {"alive": False, "processes": {}, "duplicate_pids": []}
    all_pids = []

    for name in process_names:
        matches = _ps_grep(serial, name)
        if matches:
            result["processes"][name] = matches
            result["alive"] = True
            for m in matches:
                all_pids.append(m["pid"])

    # Detect duplicates (more than 1 monkey process)
    monkey_matches = result["processes"].get("com.android.commands.monkey", [])
    if len(monkey_matches) > 1:
        result["duplicate_pids"] = [m["pid"] for m in monkey_matches[1:]]
        result["monkey_pid"] = monkey_matches[0]["pid"]
    elif len(monkey_matches) == 1:
        result["monkey_pid"] = monkey_matches[0]["pid"]

    return result


def main():
    serial = device_serial()
    args = params()

    process_names = args.get("process_names", ["com.android.commands.monkey"])
    watchdog_pattern = args.get("watchdog_script", "MonkeyTest.sh")
    log_path = args.get("log_path", "/sdcard/Monkeylog.txt")
    max_wait = args.get("max_wait_seconds", 30)
    retry_interval = args.get("retry_interval", 3)
    check_log = args.get("check_log_growth", True)

    t0 = time.time()
    deadline = t0 + max_wait

    last_log_size = 0
    monkey_alive = False
    final_status = {}

    while time.time() < deadline:
        # Check monkey process
        status = check_monkey_alive(serial, process_names + [watchdog_pattern])

        # Verify log file
        log_stat = _file_stat(serial, log_path) if check_log else {"exists": True, "size_bytes": -1}

        # All checks
        monkey_ok = status["alive"]
        watchdog_ok = bool(status["processes"].get(watchdog_pattern))
        log_ok = log_stat["exists"] and (log_stat["size_bytes"] > 0 or not check_log)
        log_growing = log_stat["size_bytes"] > last_log_size if check_log else True

        if monkey_ok:
            # Kill duplicates if found
            if status.get("duplicate_pids"):
                _kill_duplicate_monkeys(serial, status["duplicate_pids"])
                # Remove duplicates from status after killing
                status["killed_duplicates"] = status.pop("duplicate_pids")

        final_status = {
            "monkey_alive": monkey_ok,
            "watchdog_alive": watchdog_ok,
            "log_exists": log_stat["exists"],
            "log_size_bytes": log_stat["size_bytes"],
            "monkey_pid": status.get("monkey_pid", ""),
            "monkey_count": len(status["processes"].get("com.android.commands.monkey", [])),
        }

        if monkey_ok and watchdog_ok and log_ok and log_growing:
            elapsed = round(time.time() - t0, 1)
            output_result(
                True,
                monkey_pid=final_status["monkey_pid"],
                metrics={
                    "serial": serial,
                    "monkey_alive": True,
                    "watchdog_alive": True,
                    "log_size_bytes": log_stat["size_bytes"],
                    "monkey_count": final_status["monkey_count"],
                    "check_duration_s": elapsed,
                    "retries": int((elapsed - retry_interval) / retry_interval) + 1 if elapsed > retry_interval else 1,
                },
            )
            return

        time.sleep(retry_interval)
        last_log_size = log_stat["size_bytes"]

    # Timeout: monkey not confirmed
    elapsed = round(time.time() - t0, 1)
    output_result(
        False,
        error_message=f"Monkey check timed out after {elapsed}s: monkey={final_status.get('monkey_alive')} "
                       f"watchdog={final_status.get('watchdog_alive')} log_exists={final_status.get('log_exists')}",
        metrics=final_status,
    )


if __name__ == "__main__":
    main()
