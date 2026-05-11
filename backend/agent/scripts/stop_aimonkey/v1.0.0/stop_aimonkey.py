"""Stop AIMonkey: terminate all monkey-related processes on device.

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

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from _adb import adb_path, adb_shell, adb_shell_quiet, device_serial, output_result, params


def _ps_grep(serial: str, patterns: list[str], timeout: int = 10) -> list[dict]:
    """Search for processes matching any pattern, return list of {pid, name, line}."""
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
    return matches


def _kill_pids(serial: str, pids: list[str], timeout: int = 10) -> list[str]:
    """Kill processes by PID."""
    killed = []
    for pid in pids:
        try:
            adb_shell(f"kill -9 {pid}", timeout=timeout)
            killed.append(pid)
        except Exception:
            pass
    return killed


def _force_stop_packages(serial: str, packages: list[str]) -> list[str]:
    """Force-stop Android packages."""
    stopped = []
    for pkg in packages:
        try:
            adb_shell(f"am force-stop {pkg}", timeout=5).strip()
            stopped.append(pkg)
        except Exception:
            pass
    return stopped


def _run_device_stop_script(serial: str) -> dict:
    """Try to run stopAIMonkey.py on device if it exists."""
    paths = [
        "/data/local/tmp/stopAIMonkey.py",
        "/sdcard/stopAIMonkey.py",
        "/data/local/tmp/scripts/stopAIMonkey.py",
    ]
    for path in paths:
        try:
            check = adb_shell(f"test -f {path} && echo EXISTS || echo MISSING", timeout=5)
            if "EXISTS" in check:
                out = adb_shell(f"python3 {path} 2>&1 || python {path} 2>&1", timeout=30)
                return {"ran": True, "path": path, "output": out.strip()[:500]}
        except Exception:
            continue
    return {"ran": False, "reason": "stopAIMonkey.py not found on device"}


def main():
    serial = device_serial()
    args = params()

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

    t0 = time.time()
    metrics = {
        "killed_pids": [],
        "stopped_packages": [],
        "device_script": None,
        "extra_cmds": [],
        "errors": [],
    }

    matches = _ps_grep(serial, patterns)
    if matches:
        pids = list(set(m["pid"] for m in matches))
        killed = _kill_pids(serial, pids)
        metrics["killed_pids"] = killed

    stopped = _force_stop_packages(serial, force_stop)
    metrics["stopped_packages"] = stopped

    for cmd in extra_cmds:
        try:
            adb_shell(cmd, timeout=5)
            metrics["extra_cmds"].append(cmd)
        except Exception as exc:
            metrics["errors"].append(f"cmd {cmd}: {exc}")

    if args.get("run_device_script", True):
        metrics["device_script"] = _run_device_stop_script(serial)

    time.sleep(1)
    remaining = _ps_grep(serial, ["com.android.commands.monkey"])
    all_clear = len(remaining) == 0

    elapsed = round(time.time() - t0, 1)
    output_result(
        all_clear,
        metrics={
            **metrics,
            "duration_s": elapsed,
            "monkey_processes_found": len(matches),
            "monkey_processes_remaining": len(remaining),
        },
    )


if __name__ == "__main__":
    main()
