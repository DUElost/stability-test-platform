# -*- coding: utf-8 -*-
"""FacAging (com.ape.aging) Aging DDR 专项启动器（init 阶段）。

纯 ADB 无 UI 启动链路（已于 525 平台 94 台实机验证）：
1. 前置检查：adb root (uid=0)、FacAging APK 已安装、电量与供电达标；
2. 清理历史 session/report/workspace，经无害 WAKE 广播退出 stopped=true 后 kill -9
   刷新进程缓存（避免 Android 16 重放积压 BOOT_COMPLETED 导致双启动）；
3. 写入 ``home_stage_selection`` / ``home_test_mode`` / ``ddr_test_params`` /
   ``aging_pipeline_session`` (``DDR:PENDING``) DataStore 文件并修正属主与权限；
4. 临时设置 ``sys.boot.reason.last=reboot,aging_ddr_reboot_test`` 并定向广播
   ``UnifiedBootReceiver`` (``BOOT_COMPLETED``)；
5. 轮询 logcat 核验 ``sessionId`` + ``modulePassCount`` 启动标记及 DST 重启断连。

STP_STEP_PARAMS:
{
    "mode": "AGING",                          // AGING | VERIFICATION
    "aging_module_pass_count": 7,             // AGING 模式整体测试次数
    "verification_module_pass_count": 1,      // VERIFICATION 模式整体测试次数
    "enabled_items": "REBOOT_DST,DDR_FLAG_CHECK,REBOOT,DB_HISTORY",
    "reboot_dst_times": 1,
    "aging_ddr_flag_times": 1,
    "verification_ddr_flag_times": 1,
    "aging_reboot_times": 10,
    "verification_reboot_times": 1,
    "reboot_interval_sec": 5,
    "aging_check_duration_sec": 10,
    "verification_check_duration_sec": 10,
    "aging_db_times": 1,
    "verification_db_times": 1,
    "expected_apk_version": "",               // 非空时强校验版本，如 "1.0.3.3"
    "min_battery_level": 20,                  // 未接充电时最低电量阈值；充电时最低 10%
    "verify_timeout_seconds": 20
}

输出 (stdout): {"success": true/false, "metrics": {...}}
"""
from __future__ import annotations

import sys
import time
import uuid
from typing import Any

from _lib import (
    APP_FILES_DIR,
    BOOT_RECEIVER,
    DATASTORE_DIR,
    DDR_PARAM_DEFAULTS,
    EXPECTED_BOOT_REASON,
    PACKAGE,
    VALID_DDR_ITEMS,
    VALID_MODES,
    WAKE_ACTION,
    WORKSPACE_DDR_DIR,
    adb,
    ddr_service_alive,
    device_online,
    device_serial,
    ensure_adb_root,
    output_result,
    param_or_env,
    params,
    preflight_device_info,
    progress_stamp,
    read_datastore_file,
    write_datastore_files,
)

_INT_PARAM_KEYS = (
    "reboot_dst_times",
    "aging_ddr_flag_times",
    "verification_ddr_flag_times",
    "aging_reboot_times",
    "verification_reboot_times",
    "reboot_interval_sec",
    "aging_check_duration_sec",
    "verification_check_duration_sec",
    "aging_db_times",
    "verification_db_times",
    "aging_module_pass_count",
    "verification_module_pass_count",
)


def _parse_config(cfg: dict[str, Any]) -> dict[str, Any]:
    mode = str(param_or_env(cfg, "mode", "STP_AGING_DDR_MODE", "AGING")).strip().upper()
    if mode not in VALID_MODES:
        raise ValueError(f"mode 必须是 AGING 或 VERIFICATION，收到: {mode!r}")

    raw_items = str(
        param_or_env(
            cfg,
            "enabled_items",
            "STP_AGING_DDR_ENABLED_ITEMS",
            DDR_PARAM_DEFAULTS["enabled_items"],
        )
    ).strip()
    items = [item.strip().upper() for item in raw_items.split(",") if item.strip()]
    if not items:
        raise ValueError("enabled_items 不能为空")
    invalid = [item for item in items if item not in VALID_DDR_ITEMS]
    if invalid:
        raise ValueError(
            f"enabled_items 含非法项 {invalid}，合法值: {sorted(VALID_DDR_ITEMS)}"
        )

    ddr_params: dict[str, str] = dict(DDR_PARAM_DEFAULTS)
    ddr_params["enabled_items"] = ",".join(items)
    for key in _INT_PARAM_KEYS:
        env_key = f"STP_AGING_DDR_{key.upper()}"
        raw_val = param_or_env(cfg, key, env_key, DDR_PARAM_DEFAULTS[key])
        try:
            int_val = int(str(raw_val).strip())
        except ValueError as exc:
            raise ValueError(f"参数 {key} 必须为整数，收到: {raw_val!r}") from exc
        if int_val < 1:
            raise ValueError(f"参数 {key} 必须 >= 1，收到: {int_val}")
        ddr_params[key] = str(int_val)

    expected_ver = str(
        param_or_env(cfg, "expected_apk_version", "STP_AGING_DDR_EXPECTED_APK_VERSION", "")
    ).strip()
    min_battery = int(
        str(param_or_env(cfg, "min_battery_level", "STP_AGING_DDR_MIN_BATTERY_LEVEL", 20)).strip()
    )
    verify_timeout = max(
        5,
        int(
            str(
                param_or_env(
                    cfg, "verify_timeout_seconds", "STP_AGING_DDR_VERIFY_TIMEOUT_SECONDS", 20
                )
            ).strip()
        ),
    )

    effective_pass_count = int(
        ddr_params["aging_module_pass_count"]
        if mode == "AGING"
        else ddr_params["verification_module_pass_count"]
    )
    return {
        "mode": mode,
        "enabled_items": items,
        "ddr_params": ddr_params,
        "effective_pass_count": effective_pass_count,
        "expected_apk_version": expected_ver,
        "min_battery_level": min_battery,
        "verify_timeout_seconds": verify_timeout,
    }


