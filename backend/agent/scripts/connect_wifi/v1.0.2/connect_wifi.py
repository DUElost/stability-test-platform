"""Connect device to a WiFi network.

v1.0.2（#1558）：修掉「已连接」判据的子串匹配假成功。

原判据是 ``ssid in wifi_status_stdout``：请求连接 ``Test`` 而设备实际连在
``Test-5G`` 时返回 True → 直接以 ``skipped=True`` 报成功且**根本不发起连接**
（用例以为连上了目标网络，实际在别的频段）；v1.0.1 新加的「连接后回读复验」
用的是同一个谓词，也拦不住。

v1.0.2 改为**格式无关的精确 token 匹配**：把状态输出按分隔符（空白/引号/逗号/
冒号/等号/括号）切开后做**全等**比较，另加一条「带引号全等」路径以覆盖 SSID
内含空格的情形。这样不必假设 ``cmd -w wifi status`` 的具体标签形态——
真实设备实证只有 ``Wifi is enabled`` / ``Wifi is not connected`` 两行形态有样本，
连上后的 SSID 行形态没有，所以不猜格式。

代价说明：若某天设备把 SSID 以未加引号且含分隔符的形态输出，会判为未连接
（保守方向的假阴性，只会让步骤多连一次并在 10s 窗口内复验，不会假成功）。

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

import re
import shlex
import subprocess
import time

from _adb import adb_shell, adb_shell_quiet, device_serial, output_result, params

# #1558：带 SSID 标签的值。前置字符断言 `(?<![A-Za-z])` 排除 `BSSID:`——
# 否则 BSSID 会被当成一个 SSID 候选值。
_SSID_VALUE_RE = re.compile(
    r"""(?<![A-Za-z])SSID"?\s*[:=]\s*(?P<value>"[^"]*"|'[^']*'|[^,\n]*)""",
    re.IGNORECASE,
)


def _labelled_ssids(status_text: str) -> list[str]:
    """取出 ``SSID: <value>`` 形态里的值（去引号）。

    `cmd -w wifi status` 连上后的标签形态在仓库里**没有真实样本**（只实证了
    ``Wifi is enabled`` / ``Wifi is not connected`` 两行），所以这里只做「有标签
    就按标签取值」，值交给调用方做全等比较；无标签的形态由带引号全等覆盖。
    """
    values: list[str] = []
    for match in _SSID_VALUE_RE.finditer(status_text or ""):
        value = match.group("value").strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value:
            values.append(value)
    return values


def _is_connected(serial: str, ssid: str) -> bool:
    """是否已连接到**目标** SSID（全等匹配，非子串）。

    两条判据，都要求全等：
    1. 带 SSID 标签的值与目标相同（覆盖 ``SSID: x`` / ``SSID=x`` / ``"ssid":"x"``）；
    2. 目标在输出里以**带引号**的完整形态出现（覆盖无标签的 ``connected to "x"``，
       同时覆盖 SSID 内含空格的情形）。

    刻意**没有**「裸 token 全等」这条兜底：它会让目标 ``My`` 命中设备上的
    ``My Network``——只是把子串假成功换成词级假成功。代价是「既无标签又无引号」
    的形态判为未连接，属保守方向的假阴性：步骤会再连一次并在 10s 窗口内复验，
    表现为可见的步骤失败而不是静默假成功。
    """
    if not ssid:
        return False
    try:
        result = adb_shell_quiet("cmd -w wifi status", timeout=10)
    except Exception:
        return False
    text = result.stdout or ""
    if ssid in _labelled_ssids(text):
        return True
    return f'"{ssid}"' in text or f"'{ssid}'" in text


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
