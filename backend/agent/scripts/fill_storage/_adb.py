"""Lightweight ADB helpers for NFS-deployed device scripts.

Each script under scripts/device/ is self-contained but may import
this module for common ADB operations.  All configuration comes from
environment variables (STP_* contract).
"""

import json
import logging
import os
import contextlib
import subprocess
import time
import threading
import sys

logger = logging.getLogger(__name__)


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def adb_path() -> str:
    return _env("STP_ADB_PATH", "adb")


def device_serial() -> str:
    serial = _env("STP_DEVICE_SERIAL", "")
    if not serial:
        logger.error("STP_DEVICE_SERIAL is not set")
        sys.exit(1)
    return serial


def adb_shell(command: str, timeout: int = 30) -> str:
    """Run an ADB shell command on the target device, return stdout."""
    result = subprocess.run(
        [adb_path(), "-s", device_serial(), "shell", command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout or ""


def adb_shell_quiet(command: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run ADB shell, return full CompletedProcess for exit-code checks."""
    return subprocess.run(
        [adb_path(), "-s", device_serial(), "shell", command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def adb_push(local: str, remote: str, timeout: int = 120) -> None:
    subprocess.run(
        [adb_path(), "-s", device_serial(), "push", local, remote],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )


def adb_install(apk_path: str, flags: list[str] | None = None, timeout: int = 120) -> str:
    cmd = [adb_path(), "-s", device_serial(), "install"] + (flags or []) + [apk_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return (result.stdout or "").strip()



def progress_stamp(payload: dict) -> None:
    """#115 PROGRESS 打戳（stderr；不污染 stdout 结果契约）。"""
    sys.stderr.write(f"PROGRESS {json.dumps(payload, ensure_ascii=False)}\n")
    sys.stderr.flush()


# #1690:长阻塞操作期间的周期性 PROGRESS 戳——停滞钟只认 PROGRESS 戳，
# 长步骤（push / pm install / dd）不刷戳会被误判停滞（#872 的另一剖面）。
_PROGRESS_HEARTBEAT_SECONDS = 20.0
_progress_seq_lock = threading.Lock()
_progress_seq = 0


def _next_progress_seq() -> int:
    global _progress_seq
    with _progress_seq_lock:
        _progress_seq += 1
        return _progress_seq


@contextlib.contextmanager
def progress_heartbeat(phase: str, *, interval: float | None = None):
    """长阻塞段打戳：start + 周期心跳 + end（seq 单调，#804）。"""
    step = interval if interval is not None else _PROGRESS_HEARTBEAT_SECONDS
    started = time.monotonic()
    progress_stamp({"seq": _next_progress_seq(), "phase": phase, "event": "start"})
    stop = threading.Event()

    def _beat() -> None:
        while not stop.wait(step):
            progress_stamp({
                "seq": _next_progress_seq(),
                "phase": phase,
                "event": "heartbeat",
                "elapsed_s": round(time.monotonic() - started, 1),
            })

    beat_thread = threading.Thread(target=_beat, daemon=True)
    beat_thread.start()
    try:
        yield
    finally:
        stop.set()
        beat_thread.join(timeout=1.0)
        progress_stamp({
            "seq": _next_progress_seq(),
            "phase": phase,
            "event": "end",
            "elapsed_s": round(time.monotonic() - started, 1),
        })



def progress_tick(phase: str, **extra) -> None:
    """#1690：轮询循环内的单次打戳（seq 与 heartbeat 共用计数器）。"""
    progress_stamp({"seq": _next_progress_seq(), "phase": phase, **extra})

def params() -> dict:
    import json
    raw = _env("STP_STEP_PARAMS", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def output_result(success: bool, **kwargs) -> None:
    import json
    payload = {"success": success, **kwargs}
    print(json.dumps(payload, ensure_ascii=False))
