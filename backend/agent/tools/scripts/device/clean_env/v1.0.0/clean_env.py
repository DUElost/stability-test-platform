"""Clean test environment: uninstall packages, clear logs, set system properties.

Environment:
    STP_DEVICE_SERIAL   (required)
    STP_ADB_PATH        (default: adb)
    STP_STEP_PARAMS     (optional, JSON: {uninstall_packages: [str], clear_logs: bool,
                          log_dirs: [str], set_properties: {str: str}})

Output (stdout):
    {"success": true/false, "error_message": "...", "metrics": {"uninstalled": int, "logs_cleared": int, "properties_set": int}}
"""

import sys
from _adb import adb_shell, device_serial, output_result, params


def main() -> None:
    serial = device_serial()
    args = params()

    errors = []
    uninstalled = 0
    logs_cleared = 0
    properties_set = 0

    packages = args.get("uninstall_packages", [])
    for pkg in packages:
        try:
            result = adb_shell(f"pm uninstall {pkg}", timeout=30)
            if "not installed" in (result or "").lower():
                continue
            uninstalled += 1
        except Exception as exc:
            if "not installed" not in str(exc).lower():
                errors.append(f"Failed to uninstall {pkg}: {exc}")

    if args.get("clear_logs", False):
        log_dirs = args.get("log_dirs", ["/data/aee_exp", "/data/vendor/aee_exp", "/data/debuglogger/mobilelog"])
        for d in log_dirs:
            try:
                adb_shell(f"rm -rf {d}/*", timeout=30)
                adb_shell(f"mkdir -p {d}", timeout=10)
                logs_cleared += 1
            except Exception as exc:
                errors.append(f"Failed to clear {d}: {exc}")

    properties = args.get("set_properties", {})
    for key, value in properties.items():
        try:
            adb_shell(f"setprop {key} {value}", timeout=10)
            properties_set += 1
        except Exception as exc:
            errors.append(f"Failed to set property {key}: {exc}")

    if errors:
        output_result(
            False,
            error_message="; ".join(errors),
            metrics={"uninstalled": uninstalled, "logs_cleared": logs_cleared, "properties_set": properties_set},
        )
        return

    output_result(
        True,
        metrics={"uninstalled": uninstalled, "logs_cleared": logs_cleared, "properties_set": properties_set},
    )


if __name__ == "__main__":
    main()
