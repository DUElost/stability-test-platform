"""诱发式 UNISOC(uniview) 异常信号触发脚本（#73 真机验收用）。

v1.0.1（#73 实测迭代）：v1.0.0 用 ``kill -11`` 只得到 pid/rc 成功，**未产生任何
可观测产物**（无新目录、无 tombstone、``debug.uniview.last_event_key_info`` 的
event_id 未变）。v1.0.1 换成 AOSP 标准 ``am crash <pkg>`` 为主、``kill -<sig>``
为兜底，并把采样间隔降到 1s（uniview 的暂存目录 ``/data/uniview/logs/tmp``
可能在事件被消费后即清空，慢采样会漏）。

为什么需要本脚本（#73 真机调研，2026-09-14，Z2581/ums9230/Android 16）:
  - 展锐**没有** ``/data/aee_exp`` → MTK 的 aee_prepare/aee_signal_trigger 不可用；
  - 展锐实际根 ``/data/uniview``（下含 ``logs/``，再下 ``tmp/``），**不是** issue
    假设的 ``/data/unisoc_log`` / ``/data/vendor/unisoc`` / ``/sdcard/unisoc_log``。

环境变量:
    STP_DEVICE_SERIAL / STP_ADB_PATH / STP_STEP_PARAMS

STP_STEP_PARAMS:
{
  "package_name": "com.android.settings",
  "method": "am_crash",            // am_crash | kill
  "signal": 11,
  "poll_timeout_seconds": 60,
  "poll_interval_seconds": 1.0,
  "probe_roots": ["/data/uniview","/data/uniview/logs","/data/uniview/logs/tmp",
                  "/data/tombstones","/data/vendor/tombstones"]
}

输出 (stdout): 单行 JSON {"success":..., "metrics": {...}}
"""

import json
import os
import shutil
import subprocess
import sys
import time

_DEFAULT_ROOTS = [
    "/data/uniview",
    "/data/uniview/logs",
    "/data/uniview/logs/tmp",
    "/data/tombstones",
    "/data/vendor/tombstones",
]
_DEFAULT_PKG = "com.android.settings"


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
    info = _shell(f"cat {path}/unievent_info.json 2>/dev/null | head -40")
    return {"ls": ls.get("out") or ls.get("err"), "unievent_info_json": info.get("out") or None}


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
    before = {r: sorted(_list1(r)) for r in roots}

    pid = _find_pid(pkg)
    trigger = {}
    if method == "am_crash":
        trigger["am_crash"] = _shell(f"am crash {pkg} 2>&1 | tail -2", 30)
    if pid:  # 无论主路径如何，都补一刀（MTK 脚本同思路）
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
        roots_before=before,
        roots_after=after,
        new_entries=new_entries,
        event_dir_dump=dump,
        uniview_last_event_key_info=prop.get("out"),
        wait_seconds=round(time.time() - started, 2),
    )


if __name__ == "__main__":
    main()
