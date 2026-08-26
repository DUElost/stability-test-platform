"""Skip Android OOBE (out-of-box experience) on ONE target device via ADB.

刷机成功后的固定后置步骤：把刚刷完、停在 OOBE 首页的单台设备自动送进
主界面。OOBE 页长时间亮屏静置会自行关机（表现为「手机无故掉出 adb」），
所以这一步必须在 flash_firmware 之后尽快执行。

与工位手工脚本 /data/apk-repo/incoming/firmware/OOBE.bat 的关键差异：
OOBE.bat 对 host 上**所有** adb 设备广播执行；本脚本只操作
STP_DEVICE_SERIAL 指定的那台——每条命令都强制 `adb -s <serial>`，
多设备同 host 时绝不误伤邻机。

Environment:
    STP_DEVICE_SERIAL     (required) 目标设备；缺省直接失败（宁可不做，
                          不可做错机器）
    STP_ADB_PATH          (default adb)

STP_STEP_PARAMS schema:
    wait_for_device_seconds : int  (optional, default 120; 刷完重启后等设备
                          回 adb **且 boot_completed=1** 的总预算，0 = 不等待)
    locales                 : str  (optional, default en-US; system_locales)
    verify_setup_complete   : bool (optional, default true; 写完后回读两个
                          标志位核验，不一致判失败)
    setupwizard_package     : str  (optional, default
                          com.google.android.setupwizard)

v1.1.0 相对 v1.0.0（真机实证配方，2026-08-26 .66 双机）：
  - **就绪判定加等 sys.boot_completed=1**：get-state==device 在 DA 混合态
    就会放行，此时 SetupWizard 尚在初始化，先发的 BACK/HOME 会被随后完成
    初始化的 SUW 抢回前台——v1.0.0「success 但仍停 OOBE」的根因。
  - **force-stop setupwizard**：标志位只影响 SUW 的下一次决策，不会让已
    在前台的它退出；清场必须显式停掉它。
  - 清场键序改为 唤醒(224) → 解锁(82) → HOME(3)（实测落主界面；
    v1.0.0 的 am start HOME 在 SUW 存活时无效）。
  - 核验升级：标志位仍是权威判据；另采集 dumpsys mCurrentFocus 作为
    ui_focus 诊断字段——含 setupwizard 不判失败（存在合法过渡态）但留痕。
"""

import json
import os
import subprocess
import sys
import time

_PROGRESS_PREFIX = "PROGRESS "
_DEFAULT_SUW_PACKAGE = "com.google.android.setupwizard"


def _progress_stamp(seq: int, **fields) -> str:
    payload = {"seq": seq, "step": "oobe_skip", **fields}
    return _PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False)


def _emit_progress(seq: "list[int]", **fields) -> None:
    seq[0] += 1
    sys.stderr.write(_progress_stamp(seq[0], **fields) + "\n")
    sys.stderr.flush()


