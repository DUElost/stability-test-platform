"""Clean test environment: uninstall packages, clear logs, set system properties.

v1.0.1（#812）：全部动作按 adb 返回码判定——uninstall 仅 stdout 含 Success 且
rc=0 时计数（"not installed" 视为本就未装跳过）、rm/mkdir/setprop rc 非零计错；
不再吞 rc 假成功。

Environment:
    STP_DEVICE_SERIAL   (required)
    STP_ADB_PATH        (default: adb)
    STP_STEP_PARAMS     (optional, JSON: {uninstall_packages: [str], clear_logs: bool,
                          log_dirs: [str], set_properties: {str: str}})

Output (stdout):
    {"success": true/false, "error_message": "...", "metrics": {"uninstalled": int, "logs_cleared": int, "properties_set": int}}
"""

from _adb import adb_shell_quiet, device_serial, output_result, params


def main() -> None:
    device_serial()  # 校验 STP_DEVICE_SERIAL 是否存在（缺失即退出）
    args = params()

    errors = []
    uninstalled = 0
    logs_cleared = 0
    properties_set = 0

    packages = args.get("uninstall_packages", [])
    for pkg in packages:
        try:
            result = adb_shell_quiet(f"pm uninstall {pkg}", timeout=30)
            out = (result.stdout or "").strip()
            low = out.lower()
            if "not installed" in low:
                continue  # 本就未安装：既不计成功也不计失败
            if result.returncode == 0 and "success" in low:
                uninstalled += 1
            else:
                errors.append(
                    f"Failed to uninstall {pkg}: rc={result.returncode} out={out[:200]!r}"
                )
        except Exception as exc:
            errors.append(f"Failed to uninstall {pkg}: {exc}")

    if args.get("clear_logs", False):
        log_dirs = args.get("log_dirs", ["/data/aee_exp", "/data/vendor/aee_exp", "/data/debuglogger/mobilelog"])
        for d in log_dirs:
            try:
                rm = adb_shell_quiet(f"rm -rf {d}/*", timeout=30)
                mkdir = adb_shell_quiet(f"mkdir -p {d}", timeout=10)
                if rm.returncode != 0 or mkdir.returncode != 0:
                    errors.append(
                        f"Failed to clear {d}: rc={rm.returncode}/{mkdir.returncode} "
                        f"err={(rm.stderr or '').strip()[:200]!r}"
                    )
                    continue
                logs_cleared += 1
            except Exception as exc:
                errors.append(f"Failed to clear {d}: {exc}")

    properties = args.get("set_properties", {})
    for key, value in properties.items():
        try:
            result = adb_shell_quiet(f"setprop {key} {value}", timeout=10)
            if result.returncode != 0:
                errors.append(f"Failed to set property {key}: rc={result.returncode}")
                continue
            properties_set += 1
        except Exception as exc:
            errors.append(f"Failed to set property {key}: {exc}")

    metrics = {
        "uninstalled": uninstalled,
        "logs_cleared": logs_cleared,
        "properties_set": properties_set,
    }
    if errors:
        output_result(False, error_message="; ".join(errors), metrics=metrics)
        return

    output_result(True, metrics=metrics)


if __name__ == "__main__":
    main()
