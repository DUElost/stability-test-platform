# -*- coding: utf-8 -*-
"""GPU disarm/probe 1.0.0 (#3809 U2, ADR-0061).

Only execution footprints and the two approved network settings are touched.
STP_STEP_PARAMS: mode=probe/disarm, wait_device_online_seconds=600.
No device log is read, collected or removed; no APK is uninstalled.

After the bounded online wait, at most 6 identity calls (5*10+30s),
10 mutation calls (10*15s), 3 snapshots (3*5*10s) and 4s settling
are possible: <=384s, within U1's registered 600s execution allowance.
The Engine owns trusted-combination promotion and authoritative package identity.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

_DEVICE_SCRIPT = "/sdcard/Auto/gpu_stress_loop.sh"
_HOST_PKG = "com.transsion.testcaserepository"
_STOP_PACKAGES = (_HOST_PKG, _HOST_PKG + ".test",
                  "com.antutu.benchmark.full", "com.antutu.benchmark.full.lite")
_LOOP_PATTERN = "[g]pu_stress_loop.sh"
_INSTRUMENT_PATTERN = "[T]estStressGpu|[A]ndroidJUnitRunner"
_FILE_PROBE = (
    f"if [ -e {_DEVICE_SCRIPT} ] || [ -L {_DEVICE_SCRIPT} ]; then echo PRESENT; "
    "elif [ -r /sdcard ] && [ -x /sdcard ] && "
    "{ [ ! -d /sdcard/Auto ] || { [ -r /sdcard/Auto ] && [ -x /sdcard/Auto ]; }; }; "
    "then echo ABSENT; else exit 2; fi"
)
_READS = {
    "loop": f"pgrep -f '{_LOOP_PATTERN}'",
    "instrument": f"pgrep -f '{_INSTRUMENT_PATTERN}'",
    "file": _FILE_PROBE,
    "airplane": "settings get global airplane_mode_on",
    "wifi": "cmd wifi status",
}
_EXEC_PROGRESS = False
_ADB_CALLS = 0


def progress(payload: dict) -> None:
    sys.stderr.write("PROGRESS " + json.dumps(payload) + "\n")
    sys.stderr.flush()


def adb(*args: str, timeout: float = 10) -> tuple[int, str, str]:
    global _ADB_CALLS
    serial = os.environ.get("STP_DEVICE_SERIAL", "")
    if not serial:
        raise RuntimeError("STP_DEVICE_SERIAL is not set")
    try:
        result = subprocess.run([os.environ.get("STP_ADB_PATH") or "adb", "-s", serial, *args],
                                capture_output=True, timeout=timeout)
        return (result.returncode, result.stdout.decode("utf-8", errors="replace"),
                result.stderr.decode("utf-8", errors="replace"))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, "", type(exc).__name__
    finally:
        if _EXEC_PROGRESS:
            _ADB_CALLS += 1
            progress({"phase": "disarm_exec", "adb_calls": _ADB_CALLS})


def _online_booted() -> tuple[bool, str | None]:
    rc, out, _ = adb("get-state", timeout=2)
    if rc != 0 or out.strip() != "device":
        return False, None
    rc, out, _ = adb("shell", "getprop sys.boot_completed", timeout=3)
    return True, out.strip() if rc == 0 else None


def _wait_online(seconds: int) -> tuple[bool, str | None]:
    deadline = time.monotonic() + seconds
    last_boot = None
    for seq in range(seconds // 10 + 2):  # finite even with a broken test clock
        start = time.monotonic()
        remaining = deadline - start
        if remaining <= 0:
            break
        rc, out, _ = adb("get-state", timeout=min(2, remaining))
        online = rc == 0 and out.strip() == "device"
        remaining = deadline - time.monotonic()
        if online and remaining > 0:
            rc, out, _ = adb("shell", "getprop sys.boot_completed", timeout=min(3, remaining))
            last_boot = out.strip() if rc == 0 else None
        progress({"phase": "wait_device_online", "seq": seq + 1,
                  "online": online, "boot_completed": last_boot})
        if online and last_boot == "1":
            return True, "1"
        delay = min(start + 10, deadline) - time.monotonic()
        if delay > 0:
            time.sleep(delay)
    return False, last_boot


def _read_text(command: str) -> str:
    rc, out, _ = adb("shell", command)
    return out.strip() if rc == 0 else ""


def _identity() -> dict:
    soc = _read_text("getprop ro.soc.manufacturer").lower()
    platform = "UNKNOWN"
    if "mediatek" in soc or soc == "mtk":
        platform = "MTK"
    elif "unisoc" in soc or "spreadtrum" in soc:
        platform = "UNISOC"
    elif "qualcomm" in soc or soc == "qti":
        platform = "QCOM"
    else:
        board = _read_text("getprop ro.board.platform").lower()
        if board.startswith("mt"):
            platform = "MTK"
        elif board.startswith(("ums", "sc", "sp")):
            platform = "UNISOC"
        elif board.startswith(("msm", "sdm", "sm", "qcom")):
            platform = "QCOM"
    model = _read_text("getprop ro.product.model")
    uid = _read_text("id -u")
    permission = "root" if uid == "0" else "non-root" if uid.isdigit() else "unknown"
    rc, out, err = adb("shell", f"pm path {_HOST_PKG}")
    paths = [line.removeprefix("package:").strip() for line in out.splitlines()
             if line.startswith("package:")]
    installed = True if rc == 0 and paths else False if rc == 1 and not out.strip() and not err.strip() else None
    digest = None
    if installed:
        # Hash the same host APK identity reported by gpu_setup, using device facts.
        rc, out, _ = adb("shell", "sha256sum " + shlex.quote(paths[0]), timeout=30)
        token = out.split()[0].lower() if out.split() else ""
        if rc == 0 and re.fullmatch(r"[0-9a-f]{64}", token):
            digest = token
    return {"platform": platform, "model": model, "permission_model": permission,
            "apk_sha256": digest, "tool_installed": installed}


def _snapshot() -> tuple[str, dict]:
    states, failures = {}, []
    for field, command in _READS.items():
        rc, out, err = adb("shell", command)
        value = None
        text = out.strip()
        if field in ("loop", "instrument"):
            if rc == 0 and text and all(p.isdigit() and int(p) > 0 for p in text.split()):
                value = True
            elif rc == 1 and not text and not err.strip():
                value = False
        elif rc == 0:
            if field == "file" and text in ("PRESENT", "ABSENT"):
                value = text == "PRESENT"
            elif field == "airplane" and text in ("0", "1"):
                value = text == "1"
            elif field == "wifi":
                matches = re.findall(r"^(?:Wi-Fi|Wifi) is (enabled|disabled)\s*$", text, re.MULTILINE)
                if len(matches) == 1:
                    value = matches[0] == "enabled"
        states[field] = {"value": value, "rc": rc}
        if value is None:
            failures.append(field)
    if failures:
        return "readback_failed", {"states": states, "failed_reads": failures}
    footprint = any(states[k]["value"] for k in ("loop", "instrument", "file", "airplane")) or not states["wifi"]["value"]
    return "footprint" if footprint else "clear", {"states": states, "failed_reads": []}


def _disarm() -> list[dict]:
    # Stop the launcher first so force-stopped packages cannot be restarted.
    commands = [(f"pkill -f '{_LOOP_PATTERN}'", True)]
    commands += [(f"am force-stop {pkg}", False) for pkg in _STOP_PACKAGES]
    commands += [(f"pkill -f '{_INSTRUMENT_PATTERN}'", True),
                 (f"rm -f {_DEVICE_SCRIPT}", False),
                 ("settings put global airplane_mode_on 0", False),
                 ("am broadcast -a android.intent.action.AIRPLANE_MODE --ez state false", False),
                 ("svc wifi enable", False)]
    failures = []
    for command, no_match_ok in commands:
        rc, _, err = adb("shell", command, timeout=15)
        if rc != 0 and not (no_match_ok and rc == 1 and not err.strip()):
            failures.append({"command": command, "rc": rc})
    return failures


def _result(mode: str, readback: str, evidence: dict, *, action_completed: bool = False) -> dict:
    success = mode == "disarm" and action_completed and readback == "clear"
    evidence.update({"mode": mode, "readback": readback, "success": success})
    return {"mode": mode, "readback": readback, "success": success,
            "disarm_result": "executed_unverified" if success else "failed",
            "readback_summary": f"mode={mode}; readback={readback}", "evidence": evidence}


def _base_evidence() -> dict:
    # Package identity is diagnostic here; Engine/control plane supply authority.
    try:
        package_sha = Path(__file__).with_name(".stp-verified").read_text().strip()
    except OSError:
        package_sha = None
    return {"kind": "readback_failed", "boot_completed": None,
            "permission_model": "unknown", "apk_sha256": None,
            "script_name": "gpu_disarm", "script_version": "1.0.0", "package_sha256": package_sha}


def _run(cfg: dict) -> dict:
    global _EXEC_PROGRESS
    evidence = _base_evidence()
    mode = str(cfg.get("mode") or "disarm").strip().lower()
    if mode not in ("probe", "disarm"):
        raise ValueError("mode must be probe or disarm")
    wait = max(0, int(cfg.get("wait_device_online_seconds", 600) or 600))
    ready, boot = _wait_online(wait) if mode == "disarm" else _online_booted()
    evidence["boot_completed"] = boot
    if not ready or boot != "1":
        return _result(mode, "readback_failed", evidence)
    _EXEC_PROGRESS = True
    evidence.update(_identity())
    errors = _disarm() if mode == "disarm" else []
    evidence["action_errors"] = errors
    for attempt in range(3 if mode == "disarm" else 1):
        if attempt:
            time.sleep(2)
        readback, snapshot = _snapshot()
        evidence.update(snapshot)
        if readback != "footprint":
            break
    evidence["kind"] = "readback_failed" if readback == "readback_failed" else "ok"
    return _result(mode, readback, evidence, action_completed=mode == "disarm" and not errors)


def main() -> None:
    cfg = {}
    try:
        cfg = json.loads(os.environ.get("STP_STEP_PARAMS", "{}"))
        result = _run(cfg)
    except Exception as exc:  # noqa: BLE001 — never turn an unreadable device into clear
        mode = str(cfg.get("mode") or "disarm") if isinstance(cfg, dict) else "disarm"
        result = _result(mode, "readback_failed", _base_evidence())
        result["error_message"] = str(exc)
    print(json.dumps(result, ensure_ascii=False))
    if not result["success"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
