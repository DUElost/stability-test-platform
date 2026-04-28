"""Install an APK on the device via adb install.

Environment:
    STP_DEVICE_SERIAL   (required)
    STP_ADB_PATH        (default: adb)
    STP_NFS_ROOT        (prepended to relative apk_path)
    STP_STEP_PARAMS     (required, JSON: {apk_path, pkg_name?, required_version?, reinstall?, timeout_seconds?})

Output (stdout):
    {"success": true/false, "skipped": bool, "error_message": "...", "metrics": {"apk_path": "..."}}
"""

import os
import subprocess
import sys
from _adb import adb_path, adb_shell, device_serial, output_result, params


def _resolve_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    root = os.environ.get("STP_NFS_ROOT", "")
    if root:
        return os.path.join(root, path)
    return path


def main() -> None:
    serial = device_serial()
    args = params()

    apk_path = _resolve_path(args.get("apk_path", ""))
    if not apk_path:
        output_result(False, error_message="No apk_path specified")
        return

    pkg_name = args.get("pkg_name", "")
    required_version = args.get("required_version", "")
    reinstall = args.get("reinstall", True)
    timeout = args.get("timeout_seconds", 120)

    if pkg_name and required_version:
        try:
            result = subprocess.run(
                [adb_path(), "-s", serial, "shell", f"dumpsys package {pkg_name} | grep versionName"],
                capture_output=True, text=True, timeout=10,
            )
            if required_version in (result.stdout or ""):
                output_result(
                    True,
                    skipped=True,
                    skip_reason=f"{pkg_name}=={required_version} already installed",
                    metrics={"apk_path": apk_path, "pkg_name": pkg_name},
                )
                return
        except Exception:
            pass

    try:
        flags = ["-r"] if reinstall else []
        cmd = [adb_path(), "-s", serial, "install"] + flags + [apk_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = (result.stdout or "").strip()

        if result.returncode != 0 or "Failure" in output:
            output_result(False, error_message=f"APK install failed: {output}")
            return

        output_result(True, metrics={"apk_path": apk_path, "output": output[:500]})
    except subprocess.TimeoutExpired:
        output_result(False, error_message=f"APK install timed out after {timeout}s")
    except Exception as exc:
        output_result(False, error_message=f"APK install failed: {exc}")


if __name__ == "__main__":
    main()
