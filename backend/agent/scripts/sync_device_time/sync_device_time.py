"""Synchronize Android device timezone and system wall clock to Agent host clock.

v1.0.0：稳定性测试前置强制校时（位于 check_device 之后、clean_env / aee_prepare 之前）。
实证背景（2026-11-25 全机群 16 机型抽样）：离线/无 NTP 设备普遍存在 3 小时至 747 天
不等的墙钟漂移，导致 AEE/mobilelog 目录名、ExpTime、跨 run 基线判定与人工对拍失真。

执行策略（极简两分支 + 回读闭环核验）：
  1. 主路径（Android 12–16+ 通用，无需 root、无需网络）：
     - ``cmd alarm set-timezone <IANA_TZ>``
     - ``cmd alarm set-time <host_epoch_ms>``
     实测覆盖 MTK/UNISOC × HONOR/Infinix × SDK 34–36 × uid=0/uid=2000，耗时 100–370ms，
     同时更新系统时钟、``persist.sys.timezone`` 与底层硬件 ``/dev/rtc0``，且不破坏
     ``auto_time`` / ``auto_time_zone`` 设置。
  2. 兜底路径（仅主路径失败或回读未达标且 ``allow_root_fallback=true`` 时触发，
     用于 Android ≤ 11 老版本或个别裁剪 ``Shell`` 权限的固件）：
     - 确认/尝试 ``adb root``（``id -u == 0``）
     - ``setprop persist.sys.timezone <IANA_TZ>``
     - ``date -u @<host_epoch_s>``
     - ``hwclock -w``（best-effort 写回硬件 RTC）
  3. 回读闭环核验（权威判据）：
     - 回读 ``getprop persist.sys.timezone`` 与 ``date +%s``
     - 必须满足 ``tz == target_tz`` 且 ``abs(dev_epoch - host_epoch) <= max_drift_seconds``；
       不达标即显式失败，不静默放行。

Environment:
    STP_DEVICE_SERIAL     (required) 目标设备串号；缺省直接失败
    STP_ADB_PATH          (optional, default: adb)
    STP_STEP_PARAMS       (optional, JSON):
        {
          "timezone": "Asia/Shanghai",    // 可选；缺省取 Agent 主机时区，回落 Asia/Shanghai
          "max_drift_seconds": 5,         // 可选；回读允许偏差上限（秒，默认 5，最小 1）
          "allow_root_fallback": true,    // 可选；cmd alarm 失败时是否尝试 root 兜底（默认 true）
          "command_timeout_seconds": 15   // 可选；单条 adb 命令超时（秒，默认 15，最小 3）
        }

Output (stdout JSON):
    {"success": true/false, "skipped": false, "error_message": "...", "metrics": {...}}
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_PROGRESS_PREFIX = "PROGRESS "
_DEFAULT_TIMEZONE = "Asia/Shanghai"
_DEFAULT_MAX_DRIFT_SECONDS = 5
_DEFAULT_COMMAND_TIMEOUT_SECONDS = 15
_ROOT_WAIT_DEADLINE_SECONDS = 15.0
_ROOT_WAIT_MAX_POLLS = 15

# 与 backend/services/agent_installer.py 的 agent_timezone 判据对齐（纯字符白名单，拒绝注入）
_TZ_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+){0,2}$")
_CMD_ERROR_MARKERS = ("securityexception", "unknown command", "permission denial", "exception")


def subprocess_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """间接层：单测可注入，避免全局替换 subprocess。"""
    return subprocess.run(argv, **kwargs)


def _emit_progress(seq: list[int], stage: str, **fields: Any) -> None:
    seq[0] += 1
    payload = {"seq": seq[0], "step": "sync_device_time", "stage": stage, **fields}
    sys.stderr.write(_PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def _output(success: bool, **kwargs: Any) -> None:
    payload = {"success": success, "skipped": False, **kwargs}
    print(json.dumps(payload, ensure_ascii=False))


def _step_params() -> dict[str, Any]:
    raw = os.environ.get("STP_STEP_PARAMS", "{}")
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _as_bool(value: Any, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _parse_positive_int(value: Any, *, name: str, default: int, minimum: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer >= {minimum} (got bool)")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer >= {minimum} (got {value!r})") from exc
    if parsed < minimum:
        raise ValueError(f"{name} must be >= {minimum} (got {parsed})")
    return parsed


def _detect_host_timezone() -> str:
    """读取 Agent 主机当前 IANA 时区，无法确认时回落 Asia/Shanghai。"""
    env_tz = (os.environ.get("TZ") or "").strip().lstrip(":")
    if env_tz and _TZ_RE.match(env_tz):
        return env_tz

    etc_tz = Path("/etc/timezone")
    try:
        if etc_tz.is_file():
            text = etc_tz.read_text(encoding="utf-8", errors="replace").strip()
            if text and _TZ_RE.match(text):
                return text
    except OSError:
        pass

    localtime = Path("/etc/localtime")
    try:
        if localtime.is_symlink():
            target = str(localtime.resolve())
            marker = "/zoneinfo/"
            if marker in target:
                candidate = target.split(marker, 1)[1].strip()
                if candidate and _TZ_RE.match(candidate):
                    return candidate
    except OSError:
        pass

    return _DEFAULT_TIMEZONE


def validated_timezone(raw: Any) -> str:
    """校验目标 IANA 时区；空值取主机时区，非法形态直接拒绝。"""
    if raw is None or str(raw).strip() == "":
        return _detect_host_timezone()
    tz = str(raw).strip()
    if not _TZ_RE.match(tz) or ".." in tz:
        raise ValueError(f"invalid timezone {tz!r}: must match IANA timezone token")
    return tz


def _adb_cmd(
    serial: str,
    adb_path: str,
    adb_args: list[str],
    timeout: int,
) -> tuple[int, str, str]:
    """执行单次 `adb -s <serial> <adb_args...>`，返回 (rc, stdout, stderr)。"""
    try:
        proc = subprocess_run(
            [adb_path, "-s", serial, *adb_args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return -1, "", f"TimeoutExpired({timeout}s)"
    except Exception as exc:  # noqa: BLE001 — 显式转成结构化失败
        return -1, "", f"{type(exc).__name__}: {exc}"
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _adb_shell(
    serial: str,
    adb_path: str,
    shell_args: list[str],
    timeout: int,
) -> tuple[int, str, str]:
    return _adb_cmd(serial, adb_path, ["shell", *shell_args], timeout=timeout)


def _last_nonempty_line(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _parse_int_or_none(text: str) -> int | None:
    token = _last_nonempty_line(text)
    if token and re.fullmatch(r"-?\d+", token):
        return int(token)
    return None


def _cmd_looks_failed(rc: int, stdout: str, stderr: str) -> bool:
    if rc != 0:
        return True
    combined = f"{stdout}\n{stderr}".lower()
    return any(marker in combined for marker in _CMD_ERROR_MARKERS)


def _snippet(stdout: str, stderr: str, limit: int = 200) -> str:
    combined = " ".join(f"{stdout} {stderr}".split())
    return combined[:limit]


def _read_device_state(
    serial: str,
    adb_path: str,
    timeout: int,
) -> dict[str, Any]:
    """一次读取设备基线诊断信息（uid/sdk/build/tz/epoch）。"""
    probe_script = (
        'echo "UID=$(id -u)"; '
        'echo "SDK=$(getprop ro.build.version.sdk)"; '
        'echo "BTYPE=$(getprop ro.build.type)"; '
        'echo "DBG=$(getprop ro.debuggable)"; '
        'echo "TZ=$(getprop persist.sys.timezone)"; '
        'echo "EPOCH=$(date +%s)"'
    )
    host_epoch = round(time.time())
    rc, stdout, stderr = _adb_shell(serial, adb_path, [probe_script], timeout=timeout)
    kv: dict[str, str] = {}
    for ln in stdout.splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            kv[k.strip()] = v.strip()
    dev_epoch = _parse_int_or_none(kv.get("EPOCH", ""))
    uid = _parse_int_or_none(kv.get("UID", ""))
    drift = (dev_epoch - host_epoch) if dev_epoch is not None else None
    return {
        "rc": rc,
        "error": _snippet(stdout, stderr) if rc != 0 else "",
        "uid": uid,
        "sdk": kv.get("SDK") or None,
        "build_type": kv.get("BTYPE") or None,
        "debuggable": kv.get("DBG") or None,
        "timezone": kv.get("TZ") or None,
        "dev_epoch": dev_epoch,
        "host_epoch": host_epoch,
        "drift_seconds": drift,
    }


def _verify_clock(
    serial: str,
    adb_path: str,
    target_tz: str,
    max_drift_seconds: int,
    timeout: int,
) -> dict[str, Any]:
    """回读设备时区与秒级时间戳并与主机比对。"""
    probe_script = 'echo "TZ=$(getprop persist.sys.timezone)"; echo "EPOCH=$(date +%s)"'
    host_epoch = round(time.time())
    rc, stdout, stderr = _adb_shell(serial, adb_path, [probe_script], timeout=timeout)
    kv: dict[str, str] = {}
    for ln in stdout.splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            kv[k.strip()] = v.strip()
    tz_after = kv.get("TZ") or ""
    dev_epoch = _parse_int_or_none(kv.get("EPOCH", ""))
    drift = (dev_epoch - host_epoch) if dev_epoch is not None else None
    tz_ok = rc == 0 and tz_after == target_tz
    time_ok = rc == 0 and drift is not None and abs(drift) <= max_drift_seconds
    return {
        "rc": rc,
        "error": _snippet(stdout, stderr) if rc != 0 else "",
        "timezone_after": tz_after or None,
        "dev_epoch_after": dev_epoch,
        "host_epoch_after": host_epoch,
        "drift_after_seconds": drift,
        "timezone_ok": tz_ok,
        "time_ok": time_ok,
        "ok": tz_ok and time_ok,
    }


def _wait_adbd_ready(serial: str, adb_path: str, timeout: int) -> bool:
    """adb root 后轮询 adbd 恢复（同时受墙钟截止与最大轮次双上界保护）。"""
    deadline = time.monotonic() + _ROOT_WAIT_DEADLINE_SECONDS
    for _ in range(_ROOT_WAIT_MAX_POLLS):
        rc, out, _ = _adb_cmd(serial, adb_path, ["get-state"], timeout=min(5, timeout))
        if rc == 0 and "device" in out:
            return True
        if time.monotonic() >= deadline:
            break
        time.sleep(1.0)
    return False


def _sync_via_cmd_alarm(
    serial: str,
    adb_path: str,
    target_tz: str,
    timeout: int,
    commands: list[dict[str, Any]],
) -> tuple[bool, str]:
    """主路径：通过 AlarmManagerService shell 命令同步时区与毫秒时间戳。"""
    rc_tz, out_tz, err_tz = _adb_shell(
        serial, adb_path, ["cmd", "alarm", "set-timezone", target_tz], timeout=timeout
    )
    tz_failed = _cmd_looks_failed(rc_tz, out_tz, err_tz)
    commands.append({
        "name": "cmd-alarm-set-timezone",
        "rc": rc_tz,
        "ok": not tz_failed,
        "tail": _snippet(out_tz, err_tz),
    })

    host_ms = int(time.time() * 1000)
    rc_t, out_t, err_t = _adb_shell(
        serial, adb_path, ["cmd", "alarm", "set-time", str(host_ms)], timeout=timeout
    )
    time_failed = _cmd_looks_failed(rc_t, out_t, err_t)
    commands.append({
        "name": "cmd-alarm-set-time",
        "rc": rc_t,
        "ok": not time_failed,
        "tail": _snippet(out_t, err_t),
    })

    if tz_failed or time_failed:
        reasons = []
        if tz_failed:
            reasons.append(f"set-timezone(rc={rc_tz}): {_snippet(out_tz, err_tz)}")
        if time_failed:
            reasons.append(f"set-time(rc={rc_t}): {_snippet(out_t, err_t)}")
        return False, "; ".join(reasons)
    return True, ""


def _sync_via_root_fallback(
    serial: str,
    adb_path: str,
    target_tz: str,
    current_uid: int | None,
    timeout: int,
    commands: list[dict[str, Any]],
) -> tuple[bool, str]:
    """兜底路径（Android ≤ 11 或裁剪权限固件）：root 下 setprop + date + hwclock。"""
    is_root = current_uid == 0
    if not is_root:
        rc_r, out_r, err_r = _adb_cmd(serial, adb_path, ["root"], timeout=timeout)
        commands.append({
            "name": "adb-root",
            "rc": rc_r,
            "ok": rc_r == 0,
            "tail": _snippet(out_r, err_r),
        })
        if rc_r == 0:
            _wait_adbd_ready(serial, adb_path, timeout)
        rc_id, out_id, err_id = _adb_shell(serial, adb_path, ["id", "-u"], timeout=timeout)
        uid_after = _parse_int_or_none(out_id)
        is_root = rc_id == 0 and uid_after == 0
        commands.append({
            "name": "check-root-uid",
            "rc": rc_id,
            "ok": is_root,
            "tail": _snippet(out_id, err_id),
        })
        if not is_root:
            return False, f"adb root unavailable for fallback (uid={uid_after}, root_out={_snippet(out_r, err_r)!r})"

    rc_tz, out_tz, err_tz = _adb_shell(
        serial, adb_path, ["setprop", "persist.sys.timezone", target_tz], timeout=timeout
    )
    commands.append({
        "name": "root-setprop-timezone",
        "rc": rc_tz,
        "ok": rc_tz == 0,
        "tail": _snippet(out_tz, err_tz),
    })

    host_s = int(time.time())
    rc_t, out_t, err_t = _adb_shell(
        serial, adb_path, ["date", "-u", f"@{host_s}"], timeout=timeout
    )
    commands.append({
        "name": "root-date-set",
        "rc": rc_t,
        "ok": rc_t == 0,
        "tail": _snippet(out_t, err_t),
    })

    # best-effort 写硬件 RTC，避免重启类专项回退
    rc_hw, out_hw, err_hw = _adb_shell(serial, adb_path, ["hwclock", "-w"], timeout=timeout)
    commands.append({
        "name": "root-hwclock-write",
        "rc": rc_hw,
        "ok": rc_hw == 0,
        "tail": _snippet(out_hw, err_hw),
    })

    if rc_tz != 0 or rc_t != 0:
        reasons = []
        if rc_tz != 0:
            reasons.append(f"setprop(rc={rc_tz}): {_snippet(out_tz, err_tz)}")
        if rc_t != 0:
            reasons.append(f"date(rc={rc_t}): {_snippet(out_t, err_t)}")
        return False, "; ".join(reasons)
    return True, ""


def main() -> None:
    started = time.monotonic()
    seq = [0]
    serial = (os.environ.get("STP_DEVICE_SERIAL") or "").strip()
    adb_path = (os.environ.get("STP_ADB_PATH") or "adb").strip() or "adb"

    if not serial:
        _output(
            False,
            error_message="STP_DEVICE_SERIAL not set; refusing blanket execution",
            metrics={"duration_seconds": 0.0},
        )
        return

    args = _step_params()
    try:
        target_tz = validated_timezone(args.get("timezone"))
        max_drift = _parse_positive_int(
            args.get("max_drift_seconds"),
            name="max_drift_seconds",
            default=_DEFAULT_MAX_DRIFT_SECONDS,
            minimum=1,
        )
        cmd_timeout = _parse_positive_int(
            args.get("command_timeout_seconds"),
            name="command_timeout_seconds",
            default=_DEFAULT_COMMAND_TIMEOUT_SECONDS,
            minimum=3,
        )
    except ValueError as exc:
        _output(False, error_message=f"Invalid step params: {exc}", metrics={"serial": serial})
        return

    allow_root_fallback = _as_bool(args.get("allow_root_fallback"), default=True)
    commands: list[dict[str, Any]] = []

    # 1. 基线探测
    _emit_progress(seq, "probe-before", target_timezone=target_tz)
    before = _read_device_state(serial, adb_path, cmd_timeout)
    if before["rc"] != 0:
        _output(
            False,
            error_message=f"Failed to probe device {serial} before time sync: {before['error']}",
            metrics={
                "serial": serial,
                "target_timezone": target_tz,
                "duration_seconds": round(time.monotonic() - started, 3),
            },
        )
        return

    # 2. 主路径：cmd alarm
    _emit_progress(seq, "sync-alarm", target_timezone=target_tz)
    method = "cmd_alarm"
    alarm_ok, alarm_err = _sync_via_cmd_alarm(serial, adb_path, target_tz, cmd_timeout, commands)

    # 3. 回读核验
    _emit_progress(seq, "verify", method=method)
    verify = _verify_clock(serial, adb_path, target_tz, max_drift, cmd_timeout)

    # 4. 兜底路径（仅主路径未达标且允许兜底）
    fallback_err = ""
    if not verify["ok"] and allow_root_fallback:
        _emit_progress(seq, "sync-root-fallback", reason=alarm_err or "verify_mismatch")
        method = "root_fallback"
        fb_ok, fallback_err = _sync_via_root_fallback(
            serial, adb_path, target_tz, before["uid"], cmd_timeout, commands
        )
        if fb_ok:
            _emit_progress(seq, "verify-fallback", method=method)
            verify = _verify_clock(serial, adb_path, target_tz, max_drift, cmd_timeout)

    duration = round(time.monotonic() - started, 3)
    metrics: dict[str, Any] = {
        "serial": serial,
        "method": method if verify["ok"] else "failed",
        "target_timezone": target_tz,
        "timezone_before": before["timezone"],
        "timezone_after": verify["timezone_after"],
        "drift_before_seconds": before["drift_seconds"],
        "drift_after_seconds": verify["drift_after_seconds"],
        "max_drift_seconds": max_drift,
        "device_uid": before["uid"],
        "device_sdk": before["sdk"],
        "device_build_type": before["build_type"],
        "device_debuggable": before["debuggable"],
        "alarm_cmd_ok": alarm_ok,
        "duration_seconds": duration,
        "commands": commands,
    }
    _emit_progress(seq, "done", ok=verify["ok"], method=metrics["method"])

    if not verify["ok"]:
        detail_parts = [
            f"tz={verify['timezone_after']!r} (want {target_tz!r})",
            f"drift={verify['drift_after_seconds']}s (max ±{max_drift}s)",
            f"sdk={before['sdk']} build={before['build_type']}/{before['debuggable']} uid={before['uid']}",
        ]
        if alarm_err:
            detail_parts.append(f"cmd_alarm=[{alarm_err}]")
        if fallback_err:
            detail_parts.append(f"fallback=[{fallback_err}]")
        if verify["error"]:
            detail_parts.append(f"verify_err=[{verify['error']}]")
        _output(
            False,
            error_message="Device time sync verification failed: " + "; ".join(detail_parts),
            metrics=metrics,
        )
        return

    _output(True, metrics=metrics)


if __name__ == "__main__":
    main()