def _check_preflight(parsed: dict[str, Any]) -> dict[str, Any]:
    if not ensure_adb_root():
        raise RuntimeError("设备未获得 adb root（uid != 0），无法写入 FacAging DataStore")

    info = preflight_device_info(timeout=15)
    if info.get("rc") != 0:
        raise RuntimeError(f"前置探测失败（rc={info.get('rc')}）: {info.get('stderr', '')}")
    if info.get("uid") != "0":
        raise RuntimeError(f"设备当前非 root 权限（uid={info.get('uid')!r}）")

    apk_ver = str(info.get("apk_version") or "").strip()
    if not apk_ver:
        raise RuntimeError(f"设备未安装 {PACKAGE}（dumpsys package 未检出 versionName）")
    want_ver = parsed["expected_apk_version"]
    if want_ver and apk_ver != want_ver:
        raise RuntimeError(
            f"{PACKAGE} 版本不匹配：期望 {want_ver!r}，实测 {apk_ver!r} "
            f"(board={info.get('board')}, build={info.get('build')})"
        )

    battery = info.get("battery_level")
    powered = bool(info.get("usb_powered") or info.get("ac_powered"))
    min_bat = parsed["min_battery_level"]
    if isinstance(battery, int):
        if powered and battery < min(10, min_bat):
            raise RuntimeError(
                f"设备电量过低（{battery}%，已接供电），存在 DST 掉电风险"
            )
        if not powered and battery < min_bat:
            raise RuntimeError(
                f"设备电量过低（{battery}% < {min_bat}% 且未接 USB/AC 供电），拒绝启动 DDR 压测"
            )
    return info


def _prepare_clean_process() -> None:
    adb("shell", f"am force-stop {PACKAGE}", timeout=15)
    rc, out, err = adb(
        "shell",
        (
            f"mkdir -p {DATASTORE_DIR} && "
            f"chown system:system {APP_FILES_DIR} {DATASTORE_DIR} && "
            f"chmod 700 {DATASTORE_DIR} && "
            f"rm -f {DATASTORE_DIR}/ddr_test_session.preferences_pb "
            f"{DATASTORE_DIR}/ddr_test_report.preferences_pb "
            f"{DATASTORE_DIR}/aging_pipeline_session.preferences_pb && "
            f"rm -rf {WORKSPACE_DDR_DIR}"
        ),
        timeout=15,
    )
    if rc != 0:
        detail = (err or out).strip() or f"rc={rc}"
        raise RuntimeError(f"清理历史 DataStore/工作目录失败: {detail}")

    # 先在无 pipeline session 的状态下经无害 WAKE 脱离 stopped=true，排空 Android 16
    # 可能重放的积压 BOOT_COMPLETED 广播；随后仅 kill -9 刷新内存 DataStore 缓存
    # 而不重新置 stopped=true。
    adb("shell", f"am broadcast -n {BOOT_RECEIVER} -a {WAKE_ACTION}", timeout=15)
    time.sleep(1.0)
    adb("shell", f"pidof {PACKAGE} | xargs -r kill -9", timeout=10)
    time.sleep(0.3)


def _verify_startup(
    sid: str,
    pass_count: int,
    *,
    has_reboot_dst: bool,
    timeout_s: int,
) -> tuple[bool, bool, bool, list[str]]:
    started_marker = False
    dst_marker = False
    dropped_adb = False
    last_lines: list[str] = []
    deadline = time.monotonic() + timeout_s
    seq = 0
    while time.monotonic() < deadline:
        time.sleep(1.0)
        seq += 1
        rc, out, _ = adb("logcat", "-d", "-s", "Aging_:I", timeout=4)
        if rc != 0:
            dropped_adb = True
            progress_stamp(
                {
                    "phase": "verify_startup",
                    "seq": seq,
                    "started_marker": started_marker,
                    "dst_marker": dst_marker,
                    "dropped_adb": True,
                }
            )
            if started_marker or has_reboot_dst:
                break
            continue
        if (f"sessionId={sid}" in out and f"modulePassCount={pass_count}" in out) or (
            "[Ddr] test started" in out and f"sessionId={sid}" in out
        ):
            started_marker = True
        if "reboot dst round=" in out or "rebooting device dst" in out:
            dst_marker = True
        last_lines = [ln.strip() for ln in out.splitlines() if "Aging_" in ln][-8:]
        progress_stamp(
            {
                "phase": "verify_startup",
                "seq": seq,
                "started_marker": started_marker,
                "dst_marker": dst_marker,
                "dropped_adb": False,
            }
        )
        if not has_reboot_dst and started_marker:
            break
    if not dropped_adb and has_reboot_dst and not device_online(timeout=5):
        dropped_adb = True
    return started_marker, dst_marker, dropped_adb, last_lines


