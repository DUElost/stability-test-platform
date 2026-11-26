#!/usr/bin/env python3
"""Configure Unisoc (SPRD) full sysdump (`vendor.debug.sysdump.enabled`) before stability tests.

On Unisoc platforms (`/vendor/etc/init/systemDebuggerd.rc`), setting
`vendor.debug.sysdump.enabled=true` starts `systemDebuggerd`, writes `"on"` to
`/proc/sprd_sysdump` (`sysdump_status = 1`), and persists
`persist.vendor.sysdump=on` so full RAM dump is preserved on first Kernel Panic
or Watchdog hit during stability testing.

Non-Unisoc platforms (MTK, Qualcomm, etc.) are skipped immediately with
`skipped=true` (`skip_reason="non_unisoc_platform"`) without invoking `adb root`
or mutating device state.

Environment:
    STP_DEVICE_SERIAL   (required) Target ADB serial.
    STP_ADB_PATH        (optional) ADB executable path (default: "adb").
    STP_STEP_PARAMS     (optional JSON):
        {
          "enabled": true,                // true=on, false=off (default: true)
          "verify_timeout_seconds": 10,   // wait for init trigger readback (default: 10)
          "root_timeout_seconds": 15,     // wait for adb root if uid != 0 (default: 15)
          "strict_unsupported": false     // fail if Unisoc device lacks /proc/sprd_sysdump
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
_SYSDUMP_PROC_PATH = "/proc/sprd_sysdump"
_SYSDUMP_TRIGGER_PROP = "vendor.debug.sysdump.enabled"
_SYSDUMP_PERSIST_PROP = "persist.vendor.sysdump"

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
_SYSDUMP_STATUS_RE = re.compile(r"sysdump_status\s*=\s*([01])")


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


def _as_float(value: Any, default: float, *, minimum: float = 0.0) -> float:
    if value is None or str(value).strip() == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def _emit_progress(seq: list[int], stage: str, **fields: Any) -> None:
    seq[0] += 1
    payload: dict[str, Any] = {
        "seq": seq[0],
        "step": "unisoc_sysdump_config",
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


def _run_adb(serial: str, args: list[str], timeout: float = 15.0) -> tuple[int, str, str]:
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


def _probe_platform(serial: str) -> tuple[bool, dict[str, str], str]:
    shell_cmd = (
        "echo board_platform=$(getprop ro.board.platform);"
        "echo soc_manufacturer=$(getprop ro.soc.manufacturer);"
        "echo hardware=$(getprop ro.hardware);"
        "echo boot_hardware=$(getprop ro.boot.hardware);"
        "echo uid=$(id -u);"
        f"if [ -e {_SYSDUMP_PROC_PATH} ]; then echo proc_exists=1; else echo proc_exists=0; fi"
    )
    rc, out, err = _run_adb(serial, ["shell", shell_cmd], timeout=15.0)
    if rc != 0:
        return False, {}, f"adb shell probe failed (rc={rc}): {err or out}"
    info: dict[str, str] = {}
    for line in out.splitlines():
        s = line.strip()
        if "=" in s:
            k, v = s.split("=", 1)
            info[k.strip()] = v.strip()
    if "uid" not in info and "proc_exists" not in info:
        return False, info, f"unexpected probe output: {out[:200]}"
    return True, info, ""


def _is_unisoc(info: dict[str, str]) -> bool:
    if info.get("proc_exists") == "1":
        return True
    soc = info.get("soc_manufacturer", "").strip().lower()
    if soc in _UNISOC_SOC_MFRS:
        return True
    plat = info.get("board_platform", "").strip().lower()
    if any(plat.startswith(p) for p in _UNISOC_PLATFORM_PREFIXES):
        return True
    for key in ("hardware", "boot_hardware"):
        val = info.get(key, "").strip().lower()
        if "sprd" in val or "unisoc" in val:
            return True
    return False


def _ensure_root(serial: str, timeout_seconds: float) -> tuple[bool, str]:
    rc, out, err = _run_adb(serial, ["shell", "id -u"], timeout=10.0)
    if rc == 0 and out.strip() == "0":
        return True, "already_root"

    deadline = time.monotonic() + max(1.0, timeout_seconds)
    r_rc, r_out, r_err = _run_adb(serial, ["root"], timeout=min(15.0, max(3.0, timeout_seconds)))
    if r_rc != 0:
        return False, f"adb root rc={r_rc} stdout={r_out[:120]!r} stderr={r_err[:120]!r}"

    while time.monotonic() < deadline:
        st_rc, st_out, _ = _run_adb(serial, ["get-state"], timeout=5.0)
        if st_rc == 0 and st_out.strip() == "device":
            id_rc, id_out, _ = _run_adb(serial, ["shell", "id -u"], timeout=8.0)
            if id_rc == 0 and id_out.strip() == "0":
                return True, r_out or "restarted_as_root"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.5, remaining))

    id_rc, id_out, id_err = _run_adb(serial, ["shell", "id -u"], timeout=5.0)
    return (
        False,
        f"root not active after {timeout_seconds:.1f}s "
        f"(adb_root={r_out[:100]!r} id_rc={id_rc} id_out={id_out[:40]!r} id_err={id_err[:80]!r})",
    )


def _read_sysdump_state(serial: str) -> tuple[bool, dict[str, str], str]:
    shell_cmd = (
        f"echo persist=$(getprop {_SYSDUMP_PERSIST_PROP});"
        f"echo trigger=$(getprop {_SYSDUMP_TRIGGER_PROP});"
        f"if [ -e {_SYSDUMP_PROC_PATH} ]; then echo proc_exists=1; "
        f"echo proc_raw=$(cat {_SYSDUMP_PROC_PATH} 2>&1 | tr '\\n' ' '); "
        "else echo proc_exists=0; echo proc_raw=; fi"
    )
    rc, out, err = _run_adb(serial, ["shell", shell_cmd], timeout=12.0)
    if rc != 0:
        return False, {}, f"read sysdump state failed (rc={rc}): {err or out}"
    state: dict[str, str] = {}
    for line in out.splitlines():
        s = line.strip()
        if "=" in s:
            k, v = s.split("=", 1)
            state[k.strip()] = v.strip()
    m = _SYSDUMP_STATUS_RE.search(state.get("proc_raw", ""))
    state["sysdump_status"] = m.group(1) if m else ""
    return True, state, ""


def _state_matches(state: dict[str, str], enabled: bool) -> bool:
    expected_persist = "on" if enabled else "off"
    expected_status = "1" if enabled else "0"
    return (
        state.get("persist", "").strip().lower() == expected_persist
        and state.get("sysdump_status", "").strip() == expected_status
    )


def main() -> None:
    t0 = time.monotonic()
    seq = [0]
    serial = _serial()
    if not serial:
        _output(False, error_message="STP_DEVICE_SERIAL is not set")
        sys.exit(1)

    cfg = _params()
    enabled = _as_bool(cfg.get("enabled"), True)
    verify_timeout = _as_float(cfg.get("verify_timeout_seconds"), 10.0, minimum=1.0)
    root_timeout = _as_float(cfg.get("root_timeout_seconds"), 15.0, minimum=2.0)
    strict_unsupported = _as_bool(cfg.get("strict_unsupported"), False)
    target_prop_val = "true" if enabled else "false"
    expected_persist = "on" if enabled else "off"
    expected_status = "1" if enabled else "0"

    _emit_progress(seq, "probe", serial=serial, enabled=enabled)
    ok, info, probe_err = _probe_platform(serial)
    if not ok:
        _output(False, error_message=probe_err, metrics={"serial": serial})
        sys.exit(1)

    platform_meta = {
        "board_platform": info.get("board_platform", ""),
        "soc_manufacturer": info.get("soc_manufacturer", ""),
        "hardware": info.get("hardware", ""),
        "boot_hardware": info.get("boot_hardware", ""),
    }

    if not _is_unisoc(info):
        _emit_progress(seq, "skip", reason="non_unisoc_platform")
        _output(
            True,
            skipped=True,
            skip_reason="non_unisoc_platform",
            metrics={
                "serial": serial,
                "enabled_requested": enabled,
                "platform": platform_meta,
                "duration_seconds": round(time.monotonic() - t0, 2),
            },
        )
        return

    _emit_progress(seq, "ensure_root")
    root_ok, root_detail = _ensure_root(serial, root_timeout)
    if not root_ok:
        _output(
            False,
            error_message=f"unisoc sysdump requires adb root: {root_detail}",
            metrics={
                "serial": serial,
                "enabled_requested": enabled,
                "platform": platform_meta,
            },
        )
        sys.exit(1)

    read_ok, before, read_err = _read_sysdump_state(serial)
    if not read_ok:
        _output(
            False,
            error_message=read_err,
            metrics={"serial": serial, "platform": platform_meta},
        )
        sys.exit(1)

    if before.get("proc_exists") != "1":
        msg = f"{_SYSDUMP_PROC_PATH} not found on Unisoc device"
        if strict_unsupported:
            _output(
                False,
                error_message=msg,
                metrics={"serial": serial, "platform": platform_meta, "before": before},
            )
            sys.exit(1)
        _emit_progress(seq, "skip", reason="sysdump_node_absent")
        _output(
            True,
            skipped=True,
            skip_reason="sysdump_node_absent",
            metrics={
                "serial": serial,
                "enabled_requested": enabled,
                "platform": platform_meta,
                "before": before,
                "duration_seconds": round(time.monotonic() - t0, 2),
            },
        )
        return

    if _state_matches(before, enabled):
        _emit_progress(seq, "done", already_configured=True)
        _output(
            True,
            skipped=True,
            skip_reason="already_configured",
            metrics={
                "serial": serial,
                "enabled_requested": enabled,
                "platform": platform_meta,
                "before": before,
                "after": before,
                "duration_seconds": round(time.monotonic() - t0, 2),
            },
        )
        return

    _emit_progress(seq, "apply", prop=_SYSDUMP_TRIGGER_PROP, value=target_prop_val)
    set_rc, set_out, set_err = _run_adb(
        serial,
        ["shell", f"setprop {_SYSDUMP_TRIGGER_PROP} {target_prop_val}"],
        timeout=12.0,
    )
    if set_rc != 0:
        _output(
            False,
            error_message=(
                f"setprop {_SYSDUMP_TRIGGER_PROP} {target_prop_val} failed "
                f"(rc={set_rc}): {set_err or set_out}"
            ),
            metrics={"serial": serial, "platform": platform_meta, "before": before},
        )
        sys.exit(1)

    _emit_progress(seq, "verify", expected_persist=expected_persist, expected_status=expected_status)
    deadline = time.monotonic() + verify_timeout
    after: dict[str, str] = {}
    last_read_err = ""
    while True:
        r_ok, state, r_err = _read_sysdump_state(serial)
        if r_ok:
            after = state
            if _state_matches(after, enabled):
                _emit_progress(seq, "done", already_configured=False)
                _output(
                    True,
                    skipped=False,
                    metrics={
                        "serial": serial,
                        "enabled_requested": enabled,
                        "platform": platform_meta,
                        "root": root_detail,
                        "before": before,
                        "after": after,
                        "duration_seconds": round(time.monotonic() - t0, 2),
                    },
                )
                return
        else:
            last_read_err = r_err

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.5, remaining))

    _output(
        False,
        error_message=(
            f"sysdump verification mismatch after {verify_timeout:.1f}s: "
            f"expected persist={expected_persist!r} & sysdump_status={expected_status!r}, "
            f"got persist={after.get('persist')!r} sysdump_status={after.get('sysdump_status')!r} "
            f"proc_raw={after.get('proc_raw')!r}"
            + (f" last_err={last_read_err!r}" if last_read_err else "")
        ),
        metrics={
            "serial": serial,
            "enabled_requested": enabled,
            "platform": platform_meta,
            "before": before,
            "after": after,
            "duration_seconds": round(time.monotonic() - t0, 2),
        },
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
