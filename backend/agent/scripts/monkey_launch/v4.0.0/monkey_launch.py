"""Monkey test launcher v2 — supports trigger and foreground monitoring modes.

Trigger mode (default, backward-compatible with v1):
    Start monkey via nohup, return immediately. Script exits in ~15s.

Foreground mode (new):
    Start monkey, verify process is alive, then monitor until timeout
    or crash. Keeps the script alive so Watcher captures crash signals
    during the entire monkey test duration.

Environment:
    STP_DEVICE_SERIAL    (required)
    STP_ADB_PATH         (default: adb)
    STP_LOG_DIR          (default: /tmp)
    STP_STEP_PARAMS      (optional JSON)

STP_STEP_PARAMS:
{
    "aimonkey_dir": "/opt/stability-test-agent/agent/resources/aimonkey/AIMonkeyTest_20260317",
    "need_nohup": true,
    "push_resources": true,
    "sleep_mode": false,
    "blacklist": true,
    "run_mode": "trigger",
    "duration_seconds": 3600,
    "check_interval": 60,
    "process_names": ["com.android.commands.monkey"],
    "watchdog_script": "MonkeyTest.sh"
}

Output (stdout):
    {"success": true/false, "metrics": {...}}
"""

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

from _adb import adb_path, adb_shell, adb_shell_quiet, device_serial, output_result, params

_AGENT_ROOT = Path(__file__).resolve().parents[3]
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from aimonkey_paths import resolve_aimonkey_bundle_dir  # noqa: E402


# ── 进程存活检测（前台监控用） ──

def _ps_grep(serial: str, pattern: str, timeout: int = 10) -> list[dict]:
    result = subprocess.run(
        [adb_path(), "-s", serial, "shell", "ps -ef"],
        capture_output=True, text=True, timeout=timeout,
    )
    matches = []
    for line in (result.stdout or "").splitlines():
        if pattern in line and "grep" not in line:
            parts = line.split()
            if len(parts) >= 2:
                matches.append({"pid": parts[1], "line": line.strip()})
    return matches


def _check_monkey_alive(serial: str, process_names: list[str]) -> dict:
    result = {"alive": False, "processes": {}, "monkey_pid": ""}
    for name in process_names:
        matches = _ps_grep(serial, name)
        if matches:
            result["processes"][name] = matches
            result["alive"] = True
            if not result["monkey_pid"] and "monkey" in name.lower():
                result["monkey_pid"] = matches[0]["pid"]
    return result


# ── AIMonkeyTest 加载逻辑（同 v1.0.0） ──

def _resolve_aimonkey_dir(cfg: dict) -> Path:
    return resolve_aimonkey_bundle_dir(cfg)