def _run(cfg: dict[str, Any]) -> dict[str, Any]:
    serial = device_serial()
    parsed = _parse_config(cfg)
    progress_stamp({"phase": "preflight", "serial": serial, "mode": parsed["mode"]})
    pf = _check_preflight(parsed)

    progress_stamp({"phase": "prepare_datastore", "serial": serial})
    _prepare_clean_process()

    adb("logcat", "-c", timeout=10)
    sid = str(uuid.uuid4())
    now_ms = int(time.time() * 1000)
    files = {
        "home_stage_selection.preferences_pb": {"selected_stages": "DDR"},
        "home_test_mode.preferences_pb": {"test_run_mode": parsed["mode"]},
        "ddr_test_params.preferences_pb": parsed["ddr_params"],
        "aging_pipeline_session.preferences_pb": {
            "session_id": sid,
            "active": True,
            "status": "RUNNING",
            "test_run_mode": parsed["mode"],
            "started_at_ms": now_ms,
            "selected_stages": "DDR",
            "current_stage": "DDR",
            "stage_status": "DDR:PENDING",
        },
    }
    write_datastore_files(files)

    progress_stamp({"phase": "trigger_start", "serial": serial, "session_id": sid})
    prev_boot_reason = str(pf.get("boot_reason") or "").strip()
    adb("shell", f"setprop sys.boot.reason.last {EXPECTED_BOOT_REASON}", timeout=10)
    rc, out, err = adb(
        "shell",
        f"am broadcast -n {BOOT_RECEIVER} -a android.intent.action.BOOT_COMPLETED",
        timeout=15,
    )
    if rc != 0:
        detail = (err or out).strip() or f"rc={rc}"
        raise RuntimeError(f"发送 BOOT_COMPLETED 广播失败: {detail}")

    has_reboot_dst = "REBOOT_DST" in parsed["enabled_items"]
    started_marker, dst_marker, dropped_adb, tail_logs = _verify_startup(
        sid,
        parsed["effective_pass_count"],
        has_reboot_dst=has_reboot_dst,
        timeout_s=parsed["verify_timeout_seconds"],
    )

    if not has_reboot_dst and not dropped_adb:
        # 非 REBOOT_DST 配置下设备仍在线：补充确认服务/会话存活并恢复 boot reason
        if not started_marker:
            alive, _ = ddr_service_alive(timeout=10)
            ds_kind, ds_data = read_datastore_file("ddr_test_session.preferences_pb", timeout=10)
            if alive or (ds_kind == "ok" and ds_data.get("running") is True):
                started_marker = True
        if prev_boot_reason:
            adb("shell", f"setprop sys.boot.reason.last {prev_boot_reason}", timeout=10)

    if has_reboot_dst:
        if started_marker and dropped_adb:
            status = "started_and_entering_dst"
        elif started_marker and dst_marker:
            status = "started_dst_countdown"
        elif dropped_adb:
            status = "dropped_for_dst_fast"
        else:
            tail = " | ".join(tail_logs[-4:])
            raise RuntimeError(
                f"未检测到 FacAging DDR 启动或 DST 断连标记（session_id={sid}）: {tail}"
            )
    else:
        if not started_marker:
            tail = " | ".join(tail_logs[-4:])
            raise RuntimeError(
                f"未检测到 FacAging DDR 启动标记（session_id={sid}）: {tail}"
            )
        status = "started_online"

    return {
        "session_id": sid,
        "mode": parsed["mode"],
        "enabled_items": ",".join(parsed["enabled_items"]),
        "module_pass_count": parsed["effective_pass_count"],
        "apk_version": pf.get("apk_version", ""),
        "board": pf.get("board", ""),
        "build_id": pf.get("build", ""),
        "battery_level": pf.get("battery_level"),
        "started_marker": started_marker,
        "dst_marker": dst_marker,
        "dropped_adb": dropped_adb,
        "status": status,
    }


def main() -> None:
    cfg = params()
    try:
        metrics = _run(cfg)
    except Exception as exc:  # noqa: BLE001 — 脚本顶层统一输出结构化错误
        output_result(False, error_message=str(exc))
        sys.exit(1)
    output_result(True, metrics=metrics)


if __name__ == "__main__":
    main()
