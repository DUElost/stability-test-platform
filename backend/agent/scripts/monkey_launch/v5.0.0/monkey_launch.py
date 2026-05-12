"""Monkey 启动：仅启动 MonkeyTest.sh 看门狗 + aimwd 守护进程。

不做资源推送（由 monkey_resource_push 负责），只启动进程并验证 MonkeyTest.sh 运行。

环境变量:
    STP_DEVICE_SERIAL      (required)
    STP_STEP_PARAMS        (optional JSON)

STP_STEP_PARAMS:
{
    "need_nohup": true,
    "watchdog_script": "MonkeyTest.sh"
}

输出 (stdout):
    {"success": true/false, "metrics": {...}}
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


def _ps_grep(serial: str, pattern: str, timeout: int = 10) -> bool:
    """Check if a process matching `pattern` is running on device."""
    rc, out = _shell(
        serial,
        f"ps -ef | grep '{pattern}' | grep -v grep | head -1",
        timeout=timeout,
    )
    return rc == 0 and len(out.strip()) > 0


def main():
    serial = device_serial()
    args = params()

    need_nohup = bool(args.get("need_nohup", True))
    watchdog_script = args.get("watchdog_script", "MonkeyTest.sh")
    max_wait = int(args.get("max_wait_seconds", 15))

    t0 = time.time()

    # ── 预检 ──
    rc, _ = _shell(serial, "echo ready", timeout=10)
    if rc != 0:
        output_result(False, error_message=f"Device {serial} not reachable")
        sys.exit(1)

    # ── 检查是否已在运行 ──
    if _ps_grep(serial, watchdog_script):
        output_result(
            True,
            metrics={"serial": serial, "already_running": True, "duration_s": round(time.time() - t0, 1)},
        )
        return

    # ── 启动 MonkeyTest.sh ──
    if need_nohup:
        cmd = (
            "cd /data/local/tmp; "
            "nohup sh /data/local/tmp/MonkeyTest.sh >/dev/null 2>&1 &"
        )
    else:
        cmd = "nohup sh /data/local/tmp/MonkeyTest.sh >/dev/null 2>&1 &"

    rc, _ = _shell(serial, cmd, timeout=30)
    if rc != 0:
        output_result(False, error_message=f"MonkeyTest.sh start failed (rc={rc})")
        sys.exit(1)

    # ── 启动 aimwd 守护 ──
    _shell(serial, "nohup /data/local/tmp/aimwd >/dev/null 2>&1 &", timeout=15)

    # ── Post-check: 等待 MonkeyTest.sh 出现 ──
    deadline = time.time() + max_wait
    sh_running = False
    while time.time() < deadline:
        if _ps_grep(serial, watchdog_script):
            sh_running = True
            break
        time.sleep(2)

    elapsed = round(time.time() - t0, 1)

    if not sh_running:
        output_result(
            False,
            error_message=f"MonkeyTest.sh not running after {max_wait}s",
            metrics={"duration_s": elapsed},
        )
        sys.exit(1)

    output_result(
        True,
        metrics={
            "serial": serial,
            "watchdog_started": True,
            "duration_s": elapsed,
        },
    )


if __name__ == "__main__":
    main()
