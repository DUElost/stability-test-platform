"""Connect device to a WiFi network.

v1.0.1（#816）：SSID/密码经 shlex.quote 转义（原 f-string 拼接在引号/美元符/
反引号下被设备 shell 拆坏）；rc + stdout/stderr 联合判定；连接后回读
wifi status 复验（10s 窗口）才报成功。

Environment:
    STP_DEVICE_SERIAL   (required)
    STP_ADB_PATH        (default: adb)
    STP_WIFI_SSID       (required — injected by platform ResourcePool)
    STP_WIFI_PASSWORD   (required — injected by platform ResourcePool)
    STP_STEP_PARAMS     (optional, JSON: {timeout_seconds: int})

Output (stdout):
    {"success": true/false, "skipped": bool, "error_message": "...", "metrics": {"ssid": "..."}}
"""

import shlex
import subprocess
import time

from _adb import adb_shell, adb_shell_quiet, device_serial, output_result, params


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
        time.sleep(1)

        # #816：SSID/密码经 shlex.quote 转义——含引号/美元符/反引号时原
        # f-string 拼接会被设备 shell 拆坏；rc 与 stdout/stderr 联合判定
        # （错误只落 stderr 时原实现判不出，假成功）。
        cmd = (
            "cmd -w wifi connect-network "
            f"{shlex.quote(ssid)} wpa2 {shlex.quote(password)}"
        )
        result = adb_shell_quiet(cmd, timeout=timeout)
        combined = ((result.stdout or "") + (result.stderr or "")).strip()
        if result.returncode != 0 or "Error" in combined:
            output_result(False, error_message=f"WiFi connect failed: {combined[:300]}")
            return

        # #816：连接后回读 wifi status 复验（10s 窗口），命令未报错 ≠ 已连接
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if _is_connected(serial, ssid):
                output_result(True, metrics={"ssid": ssid})
                return
            time.sleep(1)
        output_result(False, error_message=f"WiFi connect not verified for {ssid} within 10s")
    except subprocess.TimeoutExpired:
        output_result(False, error_message=f"WiFi connect timed out after {timeout}s")
    except Exception as exc:
        output_result(False, error_message=f"WiFi connect failed: {exc}")


def _env(key: str, default: str = "") -> str:
    import os
    return os.environ.get(key, default)


if __name__ == "__main__":
    main()
