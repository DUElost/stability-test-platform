"""Lightweight ADB helpers for NFS-deployed device scripts.

Each script under scripts/device/ is self-contained but may import
this module for common ADB operations.  All configuration comes from
environment variables (STP_* contract).
"""

import logging
import os
import subprocess
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
