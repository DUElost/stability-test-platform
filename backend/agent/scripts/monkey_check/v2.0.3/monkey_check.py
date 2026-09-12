"""Monkey check v2.0.3 — 存活快照 + 自动重启。

crash 检测已由 watcher (inotifyd) 实时覆盖。本脚本仅做单次瞬时判断：
- monkey 进程是否存在
- MonkeyTest.sh 看门狗是否存在

若 monkey 不在但设备可达，自动重启 MonkeyTest.sh（不重新推送资源），
返回成功。稳定性测试不应因 monkey 进程短暂消失而终止。

仅设备不可达、或（重启后）看门狗起不来时才返回失败。

v2.0.3（#809）：重启不再"假成功"——nohup 的 shell rc 恒 0（脚本缺失/FBE
未解锁/fork 失败都照样 0），必须在 ≤60s 窗口内轮询 ps 见到 MonkeyTest.sh
**与** MonkeyWatchdog 才算重启成功，超时 output_result(False) exit 1；
monkey 进程检测排除 MonkeyWatchdog（其 cmdline 也含
com.android.commands.monkey，不排除会让"双亡"永远触发不了重启分支）。

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
import time

from _adb import adb_path, device_serial, output_result, params


def _shell(serial: str, cmd: str, timeout: int = 30) -> tuple[int, str]:
    result = subprocess.run(
        [adb_path(), "-s", serial, "shell", cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.returncode, (result.stdout or "").strip()


def _ps_grep(
    serial: str, pattern: str, timeout: int = 10, exclude: str = ""
) -> list[dict]:
    cmd = f"ps -ef | grep '{pattern}' | grep -v grep"
    if exclude:
        cmd += f" | grep -v '{exclude}'"
    rc, out = _shell(serial, cmd, timeout=timeout)
    matches = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            matches.append({"pid": parts[1], "line": line.strip()})
    return matches


def _wait_for_ps(serial: str, pattern: str, deadline: float, interval_s: float) -> bool:
    """在 deadline 前轮询 ps，见到 pattern 即 True（多个 pattern 可共享窗口）。"""
    while True:
        if _ps_grep(serial, pattern):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval_s)


def _restart_watchdog(serial: str, timeout_s: float = 60.0, interval_s: float = 2.0) -> bool:
    """重启 MonkeyTest.sh + aimwd，并在窗口内轮询 ps 验证两个进程确实在跑。

    #809：nohup 异步启动的 shell rc 恒 0（只证明 adb 通——脚本缺失/FBE 未
    解锁/fork 失败都照样 0）——必须在超时窗口内轮询 ps 见到 MonkeyTest.sh
    与 MonkeyWatchdog 才算重启成功，避免"重启假成功"（双亡时每个 patrol
    周期都报绿，数小时无 monkey 被记 healthy）。
    """
    cmd = (
        "cd /data/local/tmp; "
        "nohup sh /data/local/tmp/MonkeyTest.sh >/dev/null 2>&1 &"
    )
    rc, _ = _shell(serial, cmd, timeout=30)
    if rc != 0:
        return False
    # Also restart aimwd
    _shell(serial, "nohup /data/local/tmp/aimwd >/dev/null 2>&1 &", timeout=15)
    deadline = time.monotonic() + timeout_s
    sh_ok = _wait_for_ps(serial, "MonkeyTest.sh", deadline, interval_s)
    aimwd_ok = _wait_for_ps(serial, "MonkeyWatchdog", deadline, interval_s)
    return sh_ok and aimwd_ok


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
    # #809：排除 MonkeyWatchdog——aimwd 的 cmdline 也含
    # com.android.commands.monkey，不排除则它会被当成 monkey 存活证据，
    # 双亡场景永远进不了重启分支
    monkey_matches = []
    for name in process_names:
        monkey_matches.extend(_ps_grep(serial, name, exclude="MonkeyWatchdog"))
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
        # 重启成功 = 两个看门狗进程已在 ps 中出现（_restart_watchdog 内轮询
        # 验证）；monkey 进程本身还可能要等看门狗拉起 → 仍报成功
        monkey_alive = False  # 刚重启，还没出 monkey 进程

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
