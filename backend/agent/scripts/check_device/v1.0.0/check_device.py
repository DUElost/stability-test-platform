"""Check whether device is reachable via ADB.

Environment:
    STP_DEVICE_SERIAL  (required)
    STP_ADB_PATH       (default: adb)
    STP_STEP_PARAMS    (optional, JSON: {expect_root: bool})

Output (stdout):
    {"success": true/false, "error_message": "..."}
"""

import json
import subprocess
import sys
from _adb import adb_path, device_serial, output_result, params


def main() -> None:
    serial = device_serial()
    args = params()

    try:
        result = subprocess.run(
            [adb_path(), "-s", serial, "shell", "echo test"],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        output_result(False, error_message=f"Device {serial} unreachable: timeout")
        return
    except Exception as exc:
        output_result(False, error_message=f"Device {serial} unreachable: {exc}")
        return

    if "test" not in (result.stdout or ""):
        output_result(False, error_message=f"Device {serial} check failed: unexpected output")
        return

    if args.get("expect_root"):
        root_check = subprocess.run(
            [adb_path(), "-s", serial, "shell", "id -u"],
            capture_output=True, text=True, timeout=10,
        )
        if (root_check.stdout or "").strip() != "0":
            output_result(False, error_message=f"Device {serial} has no root access")
            return

    output_result(True, serial=serial, skipped=False)


if __name__ == "__main__":
    main()
