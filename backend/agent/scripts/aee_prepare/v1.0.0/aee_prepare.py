"""aee_prepare v1.0.0 — 荣耀项目测试前 AEE/日志配置准备。

背景（2026-08-30 真机实证，.68 V71 设备 236）：固件出厂
``persist.vendor.mtk.aee.mode=4`` 时 AEE EE 事件引擎不产出 db 事件
（kill -11 native 只有 tombstone、``/data/aee_exp/db_history`` 无新行），
aee_signal_trigger 诱发失效、Reconciler/inotifyd 监测空转。荣耀项目
SOP：测试前必须 ``setprop persist.vendor.mtk.aee.mode 3`` 并启动
``com.debug.loggerui`` 日志服务（mobilelog 采集）。

执行序列（与荣耀 SOP 对齐，单台目标设备）：
  1. ``settings put global development_settings_enabled 1``
  2. ``ps -A | grep monkey``（诊断：monkey 进程现状，仅记录 metrics）
  3. ``getprop persist.vendor.mtk.aee.mode``（before）
  4. ``adb root``（幂等；user_root 固件已是 root 时 no-op）
  5. ``setprop persist.vendor.mtk.aee.mode 3``（**关键**——失败即整体失败）
  6. ``getprop persist.vendor.mtk.aee.mode``（after 核验，须为 3）
  7. logger 广播 ×3（start / set_total_log_size_4096 / set_sublog_4_5_0
     ——最佳努力：组件缺失时记 warning，不阻断）
  8. ``settings put global development_settings_enabled 0``（恢复）

环境变量:
    STP_DEVICE_SERIAL      (required)
    STP_ADB_PATH           (default: adb)

输出 (stdout JSON):
    {"success": true/false, "error_message": "...", "metrics": {...}}

metrics:
    aee_mode_before / aee_mode_after / aee_mode_set_ok
    monkey_processes: list[str]   （ps -A | grep monkey 的匹配行）
    logger_broadcasts: {start: bool, total_log_size: bool, sublog: bool}
    logger_warnings: list[str]
"""

from __future__ import annotations

import json
import subprocess
import sys

from _adb import adb_path, device_serial, output_result

# 与荣耀 SOP 对齐的常量（勿改——固件侧约定的模式与 cmd_name）
_AEE_MODE_PROP = "persist.vendor.mtk.aee.mode"
_AEE_MODE_TARGET = "3"
_LOGGERUI_PKG = "com.debug.loggerui"
_LOGGERUI_RECEIVER = f"{_LOGGERUI_PKG}/.framework.LogReceiver"
_LOGGER_CMDS = ("start", "set_total_log_size_4096", "set_sublog_4_5_0")

_PROGRESS_PREFIX = "PROGRESS "
_SEQ = 0


def _emit_progress(stage: str, **fields) -> None:
    global _SEQ
    _SEQ += 1
    payload = {"seq": _SEQ, "step": "aee_prepare", "stage": stage, **fields}
    sys.stderr.write(_PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def _run_adb(serial: str, args: list, timeout: int = 30) -> tuple[int, str]:
    """adb -s <serial> <args...>；返回 (rc, stdout)。"""
    proc = subprocess.run(
        [adb_path(), "-s", serial, *args],
        capture_output=True, text=True, timeout=timeout,
    )
    return proc.returncode, proc.stdout or ""


def _shell(serial: str, command: str, timeout: int = 30) -> tuple[int, str]:
    return _run_adb(serial, ["shell", command], timeout=timeout)


def main() -> None:
    serial = device_serial()
    metrics: dict = {}
    warnings: list[str] = []
    logger_broadcasts: dict = {"start": False, "total_log_size": False, "sublog": False}

    try:
        # 1. 开发者设置开（logger 广播与 setprop 的前置，SOP 顺序）
        _emit_progress("dev-settings-on")
        _shell(serial, "settings put global development_settings_enabled 1")

        # 2. monkey 进程诊断（仅记录）
        _emit_progress("monkey-probe")
        rc, out = _shell(serial, "ps -A | grep monkey")
        metrics["monkey_processes"] = [
            ln.strip() for ln in out.splitlines() if ln.strip()
        ]

        # 3. AEE 模式 before
        _emit_progress("aee-mode-read")
        rc, out = _shell(serial, f"getprop {_AEE_MODE_PROP}")
        metrics["aee_mode_before"] = out.strip() or None

        # 4. adb root（幂等）
        _emit_progress("adb-root")
        _run_adb(serial, ["root"])

        # 5-6. 设置并核验 AEE 模式 3（关键路径）
        _emit_progress("aee-mode-set", target=_AEE_MODE_TARGET)
        rc, _ = _shell(serial, f"setprop {_AEE_MODE_PROP} {_AEE_MODE_TARGET}")
        rc2, out2 = _shell(serial, f"getprop {_AEE_MODE_PROP}")
        metrics["aee_mode_after"] = out2.strip() or None
        metrics["aee_mode_set_ok"] = (
            rc == 0 and rc2 == 0 and metrics["aee_mode_after"] == _AEE_MODE_TARGET
        )
        if not metrics["aee_mode_set_ok"]:
            output_result(
                False,
                error_message=(
                    f"setprop {_AEE_MODE_PROP} failed: rc={rc}/{rc2} "
                    f"after={metrics['aee_mode_after']!r}"
                ),
                metrics=metrics,
            )
            return

        # 7. logger 广播 ×3（最佳努力）
        for cmd in _LOGGER_CMDS:
            _emit_progress("logger-broadcast", cmd=cmd)
            try:
                rc, out = _run_adb(
                    serial,
                    ["shell", "am", "broadcast",
                     "-a", "com.debug.loggerui.ADB_CMD",
                     "-e", "cmd_name", cmd,
                     "--ei", "cmd_target", "1",
                     "-n", _LOGGERUI_RECEIVER],
                )
                ok = rc == 0 and "result=0" in out
                key = {"start": "start", "set_total_log_size_4096": "total_log_size",
                       "set_sublog_4_5_0": "sublog"}[cmd]
                logger_broadcasts[key] = ok
                if not ok:
                    warnings.append(f"broadcast {cmd}: rc={rc} out={out.strip()[:120]}")
            except Exception as exc:  # noqa: BLE001 — 广播失败不阻断
                warnings.append(f"broadcast {cmd}: {exc}")
        metrics["logger_broadcasts"] = logger_broadcasts
        metrics["logger_warnings"] = warnings

        # 8. 恢复开发者设置
        _emit_progress("dev-settings-off")
        _shell(serial, "settings put global development_settings_enabled 0")

        output_result(True, metrics=metrics)
    except Exception as exc:  # noqa: BLE001 — 顶层兜底（adb 超时等）
        _emit_progress("failed")
        output_result(False, error_message=f"aee_prepare failed: {exc}",
                      metrics=metrics)


if __name__ == "__main__":
    main()