def _step_params() -> dict:
    raw = os.environ.get("STP_STEP_PARAMS", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _output(success: bool, **kwargs) -> None:
    payload = {"success": success, "skipped": False, **kwargs}
    print(json.dumps(payload, ensure_ascii=False))


def _as_bool(value, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _param_or_env(cfg: dict, key: str, env_key: str, default):
    value = cfg.get(key)
    if value is not None and str(value) != "":
        return value
    raw = os.environ.get(env_key, "")
    if raw != "":
        return raw
    return default


def subprocess_run(argv, **kwargs):
    """间接层：单测可注入。"""
    return subprocess.run(argv, **kwargs)


def _adb_device_state(serial: str, adb_path: str) -> str:
    if not serial or not adb_path:
        return "no-device"
    try:
        proc = subprocess_run(
            [adb_path, "-s", serial, "get-state"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "unknown"
    if proc.returncode != 0:
        return "no-device"
    return (proc.stdout or "").strip() or "unknown"


def _adb_shell(serial: str, adb_path: str, shell_args: "list[str]",
               timeout: int = 15) -> "tuple[int, str]":
    try:
        proc = subprocess_run(
            [adb_path, "-s", serial, "shell"] + shell_args,
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception as exc:
        return -1, f"{type(exc).__name__}: {exc}"
    tail = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode, tail[-300:]


def _boot_completed(serial: str, adb_path: str) -> bool:
    rc, tail = _adb_shell(serial, adb_path,
                          ["getprop", "sys.boot_completed"], timeout=10)
    return rc == 0 and tail.strip().splitlines()[-1:] == ["1"]


def _wait_device_ready(
    serial: str, adb_path: str, timeout: int, seq: "list[int]",
) -> bool:
    """两段就绪：get-state==device 且 sys.boot_completed==1。

    只等 get-state 会在 DA 混合态过早放行——SetupWizard 还没初始化完，
    先发的命令会被随后苏醒的 SUW 抢回前台（v1.0.0 缺陷根因）。
    """
    deadline = time.monotonic() + timeout
    last_boot_ok = False
    while True:
        state = _adb_device_state(serial, adb_path)
        if state == "device":
            last_boot_ok = _boot_completed(serial, adb_path)
            if last_boot_ok:
                waited = round(timeout - (deadline - time.monotonic()), 1)
                _emit_progress(seq, stage="device-ready",
                               waited_seconds=waited)
                return True
        if time.monotonic() >= deadline:
            _emit_progress(seq, stage="wait-timeout",
                           last_state=state,
                           boot_completed=last_boot_ok)
            return False
        _emit_progress(seq,
                       stage="boot-wait" if state == "device"
                       else "device-wait")
        time.sleep(5)


def main() -> None:
    args = _step_params()
    serial = os.environ.get("STP_DEVICE_SERIAL", "")
    adb_path = os.environ.get("STP_ADB_PATH", "adb")
    started_at = time.time()
    seq: "list[int]" = [0]

    # 与 OOBE.bat 的本质区别：没有 serial 就拒绝执行——宁可不做，不做错机器。
    if not serial:
        _output(False, error_message=(
            "STP_DEVICE_SERIAL not set; refusing blanket execution "
            "(original OOBE.bat hits every adb device on the host)"),
            metrics={"duration_seconds": 0})
        return

    wait_seconds = _param_or_env(
        args, "wait_for_device_seconds", "", 120)
    try:
        wait_seconds = max(0, int(wait_seconds))
    except (TypeError, ValueError):
        wait_seconds = 120
    locales = str(_param_or_env(args, "locales", "", "en-US")).strip()
    verify = _as_bool(args.get("verify_setup_complete"), default=True)
    suw_package = str(
        _param_or_env(args, "setupwizard_package", "", _DEFAULT_SUW_PACKAGE)
    ).strip() or _DEFAULT_SUW_PACKAGE

    # ── 等设备回到 adb 且 boot_completed（刷完重启需要时间）───────────
    device_ready = _wait_device_ready(serial, adb_path, wait_seconds, seq)
    metrics: dict = {
        "serial": serial,
        "device_ready": device_ready,
        "commands": [],
    }
    if not device_ready:
        _output(False, error_message=(
            f"device {serial} not adb-ready within {wait_seconds}s; "
            "OOBE skip not attempted"),
                metrics=metrics)
        return

    # ── root（best-effort；部分固件无此命令，失败不阻断）──────────────
    root_rc, root_tail = _adb_shell(serial, adb_path, ["root"])
    metrics["root"] = {"rc": root_rc, "tail": root_tail}
    _emit_progress(seq, stage="root", ok=root_rc == 0)
    if root_rc == 0:
        time.sleep(2)  # adbd 重启窗口，紧随其后的命令可能瞬断

    # ── 命令序列：provisioning 标志与 OOBE.bat 一致 ────────────────────
    shell_commands: "list[tuple[str, list[str]]]" = [
        ("setup-complete", ["settings", "put", "secure",
                            "user_setup_complete", "1"]),
        ("device-provisioned", ["settings", "put", "global",
                                "device_provisioned", "1"]),
        ("system-locales", ["settings", "put", "system",
                            "system_locales", locales]),
    ]
    for name, shell_args in shell_commands:
        rc, tail = _adb_shell(serial, adb_path, shell_args)
        metrics["commands"].append({"name": name, "rc": rc, "tail": tail})
        _emit_progress(seq, stage=name.replace("_", "-"), ok=rc == 0)

    # ── 清场：SUW 不会因标志位变化自行退出，必须显式停掉 ───────────────
    suw_rc, suw_tail = _adb_shell(
        serial, adb_path, ["am", "force-stop", suw_package])
    metrics["commands"].append(
        {"name": "force-stop-setupwizard", "rc": suw_rc, "tail": suw_tail})
    _emit_progress(seq, stage="force-stop-setupwizard", ok=suw_rc == 0)

    # ── 键序：唤醒 → 解锁 → HOME（实测落主界面）──────────────────────
    keyevents: "list[tuple[str, str]]" = [
        ("keyevent-wakeup", "224"),
        ("keyevent-unlock", "82"),
        ("keyevent-home", "3"),
    ]
    for name, keycode in keyevents:
        rc, tail = _adb_shell(serial, adb_path, ["input", "keyevent", keycode])
        metrics["commands"].append({"name": name, "rc": rc, "tail": tail})
        _emit_progress(seq, stage=name.replace("_", "-"), ok=rc == 0)

    # ── 回读核验（权威判据：两个 provisioning 标志位）─────────────────
    verify_report: dict = {"enabled": verify}
    oobe_done = True
    if verify:
        checks = [
            ("user_setup_complete",
             ["settings", "get", "secure", "user_setup_complete"]),
            ("device_provisioned",
             ["settings", "get", "global", "device_provisioned"]),
        ]
        for name, read_args in checks:
            rc, tail = _adb_shell(serial, adb_path, read_args)
            value = tail.strip().splitlines()[-1] if tail.strip() else ""
            ok = rc == 0 and value.strip() == "1"
            verify_report[name] = {"value": value.strip(), "ok": ok}
            oobe_done = oobe_done and ok
        verify_report["ok"] = oobe_done
        _emit_progress(seq, stage="verify", ok=oobe_done)

    # ── UI 焦点诊断（best-effort，不参与成败判定）─────────────────────
    focus_rc, focus_tail = _adb_shell(
        serial, adb_path, ["dumpsys", "window"], timeout=20)
    focus_line = next(
        (ln.strip() for ln in focus_tail.splitlines()
         if "mCurrentFocus" in ln), "")
    ui_on_su = suw_package.lower() in focus_line.lower()
    metrics["ui_focus"] = {
        "line": focus_line.split("mCurrentFocus=")[-1][:160],
        "on_setupwizard": ui_on_su,
    }
    _emit_progress(seq, stage="ui-focus", on_setupwizard=ui_on_su)

    metrics["verify"] = verify_report
    metrics["duration_seconds"] = round(time.time() - started_at, 2)
    _emit_progress(seq, stage="done", ok=oobe_done)

    if not oobe_done:
        failed = [k for k, v in verify_report.items()
                  if isinstance(v, dict) and v.get("ok") is False]
        values = [verify_report[k].get("value") for k in failed]
        hint = (
            " (UI still on setupwizard; flags may have been reset by a "
            "later re-flash — re-run this step)" if ui_on_su else "")
        _output(False, error_message=(
            f"OOBE skip verification failed: {failed} (values: {values})"
            + hint),
                metrics=metrics)
        return

    _output(True, metrics=metrics)


if __name__ == "__main__":
    main()