def _load_monkey_test(aimonkey_dir: Path):
    script_path = aimonkey_dir / "MonkeyTest.py"
    if not script_path.exists():
        raise FileNotFoundError(f"MonkeyTest.py not found at {script_path}")

    module_name = f"_stp_monkey_launcher_{abs(hash(str(script_path)))}"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {script_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _verify_device(serial: str) -> dict:
    try:
        result = adb_shell_quiet("echo ready", timeout=10)
        if result.returncode != 0:
            return {"ok": False, "error": f"Device {serial} not reachable"}
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": f"Device check failed: {exc}"}


# ── 前台监控模式 ──

def _monitor_loop(
    serial: str,
    process_names: list[str],
    watchdog_pattern: str,
    duration_seconds: int,
    check_interval: int,
) -> dict:
    """Monitor monkey process until timeout or exit. Returns exit reason."""
    deadline = time.time() + duration_seconds
    restart_count = 0
    max_restarts = 3

    logger_prefix = "[monkey_launch:foreground]"

    while time.time() < deadline:
        remaining = int(deadline - time.time())
        status = _check_monkey_alive(serial, process_names + [watchdog_pattern])
        monkey_ok = status["alive"]

        if not monkey_ok:
            # Monkey disappeared — check if system is still alive
            try:
                sys_check = adb_shell_quiet("getprop sys.boot_completed", timeout=10)
                boot_ok = sys_check.stdout.strip() == "1"
            except Exception:
                boot_ok = False

            if not boot_ok:
                return {"reason": "device_offline", "restarts": restart_count}

            # Monkey crashed but system OK — restart if limit not hit
            if restart_count < max_restarts:
                restart_count += 1
                print(f"[STP_MONITOR] Monkey disappeared, restart attempt {restart_count}/{max_restarts}", flush=True)
                try:
                    subprocess.run(
                        [adb_path(), "-s", serial, "shell",
                         "monkey --kill-process-after-error --ignore-crashes --ignore-timeouts "
                         "--ignore-security-exceptions --monitor-native-crashes --ignore-native-crashes "
                         "--pkg-blacklist-file /sdcard/blacklist.txt --throttle 500 -v 1000000000 "
                         ">/dev/null 2>&1 &"],
                        capture_output=True, text=True, timeout=15,
                    )
                except Exception:
                    pass
                time.sleep(5)
                continue
            else:
                return {"reason": "monkey_crashed", "restarts": restart_count}

        # Periodic status heartbeat
        monkey_pid = status.get("monkey_pid", "unknown")
        print(
            f"[STP_MONITOR] Monkey alive pid={monkey_pid} remaining={remaining}s "
            f"restarts={restart_count}",
            flush=True,
        )

        time.sleep(check_interval)

    return {"reason": "timeout", "restarts": restart_count}


# ── Main ──

def main():
    serial = device_serial()
    args = params()

    aimonkey_dir = _resolve_aimonkey_dir(args)
    if not aimonkey_dir.is_dir():
        output_result(False, error_message=f"AIMonkeyTest dir not found: {aimonkey_dir}")
        return

    check = _verify_device(serial)
    if not check["ok"]:
        output_result(False, error_message=check["error"])
        return

    need_nohup = bool(args.get("need_nohup", True))
    push_resources = bool(args.get("push_resources", True))
    sleep_mode = bool(args.get("sleep_mode", False))
    blacklist = bool(args.get("blacklist", True))
    run_mode = args.get("run_mode", "trigger")

    old_cwd = os.getcwd()
    inserted = False
    try:
        os.chdir(str(aimonkey_dir))
        if str(aimonkey_dir) not in sys.path:
            sys.path.insert(0, str(aimonkey_dir))
            inserted = True

        module = _load_monkey_test(aimonkey_dir)
        MonkeyTest = getattr(module, "MonkeyTest")
        runner = MonkeyTest(need_nohup, push_resources, sleep_mode, blacklist)

        # ── Start monkey ──
        t0 = time.time()
        runner.startTest(serial)
        launch_elapsed = round(time.time() - t0, 1)

        if run_mode == "foreground":
            # ── Foreground monitoring mode ──
            duration = int(args.get("duration_seconds", 3600))
            check_interval = int(args.get("check_interval", 60))
            process_names = args.get("process_names", ["com.android.commands.monkey"])
            watchdog = args.get("watchdog_script", "MonkeyTest.sh")

            # Phase 1: Admission check — verify monkey started
            print(f"[STP_MONITOR] Admission check: verifying monkey process...", flush=True)
            admission_deadline = time.time() + 30
            monkey_confirmed = False
            while time.time() < admission_deadline:
                status = _check_monkey_alive(serial, process_names + [watchdog])
                if status["alive"]:
                    monkey_confirmed = True
                    print(
                        f"[STP_MONITOR] Admission PASS: monkey_pid={status.get('monkey_pid', '?')} "
                        f"watchdog_alive={'MonkeyTest.sh' in str(status.get('processes', {}))}",
                        flush=True,
                    )
                    break
                time.sleep(3)

            if not monkey_confirmed:
                output_result(False, error_message="Admission failed: monkey process not found after 30s")
                return

            # Phase 2: Monitor loop
            print(
                f"[STP_MONITOR] Entering foreground monitor: duration={duration}s interval={check_interval}s",
                flush=True,
            )
            monitor_result = _monitor_loop(
                serial, process_names, watchdog,
                duration, check_interval,
            )

            total_elapsed = round(time.time() - t0, 1)
            output_result(
                True,
                metrics={
                    "serial": serial,
                    "aimonkey_dir": str(aimonkey_dir),
                    "run_mode": "foreground",
                    "launch_duration_s": launch_elapsed,
                    "total_duration_s": total_elapsed,
                    "exit_reason": monitor_result["reason"],
                    "restart_count": monitor_result["restarts"],
                    "push_resources": push_resources,
                    "blacklist": blacklist,
                },
            )
        else:
            # ── Trigger mode (backward compatible) ──
            output_result(
                True,
                metrics={
                    "serial": serial,
                    "aimonkey_dir": str(aimonkey_dir),
                    "run_mode": "trigger",
                    "push_resources": push_resources,
                    "sleep_mode": sleep_mode,
                    "blacklist": blacklist,
                    "duration_s": launch_elapsed,
                },
            )
    except Exception as exc:
        output_result(False, error_message=f"Monkey launch failed: {exc}")
    finally:
        os.chdir(old_cwd)
        if inserted:
            try:
                sys.path.remove(str(aimonkey_dir))
            except ValueError:
                pass


if __name__ == "__main__":
    main()
