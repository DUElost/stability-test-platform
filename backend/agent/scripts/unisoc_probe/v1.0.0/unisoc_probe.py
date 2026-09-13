"""展锐(UNISOC)异常落盘调研探针（#73，临时脚本）。

目的：在**真机**上取证，回答 issue #73 goal 1「调研展锐真实机型的异常落盘目录」。
只读探测，不修改设备状态。

与环境/平台的关系：
  - 走平台脚本派发（Agent 在主机上执行），因为设备 USB 挂在各 host 上，
    控制面本机 adb 看不到设备（实测空列表）。
  - 平台门禁/MTK 专有步骤（aee_prepare 设 persist.vendor.mtk.aee.mode）对展锐
    无效，故需要本探针这类平台无关的取证脚本。

环境变量:
    STP_DEVICE_SERIAL  (required)
    STP_ADB_PATH       (default: adb / shutil.which)
    STP_STEP_PARAMS    (optional JSON: {"extra_paths": ["/xx"]})

输出 (stdout): 单行 JSON
    {"success": true/false, "error_message": ..., "metrics": {...}}
"""

import json
import os
import shutil
import subprocess
import sys

# issue 正文假设的路径 + 代码里已实现的路径（unisoc.py / unisoc_reconciler.py）
_DEFAULT_PATHS = [
    "/data/uniview",              # 代码现存根（ADR-0032 B5/D8）
    "/data/vendor/uniview",       # 代码现存根
    "/data/unisoc_log",           # issue #73 假设
    "/data/vendor/unisoc",        # issue #73 假设
    "/sdcard/unisoc_log",         # issue #73 假设
    "/data/aee_exp",              # MTK（对照：展锐预期不存在）
    "/data/vendor/aee_exp",       # MTK（对照）
]

_PROPS = [
    "ro.board.platform",
    "ro.hardware",
    "ro.product.model",
    "ro.build.display.id",
    "ro.build.version.release",
    "persist.vendor.mtk.aee.mode",   # MTK 专有：展锐预期为空
    "persist.sys.unisoc.log",        # 展锐候选开关
    "persist.vendor.unisoc.log",
]


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


def _shell(cmd: str, timeout: int = 25) -> dict:
    try:
        r = subprocess.run(
            [_adb(), "-s", _serial(), "shell", cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return {"rc": r.returncode, "out": (r.stdout or "").strip(), "err": (r.stderr or "").strip()}
    except Exception as exc:  # noqa: BLE001 - 探针要如实回报失败原因
        return {"rc": -1, "out": "", "err": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    serial = _serial()
    if not serial:
        print(json.dumps({"success": False, "error_message": "STP_DEVICE_SERIAL not set"}))
        sys.exit(1)

    reach = _shell("echo ok", timeout=15)
    if "ok" not in (reach.get("out") or ""):
        print(json.dumps({
            "success": False,
            "error_message": f"device unreachable: {reach}",
        }, ensure_ascii=False))
        sys.exit(1)

    props = {}
    for name in _PROPS:
        r = _shell(f"getprop {name}")
        props[name] = r.get("out") or None

    # 展锐相关 prop 全扫描（找"打开异常落盘"的候选开关）
    prop_grep = _shell(
        "getprop | grep -iE 'unisoc|sprd|uniview|log' | head -40", timeout=25
    )

    paths = {}
    for p in _DEFAULT_PATHS + list(_params().get("extra_paths") or []):
        ls = _shell(f"ls -la {p} 2>&1 | head -25", timeout=25)
        cnt = _shell(f"ls -1 {p} 2>/dev/null | wc -l", timeout=25)
        paths[p] = {
            "ls": ls.get("out") or ls.get("err"),
            "entry_count": (cnt.get("out") or "").strip() or None,
            "exists": (ls.get("out") or "").startswith("total") or "No such file" not in (ls.get("out") or ""),
        }

    root = _shell("id -u")
    disk = _shell("df -h /data 2>&1 | tail -1")

    print(json.dumps({
        "success": True,
        "error_message": None,
        "metrics": {
            "serial": serial,
            "root_uid": (root.get("out") or "").strip(),
            "props": props,
            "prop_grep_unisoc": prop_grep.get("out") or prop_grep.get("err"),
            "paths": paths,
            "df_data": disk.get("out"),
        },
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
