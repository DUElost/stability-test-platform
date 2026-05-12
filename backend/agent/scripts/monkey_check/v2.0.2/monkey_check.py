"""Monkey check v2.0.2 — 存活快照 + 自动重启。

crash 检测已由 watcher (inotifyd) 实时覆盖。本脚本仅做单次瞬时判断：
- monkey 进程是否存在
- MonkeyTest.sh 看门狗是否存在

若 monkey 不在但设备可达，自动重启 MonkeyTest.sh（不重新推送资源），
返回成功。稳定性测试不应因 monkey 进程短暂消失而终止。

仅设备不可达时才返回失败。

环境变量:
    STP_DEVICE_SERIAL      (required)
    STP_STEP_PARAMS        (optional JSON)

STP_STEP_PARAMS:
{
    "process_names": ["com.android.commands.monkey"],
    "watchdog_script": "MonkeyTest.sh"
}

输出 (stdout):
    {"success": true/false, "metrics": {...}, "restarted": true/false}
"""

import subprocess
import sys

from _adb import adb_path, device_serial, output_result, params


def _shell(serial: str, cmd: str, timeout: int = 30) -> tuple[int, str]:
    result = subprocess.run(
        [adb_path(), "-s", serial, "shell", cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.returncode, (result.stdout or "").strip()


def _ps_grep(serial: str, pattern: str, timeout: int = 10) -> list[dict]:
    rc, out = _shell(
        serial,
        f"ps -ef | grep '{pattern}' | grep -v grep",
        timeout=timeout,
    )
    matches = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            matches.append({"pid": parts[1], "line": line.strip()})
    return matches


def _restart_watchdog(serial: str) -> bool:
    """Restart MonkeyTest.sh on device. Returns True if shell command accepted."""
    cmd = (
        "cd /data/local/tmp; "
        "nohup sh /data/local/tmp/MonkeyTest.sh >/dev/null 2>&1 &"
    )
    rc, _ = _shell(serial, cmd, timeout=30)
    if rc == 0:
        # Also restart aimwd
        _shell(serial, "nohup /data/local/tmp/aimwd >/dev/null 2>&1 &", timeout=15)
    return rc == 0


def main():
    serial = device_serial()
    args = params()

    process_names = args.get("process_names", ["com.android.commands.monkey"])
    watchdog_pattern = args.get("watchdog_script", "MonkeyTest.sh")

    # ── 预检 ──
    rc, _ = _shell(serial, "echo ready", timeout=10)
    if rc != 0:
        output_result(False, error_message=f"Device {serial} not reachable")
        sys.exit(1)

    # ── 检查 monkey 进程 ──
    monkey_matches = []
    for name in process_names:
        monkey_matches.extend(_ps_grep(serial, name))
    monkey_alive = len(monkey_matches) > 0

    # ── 检查 watchdog ──
    watchdog_matches = _ps_grep(serial, watchdog_pattern)
    watchdog_alive = len(watchdog_matches) > 0

    # ── 自动恢复 ──
    restarted = False
    if not monkey_alive and not watchdog_alive:
        # Watchdog 也不在 → 尝试重启
        ok = _restart_watchdog(serial)
        restarted = ok
        if not ok:
            output_result(
                False,
                error_message="Failed to restart MonkeyTest.sh",
                metrics={"monkey_alive": False, "watchdog_alive": False, "restarted": False},
            )
            sys.exit(1)
        # 重启成功，但 monkey 进程可能还没出来 → 仍报成功
        monkey_alive = False  # 刚重启，还没出进程

    elif not monkey_alive and watchdog_alive:
        # Watchdog 活着但 monkey 不在 → watchdog 应该会自动恢复，不干预
        pass

    # ── 返回 ──
    # 只要设备可连、watchdog 进程能找到（或已重启），就返回成功
    # monkey 进程的具体存活状态作为 metrics 上报，不作为失败条件
    output_result(
        True,  # 永远不因 monkey 不在而失败
        metrics={
            "monkey_alive": monkey_alive,
            "watchdog_alive": watchdog_alive or restarted,
            "monkey_count": len(monkey_matches),
            "monkey_pid": monkey_matches[0]["pid"] if monkey_matches else "",
            "restarted": restarted,
        },
    )


if __name__ == "__main__":
    main()
