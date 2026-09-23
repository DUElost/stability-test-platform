"""展锐(UNISOC)异常通道深探脚本（#73）。

v1.0.1：在 v1.0.0（路径清单 + prop 扫描）基础上增加**固定的只读深探命令集**，
用于定位 uniview 的**事件真源**（#73 实测发现 `/data/uniview` 下只有
`logs/tmp`，且用户态崩溃只落 `/data/tombstones`，与现有 collector 期望的
`<root>/<event>/unievent_info.json` 布局不符）。

**刻意不做任意命令执行**：命令集在代码里固定（只读），避免给脚本库引入
"平台 → 任意 shell"的越权面（对照仓库 priv wrapper / 安全边界 ADR 的取向）。

环境变量:
    STP_DEVICE_SERIAL  (required)
    STP_ADB_PATH       (default: adb / shutil.which)
    STP_STEP_PARAMS    (optional JSON: {"extra_paths": [...]})

输出 (stdout): 单行 JSON {"success":..., "metrics": {"props":..., "paths":..., "deep": {...}}}
"""

import json
import os
import shutil
import subprocess
import sys

_DEFAULT_PATHS = [
    "/data/uniview",
    "/data/uniview/logs",
    "/data/uniview/logs/tmp",
    "/data/vendor/uniview",
    "/data/unisoc_log",
    "/data/aee_exp",
    "/data/tombstones",
    "/data/vendor/tombstones",
    "/data/log",
    "/data/log/reliability",
    "/data/anr",
]

_PROPS = [
    "ro.board.platform",
    "ro.hardware",
    "ro.product.model",
    "ro.build.display.id",
    "ro.build.version.release",
    "persist.vendor.mtk.aee.mode",
]

# 固定只读命令集（取证用；不提供任意命令入口）
_DEEP = {
    "uniview_tree": "find /data/uniview -maxdepth 4 -ls 2>/dev/null | head -60",
    "uniview_fs": (
        "find / -maxdepth 3 -iname '*uniview*' -not -path '/proc/*' -not -path '/sys/*' "
        "2>/dev/null | head -40"
    ),
    "uniview_proc": "ps -A -o PID,USER,NAME 2>/dev/null | grep -i -E 'uniview|slog|tombstone' | head -20",
    "uniview_props": "getprop | grep -i uniview",
    "uniview_init": (
        "for f in /vendor/etc/init/*uniview* /system/etc/init/*uniview*; do "
        "echo \"== $f\"; head -40 $f; done 2>/dev/null"
    ),
    "uniview_cfg_refs": "grep -ril uniview /vendor/etc /system/etc 2>/dev/null | head -20",
    "uniview_logs_all": "ls -laR /data/uniview 2>/dev/null | head -60",
    "tombstone_dirs": "ls -d /data/*tombstone* /data/vendor/*tombstone* /data/misc/*log* 2>/dev/null | head -20",
    "reliability_tree": "ls -laR /data/log/reliability 2>/dev/null | head -40",
    "vendor_log_dirs": "ls -d /data/vendor/*log* /data/vendor/log/ 2>/dev/null | head -20",
    "uniview_last_key": "getprop debug.uniview.last_event_key_info",
    "log_svc_dirs": "ls -d /data/log/* 2>/dev/null | head -20",
}


def _adb() -> str:
    return os.environ.get("STP_ADB_PATH") or shutil.which("adb") or "adb"


def _serial() -> str:
    return os.environ.get("STP_DEVICE_SERIAL", "")


def _params() -> dict:
    raw = os.environ.get("STP_STEP_PARAMS") or ""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _shell(cmd: str, timeout: int = 45) -> dict:
    try:
        r = subprocess.run(
            [_adb(), "-s", _serial(), "shell", cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return {"rc": r.returncode, "out": (r.stdout or "").strip(), "err": (r.stderr or "").strip()}
    except Exception as exc:  # noqa: BLE001
        return {"rc": -1, "out": "", "err": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    if not _serial():
        print(json.dumps({"success": False, "error_message": "STP_DEVICE_SERIAL not set"}))
        sys.exit(1)
    reach = _shell("echo ok", 15)
    if "ok" not in (reach.get("out") or ""):
        print(json.dumps({"success": False, "error_message": f"device unreachable: {reach}"},
                         ensure_ascii=False))
        sys.exit(1)

    props = {n: (_shell(f"getprop {n}").get("out") or None) for n in _PROPS}
    prop_grep = _shell("getprop | grep -iE 'unisoc|sprd|uniview|log' | head -40")

    paths = {}
    for p in _DEFAULT_PATHS + list(_params().get("extra_paths") or []):
        ls = _shell(f"ls -la {p} 2>&1 | head -25")
        cnt = _shell(f"ls -1 {p} 2>/dev/null | wc -l")
        paths[p] = {
            "ls": ls.get("out") or ls.get("err"),
            "entry_count": (cnt.get("out") or "").strip() or None,
            "exists": "No such file" not in (ls.get("out") or ""),
        }

    deep = {}
    for name, cmd in _DEEP.items():
        r = _shell(cmd, 60)
        deep[name] = (r.get("out") or r.get("err") or "")[:1500]

    print(json.dumps({
        "success": True,
        "error_message": None,
        "metrics": {
            "serial": _serial(),
            "root_uid": (_shell("id -u").get("out") or "").strip(),
            "props": props,
            "prop_grep_unisoc": prop_grep.get("out") or prop_grep.get("err"),
            "paths": paths,
            "deep": deep,
        },
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
