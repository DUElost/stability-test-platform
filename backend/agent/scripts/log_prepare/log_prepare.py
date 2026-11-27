#!/usr/bin/env python3
"""Unified device continuous-logging preparation script (`log_prepare` v1.0.0, #4182 Phase 1).

Configures and verifies continuous AP/system logging across SoC platforms through a
single parameterized entrypoint, routing internally by detected chipset & firmware
capabilities:

- **MTK (`com.debug.loggerui` / `mobile_log_d`)**:
  - Configures `mobilelog` capacity (`set_total_log_size_<MB>`), optional `auto_start`,
    optional `mtk_sublog_cmd`, and starts logger via `com.debug.loggerui.ADB_CMD`.
  - Optionally sets & verifies `persist.vendor.mtk.aee.mode` ONLY when `aee_mode` is
    explicitly provided (default `null` preserves the device's current AEE mode).
  - Verifies authoritative readback via `vendor.MB.running == "1"`, `vendor.MB.realpath`,
    `vendor.MB.sublog`, and (when root) `com.debug.loggerui` `mobilelog_logsize` prefs.
  - Restores `development_settings_enabled` to its pre-run value in `finally`.

- **UNISOC / SPRD (`/product/bin/ylogctl`, YLog Server 5.x)**:
  - Does NOT touch Full Sysdump (managed independently by `unisoc_sysdump_config`).
  - Configures log rotation (`ylogctl rotatelog enable|disable`), AP log folder cap
    (`ylogctl aplogmaxsize <MB>`), and enables logging (`ylogctl enable`).
  - Parses `ylogctl query` & `ylogctl space` to verify BOTH `status=enable`,
    `APLogMaxSize == ylog_size_mb`, AND that required sub-streams (default
    `["android", "kernel"]`) are actually `Open` (guarding against the A57/ZTE trap
    where `ylogctl enable` leaves `android`/`kernel` in `Close` state).
  - When required sub-streams are `Close` and `ylog_recover_substreams=true`:
    1) On `userdebug`/`eng`: runs `ylogctl resetsettings` then re-applies rotate/cap/enable;
    2) On `user` (or if still `Close`): updates `/data/ylog/ylog.conf` and restarts `ylog`
       (elevating via `adb root` if needed), failing fast if required streams remain `Close`.

Ordering note (#4182):
    For specialties with log-cleanup steps (`monkey_setup`, `gpu_setup`, `clean_env`),
    run cleanup FIRST and place `log_prepare` AFTER cleanup so freshly started log
    directories are not wiped.

Environment:
    STP_DEVICE_SERIAL   (required) Target ADB serial.
    STP_ADB_PATH        (optional) ADB executable path (default: "adb").
    STP_STEP_PARAMS     (optional JSON):
        {
          "platform": "auto",                     // "auto" | "mtk" | "unisoc"
          "mobilelog_size_mb": 4096,              // MTK total log size in MB (default: 4096)
          "mtk_auto_start": true,                 // MTK logger auto-start (default: true)
          "mtk_sublog_cmd": null,                 // optional e.g. "set_sublog_4_5_0"
          "aee_mode": null,                       // optional e.g. "3"; null = keep current
          "enable_dev_settings_temporarily": true,// MTK dev settings guard (default: true)
          "ylog_size_mb": 4096,                   // UNISOC aplogmaxsize in MB (64..10240)
          "ylog_rotate": true,                    // UNISOC rotatelog enable/disable
          "ylog_required_streams": ["android", "kernel"],
          "ylog_recover_substreams": true,
          "verify_timeout_seconds": 15,
          "root_timeout_seconds": 15,
          "strict_unsupported": false
        }

Output (stdout):
    Single JSON line: {"success": bool, "skipped": bool, ...}
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any

_PROGRESS_PREFIX = "PROGRESS "

_UNISOC_SOC_MFRS = frozenset({"unisoc", "spreadtrum", "sprd"})
_UNISOC_PLATFORM_PREFIXES = (
    "ums",
    "sp9",
    "sp7",
    "ud7",
    "sc9",
    "sc7",
    "s9863",
    "qogir",
    "shark",
    "pike",
    "orca",
)
_MTK_SOC_MFRS = frozenset({"mediatek", "mtk"})

_AEE_MODE_PROP = "persist.vendor.mtk.aee.mode"
_AEEV_MODE_PROP = "persist.vendor.mtk.aeev.mode"
_LOGGERUI_PKG = "com.debug.loggerui"
_LOGGERUI_RECEIVER = f"{_LOGGERUI_PKG}/.framework.LogReceiver"
_LOGGERUI_ACTION = "com.debug.loggerui.ADB_CMD"
_MTK_SUBLOG_CMD_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_AEE_MODE_VAL_RE = re.compile(r"^[0-9]{1,2}$")

_YLOG_CONF_PATH = "/data/ylog/ylog.conf"
_YLOG_STREAM_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_YLOG_STREAM_LINE_RE = re.compile(
    r"^\[\s*\d+\]\s+([A-Za-z0-9_]+)\s*->\s*(Open|Close)\s*->",
)
_YLOG_SPACE_KV_RE = re.compile(r"(Root|APLogSize|APLogMaxSize|DiskFreeSpace|DiskReserved):(\S*)")
_MB_LOGSIZE_XML_RE = re.compile(r'name="mobilelog_logsize">([^<]+)<')
_MB_AUTOSTART_XML_RE = re.compile(r'name="mobilelog_autostart"\s+value="([^"]+)"')


def _adb() -> str:
    return (os.environ.get("STP_ADB_PATH") or "").strip() or shutil.which("adb") or "adb"


def _serial() -> str:
    return (os.environ.get("STP_DEVICE_SERIAL") or "").strip()


def _params() -> dict[str, Any]:
    raw = (os.environ.get("STP_STEP_PARAMS") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return default


def _as_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _as_float(value: Any, default: float, *, minimum: float = 0.0) -> float:
    if value is None or str(value).strip() == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def _as_stream_list(value: Any, default: tuple[str, ...]) -> list[str]:
    if value is None:
        return list(default)
    if not isinstance(value, (list, tuple)):
        return list(default)
    out: list[str] = []
    for item in value:
        name = str(item or "").strip().lower()
        if name and _YLOG_STREAM_NAME_RE.fullmatch(name) and name not in out:
            out.append(name)
    return out


def _emit_progress(seq: list[int], stage: str, **fields: Any) -> None:
    seq[0] += 1
    payload: dict[str, Any] = {
        "seq": seq[0],
        "step": "log_prepare",
        "stage": stage,
        **fields,
    }
    sys.stderr.write(_PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def _output(
    success: bool,
    *,
    skipped: bool = False,
    skip_reason: str | None = None,
    error_message: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {"success": success, "skipped": skipped}
    if skip_reason is not None:
        payload["skip_reason"] = skip_reason
    if error_message is not None:
        payload["error_message"] = error_message
    if metrics is not None:
        payload["metrics"] = metrics
    print(json.dumps(payload, ensure_ascii=False))


def _run_adb(serial: str, args: list[str], timeout: float = 20.0) -> tuple[int, str, str]:
    cmd = [_adb(), "-s", serial, *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout:.1f}s"
    except Exception as exc:  # noqa: BLE001 - agent script contract emits JSON
        return -1, "", f"{type(exc).__name__}: {exc}"


def _shell(serial: str, command: str, timeout: float = 20.0) -> tuple[int, str, str]:
    return _run_adb(serial, ["shell", command], timeout=timeout)


def _probe_device(serial: str) -> tuple[bool, dict[str, str], str]:
    shell_cmd = (
        "echo board_platform=$(getprop ro.board.platform);"
        "echo soc_manufacturer=$(getprop ro.soc.manufacturer);"
        "echo hardware=$(getprop ro.hardware);"
        "echo boot_hardware=$(getprop ro.boot.hardware);"
        "echo build_type=$(getprop ro.build.type);"
        "echo uid=$(id -u);"
        "echo has_ylogctl=$(if [ -x /product/bin/ylogctl ] || command -v ylogctl >/dev/null 2>&1; then echo 1; else echo 0; fi);"
        f"echo has_loggerui=$(if pm path {_LOGGERUI_PKG} >/dev/null 2>&1; then echo 1; else echo 0; fi)"
    )
    rc, out, err = _shell(serial, shell_cmd, timeout=15.0)
    if rc != 0:
        return False, {}, f"platform probe failed (rc={rc}): {err or out}"
    info: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = v.strip()
    return True, info, ""


def _detect_platform(info: dict[str, str]) -> str:
    board = (info.get("board_platform") or "").lower()
    soc_mfr = (info.get("soc_manufacturer") or "").lower()
    hardware = (info.get("hardware") or "").lower()
    boot_hw = (info.get("boot_hardware") or "").lower()

    if soc_mfr in _UNISOC_SOC_MFRS or any(
        val.startswith(_UNISOC_PLATFORM_PREFIXES) for val in (board, hardware, boot_hw)
    ):
        return "unisoc"
    if soc_mfr in _MTK_SOC_MFRS or any(
        val.startswith("mt") for val in (board, hardware, boot_hw)
    ):
        return "mtk"
    if info.get("has_ylogctl") == "1":
        return "unisoc"
    if info.get("has_loggerui") == "1":
        return "mtk"
    return "unknown"


def _ensure_root(serial: str, current_uid: str, timeout_seconds: float) -> tuple[bool, str]:
    if current_uid == "0":
        return True, ""
    rc, out, err = _run_adb(serial, ["root"], timeout=15.0)
    if rc != 0 and "already running as root" not in (out + " " + err).lower():
        return False, f"adb root failed (rc={rc}): {err or out}"
    deadline = time.monotonic() + timeout_seconds
    last_err = ""
    while time.monotonic() < deadline:
        rc_state, state_out, state_err = _run_adb(serial, ["get-state"], timeout=5.0)
        if rc_state == 0 and "device" in state_out:
            rc_uid, uid_out, uid_err = _shell(serial, "id -u", timeout=5.0)
            if rc_uid == 0 and uid_out.strip() == "0":
                return True, ""
            last_err = f"uid={uid_out.strip()!r} err={uid_err}"
        else:
            last_err = f"state={state_out!r} err={state_err}"
        time.sleep(0.5)
    return False, f"adb root did not converge to uid=0 within {timeout_seconds:.1f}s ({last_err})"


# ── MTK Handler ──────────────────────────────────────────────────────────────


def _read_mtk_state(serial: str, include_prefs: bool = False) -> dict[str, Any]:
    shell_cmd = (
        f"echo aee_mode=$(getprop {_AEE_MODE_PROP});"
        f"echo aeev_mode=$(getprop {_AEEV_MODE_PROP});"
        "echo mb_running=$(getprop vendor.MB.running);"
        "echo mb_realpath=$(getprop vendor.MB.realpath);"
        "echo mb_sublog=$(getprop vendor.MB.sublog)"
    )
    rc, out, _ = _shell(serial, shell_cmd, timeout=15.0)
    state: dict[str, Any] = {
        "aee_mode": None,
        "aeev_mode": None,
        "mb_running": None,
        "mb_realpath": None,
        "mb_sublog": None,
    }
    if rc == 0:
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                state[k.strip()] = v.strip() or None

    if include_prefs:
        rc_p, out_p, _ = _shell(
            serial,
            f"cat /data/user/0/{_LOGGERUI_PKG}/shared_prefs/*.xml 2>/dev/null || true",
            timeout=10.0,
        )
        if rc_p == 0 and out_p:
            m_size = _MB_LOGSIZE_XML_RE.search(out_p)
            m_auto = _MB_AUTOSTART_XML_RE.search(out_p)
            if m_size:
                state["mobilelog_logsize_pref"] = m_size.group(1).strip()
            if m_auto:
                state["mobilelog_autostart_pref"] = m_auto.group(1).strip()
    return state


def _send_mtk_broadcast(serial: str, cmd_name: str) -> tuple[bool, str]:
    rc, out, err = _run_adb(
        serial,
        [
            "shell",
            "am",
            "broadcast",
            "-a",
            _LOGGERUI_ACTION,
            "-e",
            "cmd_name",
            cmd_name,
            "--ei",
            "cmd_target",
            "1",
            "-n",
            _LOGGERUI_RECEIVER,
        ],
        timeout=20.0,
    )
    ok = rc == 0 and "result=0" in out
    detail = f"rc={rc} out={out[:120]}" if not ok else "ok"
    if err and not ok:
        detail += f" err={err[:80]}"
    return ok, detail


def _prepare_mtk(
    serial: str,
    probe_info: dict[str, str],
    cfg: dict[str, Any],
    seq: list[int],
) -> None:
    mobilelog_size_mb = _as_int(cfg.get("mobilelog_size_mb"), 4096, minimum=256, maximum=65536)
    mtk_auto_start = _as_bool(cfg.get("mtk_auto_start"), True)
    raw_sublog = cfg.get("mtk_sublog_cmd")
    mtk_sublog_cmd = str(raw_sublog).strip() if raw_sublog not in (None, "") else None
    raw_aee = cfg.get("aee_mode")
    target_aee_mode = str(raw_aee).strip() if raw_aee not in (None, "") else None
    toggle_dev = _as_bool(cfg.get("enable_dev_settings_temporarily"), True)
    verify_timeout = _as_float(cfg.get("verify_timeout_seconds"), 15.0, minimum=1.0)
    root_timeout = _as_float(cfg.get("root_timeout_seconds"), 15.0, minimum=1.0)
    strict_unsupported = _as_bool(cfg.get("strict_unsupported"), False)

    if mtk_sublog_cmd and not _MTK_SUBLOG_CMD_RE.fullmatch(mtk_sublog_cmd):
        _output(
            False,
            error_message=f"invalid mtk_sublog_cmd: {mtk_sublog_cmd!r}",
            metrics={"detected_platform": "mtk"},
        )
        return
    if target_aee_mode is not None and not _AEE_MODE_VAL_RE.fullmatch(target_aee_mode):
        _output(
            False,
            error_message=f"invalid aee_mode: {target_aee_mode!r}",
            metrics={"detected_platform": "mtk"},
        )
        return

    metrics: dict[str, Any] = {
        "detected_platform": "mtk",
        "board_platform": probe_info.get("board_platform") or "",
        "build_type": probe_info.get("build_type") or "",
        "has_loggerui": probe_info.get("has_loggerui") == "1",
        "target_mobilelog_size_mb": mobilelog_size_mb,
        "target_aee_mode": target_aee_mode,
        "warnings": [],
    }

    if probe_info.get("has_loggerui") != "1":
        if strict_unsupported:
            _output(
                False,
                error_message=f"{_LOGGERUI_PKG} not installed on MTK device",
                metrics=metrics,
            )
        else:
            _output(
                True,
                skipped=True,
                skip_reason="mtk_loggerui_missing",
                metrics=metrics,
            )
        return

    is_root = probe_info.get("uid") == "0"
    _emit_progress(seq, "mtk-read-before")
    before = _read_mtk_state(serial, include_prefs=is_root)
    metrics["before"] = before

    dev_before: str | None = None
    dev_enabled_by_us = False
    try:
        if toggle_dev:
            rc_d, out_d, _ = _shell(serial, "settings get global development_settings_enabled", timeout=10.0)
            dev_before = out_d.strip() if rc_d == 0 else None
            if dev_before != "1":
                _emit_progress(seq, "mtk-dev-settings-on")
                _shell(serial, "settings put global development_settings_enabled 1", timeout=10.0)
                dev_enabled_by_us = True

        # Only change AEE mode when explicitly requested by caller/project config.
        if target_aee_mode is not None:
            if before.get("aee_mode") != target_aee_mode:
                _emit_progress(seq, "mtk-adb-root")
                root_ok, root_err = _ensure_root(serial, probe_info.get("uid") or "", root_timeout)
                if not root_ok:
                    _output(
                        False,
                        error_message=f"failed to elevate for aee_mode={target_aee_mode}: {root_err}",
                        metrics=metrics,
                    )
                    return
                is_root = True
                _emit_progress(seq, "mtk-set-aee-mode", target=target_aee_mode)
                rc_set, out_set, err_set = _shell(
                    serial,
                    f"setprop {_AEE_MODE_PROP} {target_aee_mode}",
                    timeout=10.0,
                )
                rc_get, after_aee, _ = _shell(serial, f"getprop {_AEE_MODE_PROP}", timeout=10.0)
                after_aee_val = after_aee.strip() or None
                metrics["aee_mode_after"] = after_aee_val
                if rc_set != 0 or rc_get != 0 or after_aee_val != target_aee_mode:
                    _output(
                        False,
                        error_message=(
                            f"setprop {_AEE_MODE_PROP}={target_aee_mode} failed: "
                            f"rc={rc_set}/{rc_get} after={after_aee_val!r} {err_set or out_set}"
                        ).strip(),
                        metrics=metrics,
                    )
                    return
            else:
                metrics["aee_mode_after"] = before.get("aee_mode")
                metrics["aee_mode_already_matched"] = True

        # Configure & start MTK mobilelog via com.debug.loggerui broadcasts
        cmds = [
            f"set_total_log_size_{mobilelog_size_mb}",
            f"set_auto_start_{'1' if mtk_auto_start else '0'}",
        ]
        if mtk_sublog_cmd:
            cmds.append(mtk_sublog_cmd)
        cmds.append("start")

        broadcast_results: dict[str, bool] = {}
        for cmd_name in cmds:
            _emit_progress(seq, "mtk-logger-broadcast", cmd=cmd_name)
            ok, detail = _send_mtk_broadcast(serial, cmd_name)
            broadcast_results[cmd_name] = ok
            if not ok:
                metrics["warnings"].append(f"broadcast {cmd_name}: {detail}")
        metrics["logger_broadcasts"] = broadcast_results

        # Poll authoritative readback: vendor.MB.running == "1"
        _emit_progress(seq, "mtk-verify-readback")
        deadline = time.monotonic() + verify_timeout
        after = _read_mtk_state(serial, include_prefs=is_root)
        while after.get("mb_running") != "1" and time.monotonic() < deadline:
            time.sleep(0.5)
            after = _read_mtk_state(serial, include_prefs=is_root)
        metrics["after"] = after

        if after.get("mb_running") != "1":
            _output(
                False,
                error_message=(
                    f"MTK mobilelog verification failed: vendor.MB.running={after.get('mb_running')!r} "
                    f"(expected '1') after {verify_timeout:.1f}s"
                ),
                metrics=metrics,
            )
            return

        _output(True, metrics=metrics)
    finally:
        if dev_enabled_by_us:
            _emit_progress(seq, "mtk-dev-settings-restore")
            restore_val = dev_before if dev_before in ("0", "1") else "0"
            _shell(serial, f"settings put global development_settings_enabled {restore_val}", timeout=10.0)


# ── UNISOC Handler ───────────────────────────────────────────────────────────


def _parse_ylog_query(text: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {
        "status": None,
        "file": None,
        "size_bytes": None,
        "pid": None,
        "streams": {},
    }
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("status ="):
            parsed["status"] = line.split("=", 1)[1].strip().lower()
        elif line.startswith("file ="):
            val = line.split("=", 1)[1].strip()
            parsed["file"] = None if val.upper() == "NULL" else val
        elif line.startswith("size ="):
            val = line.split("=", 1)[1].strip()
            try:
                parsed["size_bytes"] = int(val)
            except ValueError:
                parsed["size_bytes"] = None
        elif line.startswith("pid ="):
            val = line.split("=", 1)[1].strip()
            try:
                parsed["pid"] = int(val)
            except ValueError:
                parsed["pid"] = None
        else:
            m = _YLOG_STREAM_LINE_RE.match(line)
            if m:
                parsed["streams"][m.group(1).lower()] = m.group(2)
    return parsed


def _parse_ylog_space(text: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {
        "root": None,
        "aplog_size_mb": None,
        "aplog_max_size_mb": None,
        "disk_free_mb": None,
        "disk_reserved_mb": None,
    }
    key_map = {
        "Root": ("root", str),
        "APLogSize": ("aplog_size_mb", int),
        "APLogMaxSize": ("aplog_max_size_mb", int),
        "DiskFreeSpace": ("disk_free_mb", int),
        "DiskReserved": ("disk_reserved_mb", int),
    }
    for k, raw_val in _YLOG_SPACE_KV_RE.findall(text):
        dest, conv = key_map[k]
        if conv is str:
            parsed[dest] = raw_val or None
        else:
            try:
                parsed[dest] = int(raw_val)
            except ValueError:
                parsed[dest] = None
    return parsed


def _read_unisoc_state(serial: str) -> dict[str, Any]:
    rc_q, out_q, err_q = _shell(serial, "ylogctl query", timeout=15.0)
    rc_s, out_s, err_s = _shell(serial, "ylogctl space", timeout=15.0)
    query = _parse_ylog_query(out_q) if rc_q == 0 else _parse_ylog_query("")
    space = _parse_ylog_space(out_s) if rc_s == 0 else _parse_ylog_space("")
    return {
        "query_rc": rc_q,
        "space_rc": rc_s,
        "query_err": err_q if rc_q != 0 else "",
        "space_err": err_s if rc_s != 0 else "",
        **query,
        **space,
    }


def _missing_open_streams(state: dict[str, Any], required_streams: list[str]) -> list[str]:
    streams = state.get("streams") or {}
    return [name for name in required_streams if streams.get(name) != "Open"]


def _apply_ylog_settings(serial: str, ylog_size_mb: int, ylog_rotate: bool) -> list[str]:
    warnings: list[str] = []
    rotate_arg = "enable" if ylog_rotate else "disable"
    for cmd in (
        f"ylogctl rotatelog {rotate_arg}",
        f"ylogctl aplogmaxsize {ylog_size_mb}",
        "ylogctl enable",
    ):
        rc, out, err = _shell(serial, cmd, timeout=15.0)
        if rc != 0 or "wrong cmd" in out.lower():
            warnings.append(f"{cmd}: rc={rc} out={out[:80]} err={err[:80]}")
    return warnings


def _rewrite_ylog_conf_and_restart(
    serial: str,
    required_streams: list[str],
    ylog_size_mb: int,
    ylog_rotate: bool,
) -> tuple[bool, str]:
    updates = {
        "status": "enable",
        "aplogrotate": "enable" if ylog_rotate else "disable",
        "aplogmaxsize": str(ylog_size_mb),
    }
    for s in required_streams:
        updates[s] = "1"

    rc_read, conf_text, err_read = _shell(serial, f"cat {_YLOG_CONF_PATH}", timeout=10.0)
    if rc_read != 0 or not conf_text.strip():
        return False, f"cannot read {_YLOG_CONF_PATH} (rc={rc_read}): {err_read}"

    new_lines: list[str] = []
    seen_keys: set[str] = set()
    for line in conf_text.splitlines():
        if "," in line:
            k, _ = line.split(",", 1)
            key = k.strip()
            if key in updates:
                new_lines.append(f"{key},{updates[key]}")
                seen_keys.add(key)
                continue
        new_lines.append(line)
    for k, v in updates.items():
        if k not in seen_keys:
            new_lines.append(f"{k},{v}")

    payload = "\n".join(new_lines) + "\n"
    # Safe single-quoted heredoc write (values are strictly validated alnum/int).
    write_cmd = (
        f"cat <<'__STP_YLOG_CONF__' > {_YLOG_CONF_PATH}\n{payload}__STP_YLOG_CONF__\n"
        "chmod 666 /data/ylog/ylog.conf 2>/dev/null || true\n"
        "stop ylog && start ylog"
    )
    rc_w, out_w, err_w = _shell(serial, write_cmd, timeout=20.0)
    if rc_w != 0:
        return False, f"rewrite {_YLOG_CONF_PATH} & restart ylog failed (rc={rc_w}): {err_w or out_w}"
    return True, ""


def _unisoc_converged(
    state: dict[str, Any],
    required_streams: list[str],
    expected_max_size_mb: int,
) -> bool:
    if state.get("status") != "enable":
        return False
    if not state.get("file"):
        return False
    if _missing_open_streams(state, required_streams):
        return False
    cap = state.get("aplog_max_size_mb")
    if cap is None or cap != expected_max_size_mb:
        return False
    return True


def _prepare_unisoc(
    serial: str,
    probe_info: dict[str, str],
    cfg: dict[str, Any],
    seq: list[int],
) -> None:
    ylog_size_mb = _as_int(cfg.get("ylog_size_mb"), 4096, minimum=64, maximum=10240)
    ylog_rotate = _as_bool(cfg.get("ylog_rotate"), True)
    required_streams = _as_stream_list(cfg.get("ylog_required_streams"), ("android", "kernel"))
    recover_substreams = _as_bool(cfg.get("ylog_recover_substreams"), True)
    verify_timeout = _as_float(cfg.get("verify_timeout_seconds"), 15.0, minimum=1.0)
    root_timeout = _as_float(cfg.get("root_timeout_seconds"), 15.0, minimum=1.0)
    strict_unsupported = _as_bool(cfg.get("strict_unsupported"), False)
    build_type = (probe_info.get("build_type") or "").lower()

    metrics: dict[str, Any] = {
        "detected_platform": "unisoc",
        "board_platform": probe_info.get("board_platform") or "",
        "build_type": build_type,
        "has_ylogctl": probe_info.get("has_ylogctl") == "1",
        "target_ylog_size_mb": ylog_size_mb,
        "target_ylog_rotate": ylog_rotate,
        "required_streams": required_streams,
        "substream_recovery_method": "none",
        "warnings": [],
    }

    if probe_info.get("has_ylogctl") != "1":
        if strict_unsupported:
            _output(
                False,
                error_message="ylogctl not available on UNISOC device",
                metrics=metrics,
            )
        else:
            _output(
                True,
                skipped=True,
                skip_reason="unisoc_ylogctl_missing",
                metrics=metrics,
            )
        return

    rc_ver, ver_out, _ = _shell(serial, "ylogctl version", timeout=10.0)
    if rc_ver == 0 and ver_out:
        metrics["ylog_version"] = ver_out.strip()

    _emit_progress(seq, "unisoc-read-before")
    before = _read_unisoc_state(serial)
    metrics["before"] = before

    _emit_progress(seq, "unisoc-apply-ylogctl", size_mb=ylog_size_mb)
    metrics["warnings"].extend(_apply_ylog_settings(serial, ylog_size_mb, ylog_rotate))

    after = _read_unisoc_state(serial)
    missing = _missing_open_streams(after, required_streams)

    # Guard against A57/ZTE trap where `ylogctl enable` leaves android/kernel Closed.
    if missing and recover_substreams:
        if build_type in ("userdebug", "eng"):
            _emit_progress(seq, "unisoc-recover-resetsettings", missing=missing)
            rc_rst, out_rst, err_rst = _shell(serial, "ylogctl resetsettings", timeout=15.0)
            if rc_rst != 0:
                metrics["warnings"].append(f"ylogctl resetsettings failed (rc={rc_rst}): {err_rst or out_rst}")
            # resetsettings restores aplogmaxsize to 99%, so re-apply cap/rotate/enable.
            metrics["warnings"].extend(_apply_ylog_settings(serial, ylog_size_mb, ylog_rotate))
            metrics["substream_recovery_method"] = "resetsettings"
            after = _read_unisoc_state(serial)
            missing = _missing_open_streams(after, required_streams)

        if missing:
            _emit_progress(seq, "unisoc-recover-ylog-conf", missing=missing)
            root_ok, root_err = _ensure_root(serial, probe_info.get("uid") or "", root_timeout)
            if root_ok:
                ok_rw, err_rw = _rewrite_ylog_conf_and_restart(
                    serial, required_streams, ylog_size_mb, ylog_rotate,
                )
                if ok_rw:
                    metrics["substream_recovery_method"] = "ylog_conf_restart"
                else:
                    metrics["warnings"].append(err_rw)
            else:
                metrics["warnings"].append(f"cannot elevate for ylog.conf recovery: {root_err}")

    _emit_progress(seq, "unisoc-verify-readback")
    deadline = time.monotonic() + verify_timeout
    after = _read_unisoc_state(serial)
    while not _unisoc_converged(after, required_streams, ylog_size_mb) and time.monotonic() < deadline:
        time.sleep(0.5)
        after = _read_unisoc_state(serial)
    metrics["after"] = after

    if not _unisoc_converged(after, required_streams, ylog_size_mb):
        still_missing = _missing_open_streams(after, required_streams)
        _output(
            False,
            error_message=(
                f"UNISOC ylog verification failed: status={after.get('status')!r}, "
                f"aplog_max_size_mb={after.get('aplog_max_size_mb')!r} (expected {ylog_size_mb}), "
                f"file={after.get('file')!r}, unopened_streams={still_missing}"
            ),
            metrics=metrics,
        )
        return

    _output(True, metrics=metrics)


def main() -> int:
    seq = [0]
    serial = _serial()
    if not serial:
        _output(False, error_message="STP_DEVICE_SERIAL is required")
        return 1

    cfg = _params()
    requested_platform = str(cfg.get("platform") or "auto").strip().lower()
    if requested_platform not in ("auto", "mtk", "unisoc"):
        _output(False, error_message=f"unsupported platform parameter: {requested_platform!r}")
        return 1
    strict_unsupported = _as_bool(cfg.get("strict_unsupported"), False)

    _emit_progress(seq, "probe-platform")
    ok, probe_info, probe_err = _probe_device(serial)
    if not ok:
        _output(False, error_message=probe_err)
        return 1

    detected = _detect_platform(probe_info) if requested_platform == "auto" else requested_platform
    if detected == "mtk":
        _prepare_mtk(serial, probe_info, cfg, seq)
        return 0
    if detected == "unisoc":
        _prepare_unisoc(serial, probe_info, cfg, seq)
        return 0

    metrics = {
        "detected_platform": detected,
        "board_platform": probe_info.get("board_platform") or "",
        "hardware": probe_info.get("hardware") or "",
        "soc_manufacturer": probe_info.get("soc_manufacturer") or "",
    }
    if strict_unsupported:
        _output(
            False,
            error_message=f"unsupported platform for log_prepare: {metrics}",
            metrics=metrics,
        )
        return 0
    _output(
        True,
        skipped=True,
        skip_reason="unsupported_platform",
        metrics=metrics,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
