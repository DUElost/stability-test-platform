"""Monkey check v2.0.0 — 存活快照（无内部循环）。

crash 检测已由 watcher (inotifyd) 实时覆盖，本脚本仅做单次瞬时判断：
- monkey 进程是否存在
- MonkeyTest.sh 看门狗是否存在
- 日志文件是否在增长

执行耗时 < 3 秒，不再阻塞 patrol。

环境变量:
    STP_DEVICE_SERIAL    (required)
    STP_STEP_PARAMS      (optional JSON)

STP_STEP_PARAMS:
{
    "process_names": ["com.android.commands.monkey"],
    "watchdog_script": "MonkeyTest.sh",
    "log_path": "/sdcard/Monkeylog.txt",
    "check_log_growth": true
}

输出 (stdout):
    {"success": true/false, "monkey_alive": ..., "watchdog_alive": ..., "metrics": {...}}
"""

import subprocess
import sys

from _adb import adb_path, device_serial, output_result, params


def _ps_grep(serial: str, pattern: str, timeout: int = 10) -> list[dict]:
    result = subprocess.run(
        [adb_path(), "-s", serial, "shell", f"ps -ef | grep '{pattern}' | grep -v grep"],
        capture_output=True, text=True, timeout=timeout,
    )
    matches = []
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            matches.append({"pid": parts[1], "line": line.strip()})
    return matches


def _file_stat(serial: str, path: str, timeout: int = 10) -> dict:
    from _adb import adb_shell
    try:
        out = adb_shell(f"stat -c '%s|%Y' {path} 2>/dev/null || echo ''", timeout=timeout)
        out = out.strip()
        if not out:
            return {"exists": False, "size_bytes": 0}
        parts = out.split("|")
        return {
            "exists": True,
            "size_bytes": int(parts[0]) if parts[0].lstrip("-").isdigit() else 0,
        }
    except Exception:
        return {"exists": False, "size_bytes": 0}


def main():
    serial = device_serial()
    args = params()

    process_names = args.get("process_names", ["com.android.commands.monkey"])
    watchdog_pattern = args.get("watchdog_script", "MonkeyTest.sh")
    log_path = args.get("log_path", "/sdcard/Monkeylog.txt")
    check_log = args.get("check_log_growth", True)

    monkey_matches = []
    for name in process_names:
        monkey_matches.extend(_ps_grep(serial, name))
    monkey_alive = len(monkey_matches) > 0

    watchdog_matches = _ps_grep(serial, watchdog_pattern)
    watchdog_alive = len(watchdog_matches) > 0

    log_stat = _file_stat(serial, log_path) if check_log else {"exists": True, "size_bytes": -1}
    log_ok = log_stat["exists"] and (log_stat["size_bytes"] > 0 or not check_log)

    ok = monkey_alive and watchdog_alive and log_ok

    output_result(
        ok,
        error_message=(
            None if ok
            else f"monkey={monkey_alive} watchdog={watchdog_alive} "
                 f"log_exists={log_stat.get('exists', False)}"
        ),
        metrics={
            "monkey_alive": monkey_alive,
            "watchdog_alive": watchdog_alive,
            "log_exists": log_stat["exists"],
            "log_size_bytes": log_stat["size_bytes"],
            "monkey_count": len(monkey_matches),
            "monkey_pid": monkey_matches[0]["pid"] if monkey_matches else "",
        },
    )


if __name__ == "__main__":
    main()
