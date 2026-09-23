"""诱发式 UNISOC(uniview) 异常信号触发脚本（#73 真机验收用）。

v1.0.2：**根路径对准权威布局**。v1.0.0/v1.0.1 把探针面设为 ``/data/uniview``
（框架侧目录），真机与 toolkit 双向确认后可知那是**错的**——事件真源在
``/data/ylog/uniview_exception/{Type}.{event_id}/``，元数据文件名为
``unievent_info``（无 ``.json``）。旧版本因此**永远看不到事件**（这也是 v1.0.0/
v1.0.1 实测"无新条目"的原因之一）。v1.0.0/v1.0.1 保持不可变，故以新版本修正。

对齐 MTK 的 ``aee_signal_trigger``：主动 ``kill -<sig>`` / ``am crash`` 诱发
native crash，让展锐 **uniview** 落盘事件，供 ``UnisocUniviewReconciler``
pull + 解析 + emit ``log_signal``。

真机依据（Z2581/Z2582，2026-09-14）:
  - ``/data/ylog/uniview_exception/`` 下有 ``ANR.103000005`` / ``JE.103000004`` /
    ``NE.103000003`` / ``Reboot.103000002`` 等 ``{Type}.{event_id}`` 目录；
  - 每个目录内是 ``unievent_info``（JSONL）+ ``{seq}-{ts}.tar.gz``；
  - ``Reboot`` 类的行可能全部是 ``reboot_reason="normalboot"``（正常开机，非异常）。

环境变量: STP_DEVICE_SERIAL / STP_ADB_PATH / STP_STEP_PARAMS

STP_STEP_PARAMS:
{
  "package_name": "com.android.settings",
  "method": "am_crash",            // am_crash | kill
  "signal": 11,
  "poll_timeout_seconds": 60,
  "poll_interval_seconds": 1.0,
  "probe_roots": ["/data/ylog/uniview_exception", "/data/anr",
                  "/data/tombstones", "/data/ylog"]
}

输出 (stdout): 单行 JSON {"success":..., "metrics": {...}}
"""

import json
import os
import shutil
import subprocess
import sys
import time

#: 权威事件根 + toolkit `_scan_platform_sources()` 的 SPRD 附加源
_DEFAULT_ROOTS = [
    "/data/ylog/uniview_exception",
    "/data/ylog",
    "/data/anr",
    "/data/tombstones",
]
_DEFAULT_PKG = "com.android.settings"
#: 权威元数据文件名（真机确认；无 `.json`）
_INFO_FILENAME = "unievent_info"


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


def _shell(cmd: str, timeout: int = 30) -> dict:
    try:
        r = subprocess.run(
            [_adb(), "-s", _serial(), "shell", cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return {"rc": r.returncode, "out": (r.stdout or "").strip(), "err": (r.stderr or "").strip()}
    except Exception as exc:  # noqa: BLE001
        return {"rc": -1, "out": "", "err": f"{type(exc).__name__}: {exc}"}


def _list1(path: str) -> list:
    r = _shell(f"ls -1 {path} 2>/dev/null")
    return [x.strip() for x in (r.get("out") or "").splitlines() if x.strip()]


def _dump_dir(path: str) -> dict:
    ls = _shell(f"ls -la {path} 2>&1 | head -30")
    info = _shell(f"cat {path}/{_INFO_FILENAME} 2>/dev/null | tail -10")
    return {"ls": ls.get("out") or ls.get("err"), "unievent_info": info.get("out") or None}


def _result(success: bool, error_message=None, **metrics) -> None:
    print(json.dumps({"success": success, "error_message": error_message, "metrics": metrics},
                     ensure_ascii=False))


def _find_pid(pkg: str) -> str:
    out = (_shell(f"pidof {pkg} 2>/dev/null").get("out") or "").strip()
    if out:
        return out.split()[0]
    alt = _shell(
        f"ps -A -o PID,NAME 2>/dev/null | grep {pkg} | awk '{{print $1}}' | head -1", 20
    )
    return (alt.get("out") or "").strip()


def main() -> None:
    if not _serial():
        _result(False, "STP_DEVICE_SERIAL not set")
        sys.exit(1)
    p = _params()
    pkg = str(p.get("package_name") or _DEFAULT_PKG)
    method = str(p.get("method") or "am_crash")
    signal = int(p.get("signal") or 11)
    timeout_s = float(p.get("poll_timeout_seconds") or 60)
    interval = float(p.get("poll_interval_seconds") or 1.0)
    roots = list(p.get("probe_roots") or _DEFAULT_ROOTS)

    reach = _shell("echo ok", 15)
    if "ok" not in (reach.get("out") or ""):
        _result(False, f"device unreachable: {reach}")
        sys.exit(1)

    _shell(f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1 2>&1 | tail -1", 30)
    time.sleep(2.0)
    # 每个根的"触发前快照"：后续按根做差集（每拍都会对每个根各调一次）
    before = {r: sorted(_list1(r)) for r in roots}

    pid = _find_pid(pkg)
    trigger = {}
    if method == "am_crash":
        trigger["am_crash"] = _shell(f"am crash {pkg} 2>&1 | tail -2", 30)
    if pid:  # 无论主路径如何都补一刀（MTK 脚本同思路）
        trigger["kill"] = _shell(f"kill -{signal} {pid}; echo rc=$?", 20)

    started = time.time()
    new_entries: dict = {}
    while time.time() - started < timeout_s:
        for r in roots:
            fresh = [x for x in _list1(r) if x not in before.get(r, [])]
            if fresh:
                new_entries[r] = fresh
        if new_entries:
            break
        time.sleep(interval)

    after = {r: sorted(_list1(r)) for r in roots}
    dump = {}
    for r, fresh in new_entries.items():
        for name in fresh:
            dump[f"{r}/{name}"] = _dump_dir(f"{r}/{name}")

    prop = _shell("getprop debug.uniview.last_event_key_info", 15)

    _result(
        True,
        None,
        package=pkg,
        method=method,
        pid=pid,
        trigger=trigger,
        probe_roots=roots,
        roots_before=before,
        roots_after=after,
        new_entries=new_entries,
        event_dir_dump=dump,
        uniview_last_event_key_info=prop.get("out"),
        wait_seconds=round(time.time() - started, 2),
    )


if __name__ == "__main__":
    main()
