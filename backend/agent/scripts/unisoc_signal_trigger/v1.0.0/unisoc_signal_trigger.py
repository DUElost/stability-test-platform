"""诱发式 UNISOC(uniview) 异常信号触发脚本（#73 真机验收用）。

对齐 MTK 的 ``aee_signal_trigger``：主动 ``kill -<sig>`` 一个用户态 App 触发
native crash，让展锐 **uniview** 机制落盘事件，供 ``UnisocUniviewReconciler``
pull + 解析 + emit ``log_signal``。

为什么需要本脚本（#73 真机调研结论，2026-09-14，Z2581/ums9230/Android 16）:
  - 展锐**没有** ``/data/aee_exp``（与 MTK 完全不同）→ MTK 的 aee_prepare/
    aee_signal_trigger 在本平台不可用；
  - 展锐实际根是 ``/data/uniview``，其下 ``logs/``（再下 ``tmp/``）——**不是**
    issue 正文假设的 ``/data/unisoc_log`` / ``/data/vendor/unisoc`` /
    ``/sdcard/unisoc_log``（三者实测均不存在）；
  - 因此本脚本的探针面固定为 ``/data/uniview`` 系（可用 STP_STEP_PARAMS 追加）。

环境变量:
    STP_DEVICE_SERIAL   (required)
    STP_ADB_PATH        (default: adb / shutil.which)
    STP_STEP_PARAMS     (optional JSON)

STP_STEP_PARAMS:
{
  "package_name": "com.android.settings",   // 被诱发崩溃的 App
  "poll_timeout_seconds": 40,                // kill 后轮询新目录的最长等待
  "poll_interval_seconds": 2.0,
  "signal": 11,                              // 默认 11 = SIGSEGV
  "probe_roots": ["/data/uniview", "/data/uniview/logs", "/data/uniview/logs/tmp"]
}

输出 (stdout): 单行 JSON
    {"success": true/false, "error_message": ..., "metrics": {
        "killed_pid": "...", "signal": 11,
        "roots_before": {...}, "roots_after": {...},
        "new_entries": {...}, "event_dir_dump": {...}, "wait_seconds": 4.2}}
"""

import json
import os
import shutil
import subprocess
import sys
import time

_DEFAULT_ROOTS = ["/data/uniview", "/data/uniview/logs", "/data/uniview/logs/tmp"]
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


def main() -> None:
    if not _serial():
        _result(False, "STP_DEVICE_SERIAL not set")
        sys.exit(1)
    p = _params()
    pkg = str(p.get("package_name") or _DEFAULT_PKG)
    signal = int(p.get("signal") or 11)
    timeout_s = float(p.get("poll_timeout_seconds") or 40)
    interval = float(p.get("poll_interval_seconds") or 2.0)
    roots = list(p.get("probe_roots") or _DEFAULT_ROOTS)

    reach = _shell("echo ok", 15)
    if "ok" not in (reach.get("out") or ""):
        _result(False, f"device unreachable: {reach}")
        sys.exit(1)

    before = {r: sorted(_list1(r)) for r in roots}

    launched = _shell(
        f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1 2>&1 | tail -2", 30
    )
    time.sleep(3.0)
    pid_out = _shell(f"pidof {pkg} 2>/dev/null || ps -A -o PID,NAME 2>/dev/null | grep {pkg} | awk '{{print $1}}'", 20)
    pid = (pid_out.get("out") or "").split()[0] if (pid_out.get("out") or "").split() else ""
    if not pid:
        _result(False, f"pid not found for {pkg}", launch_output=launched.get("out"),
                roots_before=before)
        sys.exit(1)

    killed = _shell(f"kill -{signal} {pid}; echo rc=$?", 20)

    started = time.time()
    new_entries: dict = {}
    while time.time() - started < timeout_s:
        after_now = {r: sorted(_list1(r)) for r in roots}
        for r in roots:
            fresh = [x for x in after_now[r] if x not in before[r]]
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

    _result(
        True,
        None,
        killed_pid=pid,
        signal=signal,
        kill_output=killed.get("out"),
        launch_output=(launched.get("out") or "")[:200],
        roots_before=before,
        roots_after=after,
        new_entries=new_entries,
        event_dir_dump=dump,
        wait_seconds=round(time.time() - started, 2),
    )


if __name__ == "__main__":
    main()
