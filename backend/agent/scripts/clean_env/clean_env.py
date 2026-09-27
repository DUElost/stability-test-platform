"""Clean test environment: uninstall packages, clear logs, set system properties.

v1.1.1（#3463 G3）：**plan 参数插入 root shell 的校验面收口**——v1.1.0 把
`uninstall_packages` / `log_dirs` / `set_properties` 的键值原样插进设备端 root shell
（`pm uninstall {pkg}` / `rm -rf {d}/*` / `setprop {key} {value}`，#3107 判据；clean_env
的 `log_dirs` 与 monkey_setup v2.3.10 缺陷完全同形、此前无 issue 跟踪）：
`log_dirs: [""]` ⇒ `rm -rf /*`、含空格/`;`/`$()` 的值可扩张成任意命令。现在
`log_dirs` port `validated_log_dirs`（只接受 `/data/`、`/sdcard/` 下的绝对路径）；
包名与 `setprop` 键按 `^[A-Za-z0-9._-]+$` 白名单；`setprop` 值过 `shlex.quote`。
非法值整步转红，且不下发任何含未校验值的命令（宁可红不把破坏性命令执行面交给参数）。

v1.0.1（#812）：全部动作按 adb 返回码判定——uninstall 仅 stdout 含 Success 且
rc=0 时计数（"not installed" 视为本就未装跳过）、rm/mkdir/setprop rc 非零计错；
不再吞 rc 假成功。

v1.1.0（#1690）：pm uninstall 与日志目录清理（rm -rf 大目录）段接入
PROGRESS 心跳——停滞钟只认 PROGRESS 戳（#115），长静默段会被误判停滞。

Environment:
    STP_DEVICE_SERIAL   (required)
    STP_ADB_PATH        (default: adb)
    STP_STEP_PARAMS     (optional, JSON: {uninstall_packages: [str], clear_logs: bool,
                          log_dirs: [str], set_properties: {str: str}})

Output (stdout):
    {"success": true/false, "error_message": "...", "metrics": {"uninstalled": int, "logs_cleared": int, "properties_set": int}}
"""

import re
import shlex

from _adb import adb_shell_quiet, device_serial, output_result, params, progress_heartbeat

#: `log_dirs` 白名单形态（判据 port 自 monkey_setup v2.3.11 / #3107）：每一项都会被
#: 插进设备端 root shell 的 `rm -rf {d}/*`，故只接受 /data/ 或 /sdcard/ 下的普通绝对路径。
_DEFAULT_LOG_DIRS = (
    "/data/aee_exp",
    "/data/vendor/aee_exp",
    "/data/debuglogger/mobilelog",
)

_LOG_DIR_RE = re.compile(r"^/(?:data|sdcard)/[A-Za-z0-9._/-]{1,120}$")

#: 包名 / setprop 键白名单（#3463 G3/F3）：两者都直接插进 root shell。
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def validated_log_dirs(raw: object) -> list:
    """校验计划参数 `log_dirs`（#3107，port 自 monkey_setup v2.3.11）。

    未传（None）按默认目录返回；传了就必须**每一项**都是 ``/data/`` 或 ``/sdcard/``
    下的普通绝对路径——非法项直接抛 ValueError，由调用方记红（不静默跳过，
    否则「清理没做」会以 success 结束）。
    """
    if raw is None:
        return list(_DEFAULT_LOG_DIRS)
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"log_dirs 必须是字符串列表（收到 {type(raw).__name__}）")
    out = []
    for item in raw:
        text = str(item or "").strip()
        if not _LOG_DIR_RE.match(text) or ".." in text.split("/"):
            raise ValueError(
                f"log_dirs 含非法项 {text!r}：必须是 /data/ 或 /sdcard/ 下的绝对路径"
            )
        out.append(text)
    return out


def validated_package_name(raw: object) -> str:
    """校验 `uninstall_packages` 单项（#3463 G3/F3）。

    该值被插进 root 的 `pm uninstall`，只接受包名形态（字母/数字/点/下划线/连字符）；
    非法项抛 ValueError，由调用方记红，不把未校验值下发。
    """
    text = str(raw or "").strip()
    if not _NAME_RE.match(text):
        raise ValueError(
            f"uninstall_packages 含非法项 {text!r}：必须匹配 ^[A-Za-z0-9._-]+$"
        )
    return text


def validated_property_key(raw: object) -> str:
    """校验 `set_properties` 键（#3463 G3/F3）。

    该值被插进 root 的 `setprop {key} …`，只接受 ``[A-Za-z0-9._-]`` 形态（覆盖
    ``persist.sys.*`` 等合法点分键名）；值侧由调用点 `shlex.quote`。
    """
    text = str(raw or "").strip()
    if not _NAME_RE.match(text):
        raise ValueError(
            f"set_properties 含非法键 {text!r}：必须匹配 ^[A-Za-z0-9._-]+$"
        )
    return text


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
            pkg = validated_package_name(pkg)
        except ValueError as exc:
            # 参数非法 = 配置错误，记红该步；不把未校验值插进 root shell（#3463 G3）。
            errors.append(str(exc))
            continue
        try:
            with progress_heartbeat(f"uninstall:{pkg}"):
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
        try:
            log_dirs = validated_log_dirs(args.get("log_dirs"))
        except ValueError as exc:
            # 参数非法 = 配置错误，直接红（#3107 判据）——不把未校验的值插进 root shell。
            errors.append(str(exc))
            log_dirs = []
        for d in log_dirs:
            try:
                with progress_heartbeat(f"clear_logs:{d}"):
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
            key = validated_property_key(key)
        except ValueError as exc:
            # 键非法 = 配置错误，记红该步；值侧统一 shlex.quote（#3463 G3）。
            errors.append(str(exc))
            continue
        try:
            result = adb_shell_quiet(f"setprop {key} {shlex.quote(str(value))}", timeout=10)
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
