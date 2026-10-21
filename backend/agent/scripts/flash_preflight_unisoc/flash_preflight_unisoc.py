#!/usr/bin/env python3
"""展锐（Unisoc）刷机环境校验——Plan init 编排位，跑在 flash_firmware_unisoc 之前。

对齐 MTK 侧的 `flash_preflight` 编排语义（每设备 job 各执行一次、只读核验、
在**进 bootloader 之前**把 host/设备/存储三类现场条件查清），但判据集换成展锐
真实依赖——MTK 的 SP Flash Tool/qt-libs/udev 检查对展锐是错误集合，二者不共用。

逐项检查（全部 fail-closed，读不到就失败，绝不放行到刷机窗口才发现）：
    fastboot     可执行文件解析（语义与 flash_firmware_unisoc.resolve_fastboot
                 一致：STP_FASTBOOT_PATH → PATH → 常见安装路径）
    adb          设备可达且 get-state==device（离线/未授权在这里就拦，不进 reboot 链）
    identity     ro.product.model / ro.product.device 命中 allowlist（默认 Z2581/P615F03）。
                 互刷红线：展锐家族变体（A57 Pro=P615F03_1、A77=P615F04）固件不同，
                 序列号形态不可信，必须设备侧实读硬匹配
    battery      dumpsys battery level >= battery_min_level（缺省 5）。电量 0 的设备
                 刷机中途掉电=砖；Owner 已裁定排除
    firmware     {STP_NFS_ROOT}/firmware/{family}/latest.json 可读且 JSON 合法；
                 目标 version 目录的 manifest.json 可读、models 允许该设备 model
    host-lock    /tmp/stp-flash-firmware.lock 若被占（另一族/同族在刷），本步只
                 **报告 busy=True** 不等待——等待交给下一步 flash 的 host 锁（1.0.6），
                 避免两个 init 步叠在同一把锁上排队

STP agent script contract:
    env  STP_DEVICE_SERIAL   目标设备（必填）
         STP_NFS_ROOT        firmware root
         STP_STEP_PARAMS     JSON params（见下）
    out  one JSON line: {success, skipped, metrics|error_message}
    log  "PROGRESS {json}" lines on stderr

Params:
    family            str  默认 "A57"
    version           str  可选；缺省按 latest.json 指针取该 model 的目标版本
    identity_models   list 允许的 ro.product.model（默认 ["Z2581","P615F03"]）
    identity_devices  list 允许的 ro.product.device（默认 ["Z2581","P615F03"]）
    battery_min_level int  默认 5
    skip_battery      bool 默认 false（产线显插电场景可放开）
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time

_HOST_LOCK_PATH = "/tmp/stp-flash-firmware.lock"
_SEQ = 0


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default


def _params() -> dict:
    try:
        return json.loads(_env("STP_STEP_PARAMS", "{}"))
    except json.JSONDecodeError:
        return {}


def _stamp(**fields) -> None:
    global _SEQ
    _SEQ += 1
    sys.stderr.write("PROGRESS " + json.dumps({"seq": _SEQ, "step": "flash_preflight_unisoc",
                                               **fields}, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def _output(success: bool, **kwargs) -> None:
    print(json.dumps({"success": success, "skipped": False, **kwargs}, ensure_ascii=False))
    sys.stdout.flush()


def _run(argv, timeout=30):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _clean(text: str) -> str:
    return (text or "").replace("\r", "").strip().splitlines()[0].strip() if (text or "").strip() else ""


def check_fastboot() -> str:
    explicit = _env("STP_FASTBOOT_PATH", "")
    if explicit:
        if os.path.isfile(explicit) and os.access(explicit, os.X_OK):
            return explicit
        raise RuntimeError(f"STP_FASTBOOT_PATH not executable: {explicit}")
    found = shutil.which("fastboot")
    if found:
        return found
    for cand in ("/usr/bin/fastboot", "/usr/local/bin/fastboot", "/opt/android-sdk/platform-tools/fastboot"):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    raise RuntimeError("fastboot not found (PATH + common dirs) — run the host 刷机前置 button first")


def check_device(serial: str) -> dict:
    adb = _env("STP_ADB_PATH", "adb")
    p = _run([adb, "-s", serial, "get-state"])
    if p.returncode != 0 or _clean(p.stdout) != "device":
        raise RuntimeError(f"adb not ready for {serial}: rc={p.returncode} out={_clean(p.stdout) or _clean(p.stderr)}")
    def gp(prop):
        return _clean(_run([adb, "-s", serial, "shell", "getprop", prop]).stdout)
    info = {"model": gp("ro.product.model"), "device": gp("ro.product.device"),
            "incremental": gp("ro.build.version.incremental"),
            "sw_internal": gp("ro.build.sw_internal_version")}
    if not info["model"]:
        raise RuntimeError("getprop ro.product.model empty — device busy/rebooting, retry dispatch")
    return info


def check_battery(serial: str, min_level: int) -> int:
    adb = _env("STP_ADB_PATH", "adb")
    p = _run([adb, "-s", serial, "shell", "dumpsys", "battery"])
    m = re.search(r"level:\s*(\d+)", p.stdout or "")
    if not m:
        raise RuntimeError(f"battery level unreadable (min={min_level})")
    level = int(m.group(1))
    if level < min_level:
        raise RuntimeError(f"battery {level}% < min {min_level}% — charge before flashing")
    return level


def check_firmware(family: str, info: dict, want_version: str) -> dict:
    root = os.path.join(_env("STP_NFS_ROOT", ""), "firmware")
    pointer_path = os.path.join(root, family, "latest.json")
    try:
        with open(pointer_path, encoding="utf-8") as fh:
            pointer = json.load(fh)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"latest.json unreadable: {pointer_path}: {exc}")
    version = want_version
    if not version:
        versions = pointer.get("versions")
        if isinstance(versions, dict):
            for key in (info["model"], info["device"]):
                if versions.get(key):
                    version = str(versions[key]).strip()
                    break
        version = version or str(pointer.get("version") or "").strip()
    if not version:
        raise RuntimeError(f"no target version for model {info['model']!r} in {pointer_path}")
    mpath = os.path.join(root, family, version, "manifest.json")
    try:
        with open(mpath, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"manifest.json unreadable: {mpath}: {exc}")
    allowed = manifest.get("models")
    if isinstance(allowed, list) and allowed:
        ident = {info["model"], info["device"]}
        if not (ident & {str(m) for m in allowed}):
            raise RuntimeError(f"identity {info['model']}/{info['device']} not allowed by manifest models {allowed}")
    return {"family": family, "version": version, "manifest": mpath,
            "pointer": str(pointer.get("version") or pointer.get("versions") or "")[:80]}


def host_lock_state() -> bool:
    """True when another flash currently holds the host lock (informational)."""
    if not os.path.exists(_HOST_LOCK_PATH):
        return False
    import fcntl
    fd = os.open(_HOST_LOCK_PATH, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def main() -> None:
    args = _params()
    serial = _env("STP_DEVICE_SERIAL", "")
    if len(sys.argv) > 1:
        i = 1
        while i < len(sys.argv):
            a = sys.argv[i]
            if a == "--serial":
                serial = sys.argv[i + 1]; i += 2
            else:
                print(f"unknown arg {a}", file=sys.stderr); sys.exit(2)
    if not serial:
        _output(False, error_message="STP_DEVICE_SERIAL is not set")
        return

    ok_models = args.get("identity_models") or ["Z2581", "P615F03"]
    ok_devices = args.get("identity_devices") or ["Z2581", "P615F03"]
    min_level = int(args.get("battery_min_level", 5) or 5)
    family = str(args.get("family") or "A57")

    metrics = {}
    try:
        fb = check_fastboot()
        _stamp(stage="fastboot", path=fb)
        info = check_device(serial)
        _stamp(stage="device", model=info["model"], incremental=info["incremental"])
        if info["model"] not in ok_models or info["device"] not in ok_devices:
            raise RuntimeError(f"identity mismatch model={info['model']!r} device={info['device']!r} "
                               f"allowed={ok_models}/{ok_devices} — 不能互刷")
        if not bool(args.get("skip_battery")):
            lvl = check_battery(serial, min_level)
            _stamp(stage="battery", level=lvl)
            metrics["battery_level"] = lvl
        fw = check_firmware(family, info, str(args.get("version") or ""))
        _stamp(stage="firmware", version=fw["version"])
        busy = host_lock_state()
        _stamp(stage="done", host_lock_busy=busy)
        metrics.update(fw)
        metrics["host_lock_busy"] = busy
        metrics["device"] = info
        _output(True, metrics=metrics)
    except Exception as exc:  # contract: always JSON
        _output(False, error_message=str(exc), metrics={"log_tail": None})


if __name__ == "__main__":
    main()
