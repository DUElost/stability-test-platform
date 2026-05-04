"""Connect device to a WiFi network.

Environment:
    STP_DEVICE_SERIAL   (required)
    STP_ADB_PATH        (default: adb)
    STP_WIFI_SSID       (required — injected by platform ResourcePool)
    STP_WIFI_PASSWORD   (required — injected by platform ResourcePool)
    STP_STEP_PARAMS     (optional, JSON: {timeout_seconds: int})

Output (stdout):
    {"success": true/false, "skipped": bool, "error_message": "...", "metrics": {"ssid": "..."}}
"""

import json
import subprocess
import sys
from _adb import adb_path, adb_shell, adb_shell_quiet, device_serial, output_result, params


def _is_connected(serial: str, ssid: str) -> bool:
    try:
        result = adb_shell_quiet("cmd -w wifi status", timeout=10)
        return ssid in (result.stdout or "")
    except Exception:
        return False


def main() -> None:
    serial = device_serial()
    args = params()

    ssid = args.get("ssid") or _env("STP_WIFI_SSID", "")
    password = args.get("password") or _env("STP_WIFI_PASSWORD", "")

    if not ssid:
        output_result(False, error_message="No WiFi SSID specified (set STP_WIFI_SSID or params.ssid)")
        return

    if _is_connected(serial, ssid):
        output_result(True, skipped=True, skip_reason=f"Already connected to {ssid}", metrics={"ssid": ssid})
        return

    timeout = args.get("timeout_seconds", 30)

    try:
        adb_shell("svc wifi enable", timeout=10)
        import time
        time.sleep(1)

        cmd = f'cmd -w wifi connect-network "{ssid}" wpa2 "{password}"'
        result = adb_shell(cmd, timeout=timeout)

        if "Error" in (result or ""):
            output_result(False, error_message=f"WiFi connect failed: {result.strip()}")
            return

        output_result(True, metrics={"ssid": ssid})
    except subprocess.TimeoutExpired:
        output_result(False, error_message=f"WiFi connect timed out after {timeout}s")
    except Exception as exc:
        output_result(False, error_message=f"WiFi connect failed: {exc}")


def _env(key: str, default: str = "") -> str:
    import os
    return os.environ.get(key, default)


if __name__ == "__main__":
    main()
