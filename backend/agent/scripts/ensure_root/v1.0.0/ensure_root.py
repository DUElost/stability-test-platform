"""Ensure device has root access via adb root.

Environment:
    STP_DEVICE_SERIAL   (required)
    STP_ADB_PATH        (default: adb)
    STP_STEP_PARAMS     (optional, JSON: {max_attempts: int, retry_delay_seconds: float})

Output (stdout):
    {"success": true/false, "skipped": bool, "error_message": "..."}
"""

import subprocess
import sys
import time
from _adb import adb_path, adb_shell, device_serial, output_result, params


def _is_root(serial: str) -> bool:
    try:
        result = subprocess.run(
            [adb_path(), "-s", serial, "shell", "id -u"],
            capture_output=True, text=True, timeout=10,
        )
        return (result.stdout or "").strip() == "0"
    except Exception:
        return False


def main() -> None:
    serial = device_serial()
    args = params()
    max_attempts = args.get("max_attempts", 3)
    retry_delay = args.get("retry_delay_seconds", 3.0)

    if _is_root(serial):
        output_result(True, skipped=True, skip_reason="Already root")
        return

    for attempt in range(1, max_attempts + 1):
        try:
            subprocess.run(
                [adb_path(), "-s", serial, "root"],
                capture_output=True, text=True, timeout=10,
            )
            time.sleep(retry_delay)

            if _is_root(serial):
                output_result(True, metrics={"attempts": attempt})
                return
        except Exception as exc:
            if attempt == max_attempts:
                output_result(False, error_message=f"adb root failed after {max_attempts} attempts: {exc}")
                return
            time.sleep(2)

    output_result(False, error_message=f"Root access not granted after {max_attempts} attempts")


if __name__ == "__main__":
    main()
