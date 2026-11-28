"""Skip Android OOBE (out-of-box experience) on ONE target device via ADB.

v1.1.4：通用向导包探测 + 清场硬校验（P532AA 实证）。
  - 种子名单扩到 Tinno SetupWizard / Pixel SUW；并按机探测已安装
    setup|oobe|wizard 包与占用 HOME 的非桌面包，避免机型专属硬编码。
  - 写入 setup_wizard_has_run=1；禁用优先 pm disable，失败再
    pm disable-user（系统 priv-app 上 disable-user 常不够）。
  - dumpsys window 改为设备侧 grep mCurrentFocus，修复 _adb_shell 只留
    末 300 字导致焦点诊断恒空、误报已离开 OOBE。
  - 标志位之外新增 wizard_cleared 校验：目标向导仍 enabled 或 HOME 仍
    解析到向导包 → 失败（覆盖「flags=1 但仍停 WelcomeActivity」）。

v1.1.3（#4083）：adb root 直调、setup_skipped、Tinno FactoryOOBE、
多屏 focus 过滤与二次清场。

v1.1.2（#1591）：验证失败重试（verify_retries 默认 2）——刷机后系统忙碌，
settings put 可能未即时落盘；重设 flags + 等待 + 重验证以收敛。

v1.1.1（#816）：adb root 后固定 sleep(2) 改轮询 get-state==device（上限 15s，
超时继续由命令层 rc/verify 兜底）——adbd 重启需 2–5s+，固定窗口落在
offline 时整步失败率偏高。

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
    verify_wizard_cleared   : bool (optional, default true; 向导包已禁用且
                          HOME 不再解析到向导包)
    discover_setupwizard    : bool (optional, default true; 按机探测向导包)
    setupwizard_package     : str  (optional, default
                          com.google.android.setupwizard)
    extra_setupwizard_packages : list|str (optional; 追加种子向导包)
    disable_setupwizard     : bool (optional, default true)

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
import re
import subprocess
import sys
import time

_PROGRESS_PREFIX = "PROGRESS "
_DEFAULT_SUW_PACKAGE = "com.google.android.setupwizard"
_DEFAULT_EXTRA_SUW_PACKAGES = (
    "com.tinno.factoryoobe",
    "com.tinno.setupwizard",
    "com.google.android.pixel.setupwizard",
)
_EXCLUDE_PACKAGES = frozenset({
    "com.android.managedprovisioning",
})
_LAUNCHER_PACKAGES = frozenset({
    "com.android.launcher3",
    "com.android.launcher",
    "com.google.android.apps.nexuslauncher",
    "com.microsoft.launcher",
})
_WIZARD_NAME_RE = re.compile(
    r"(setupwizard|setup_wizard|factoryoobe|(?:^|\.)oobe(?:\.|$))",
    re.IGNORECASE,
)


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


def _adb_cmd(serial: str, adb_path: str, adb_args: "list[str]",
             timeout: int = 15, *, keep_chars: int = 300) -> "tuple[int, str]":
    try:
        proc = subprocess_run(
            [adb_path, "-s", serial] + adb_args,
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception as exc:
        return -1, f"{type(exc).__name__}: {exc}"
    tail = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if keep_chars and keep_chars > 0 and len(tail) > keep_chars:
        return proc.returncode, tail[-keep_chars:]
    return proc.returncode, tail


def _adb_shell(serial: str, adb_path: str, shell_args: "list[str]",
               timeout: int = 15, *, keep_chars: int = 300) -> "tuple[int, str]":
    return _adb_cmd(
        serial, adb_path, ["shell"] + shell_args,
        timeout=timeout, keep_chars=keep_chars,
    )


def _wait_adbd_ready(serial: str, adb_path: str, deadline_seconds: float = 15.0) -> bool:
    """adb root 后轮询 adbd 恢复（#816/#4083）：adb get-state == device 才算就绪。"""
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        rc, tail = _adb_cmd(serial, adb_path, ["get-state"], timeout=5)
        if rc == 0 and "device" in tail:
            return True
        time.sleep(1.0)
    return False


def _extract_current_focus(focus_tail: str) -> str:
    """多屏 dumpsys window 会先输出 mCurrentFocus=null，取最后一个非空焦点窗口。"""
    non_null = [
        ln.strip()
        for ln in focus_tail.splitlines()
        if "mCurrentFocus=" in ln and not ln.strip().endswith("=null")
    ]
    if non_null:
        return non_null[-1]
    return next(
        (ln.strip() for ln in focus_tail.splitlines() if "mCurrentFocus" in ln),
        "",
    )


def _looks_like_wizard(package: str) -> bool:
    pkg = (package or "").strip()
    if not pkg or pkg in _EXCLUDE_PACKAGES or pkg in _LAUNCHER_PACKAGES:
        return False
    return bool(_WIZARD_NAME_RE.search(pkg))


def _parse_suw_packages(primary: str, extra_raw) -> "list[str]":
    """合并主向导包与额外向导包（去重保序）。"""
    pkgs: list[str] = [primary]
    if isinstance(extra_raw, (list, tuple)):
        extras = [str(x).strip() for x in extra_raw if str(x).strip()]
    elif isinstance(extra_raw, str) and extra_raw.strip():
        extras = [s.strip() for s in extra_raw.split(",") if s.strip()]
    else:
        extras = list(_DEFAULT_EXTRA_SUW_PACKAGES)
    for pkg in extras:
        if pkg not in pkgs:
            pkgs.append(pkg)
    return pkgs


def _package_installed(serial: str, adb_path: str, package: str) -> bool:
    rc, tail = _adb_shell(serial, adb_path, ["pm", "path", package], timeout=10)
    return rc == 0 and "package:" in tail


def _adb_shell_pipeline(
    serial: str, adb_path: str, pipeline: str, *, timeout: int = 20,
    keep_chars: int = 2000,
) -> "tuple[int, str]":
    """整段管道作为单一 shell 参数（避免 `shell sh -c` 在部分镜像上空输出）。"""
    return _adb_cmd(
        serial, adb_path, ["shell", pipeline],
        timeout=timeout, keep_chars=keep_chars,
    )


def _list_installed_wizardish(serial: str, adb_path: str) -> "list[str]":
    # keep_chars=0：完整 pm list，宿主侧过滤（比 sh -c 管道更稳）。
    rc, tail = _adb_shell(
        serial, adb_path, ["pm", "list", "packages"],
        timeout=20, keep_chars=0,
    )
    if rc != 0:
        return []
    found: list[str] = []
    for line in tail.splitlines():
        line = line.strip()
        if not line.startswith("package:"):
            continue
        pkg = line.split(":", 1)[1].strip()
        if _looks_like_wizard(pkg) and pkg not in found:
            found.append(pkg)
    return found


def _home_activity_packages(serial: str, adb_path: str) -> "list[str]":
    """解析 HOME intent 候选包（占用默认桌面的向导也在此列）。"""
    rc, tail = _adb_shell(
        serial, adb_path,
        ["cmd", "package", "query-activities",
         "-a", "android.intent.action.MAIN",
         "-c", "android.intent.category.HOME"],
        timeout=20, keep_chars=0,
    )
    if rc != 0 or not tail.strip():
        # 旧镜像无 query-activities 时退回 resolve-activity
        rc2, tail2 = _adb_shell(
            serial, adb_path,
            ["cmd", "package", "resolve-activity", "--brief",
             "-a", "android.intent.action.MAIN",
             "-c", "android.intent.category.HOME"],
            timeout=15, keep_chars=1000,
        )
        if rc2 != 0:
            return []
        tail = tail2
    pkgs: list[str] = []
    for m in re.finditer(r"packageName=([A-Za-z0-9_.]+)", tail):
        pkg = m.group(1)
        if pkg not in pkgs:
            pkgs.append(pkg)
    for raw in re.findall(
            r"(?:^|\s)([A-Za-z0-9_.]+)/[A-Za-z0-9_.]+",
            tail, flags=re.MULTILINE):
        if raw not in pkgs:
            pkgs.append(raw)
    return pkgs


def _resolve_home_package(serial: str, adb_path: str) -> str:
    rc, tail = _adb_shell(
        serial, adb_path,
        ["cmd", "package", "resolve-activity", "--brief",
         "-a", "android.intent.action.MAIN",
         "-c", "android.intent.category.HOME"],
        timeout=15, keep_chars=1000,
    )
    if rc != 0:
        return ""
    for line in reversed([ln.strip() for ln in tail.splitlines() if ln.strip()]):
        if "/" in line and not line.startswith("priority=") and " " not in line:
            return line.split("/", 1)[0]
    return ""


def _read_current_focus(serial: str, adb_path: str) -> str:
    """设备侧 grep，避免 dumpsys 全文被默认末 300 字截断。"""
    rc, tail = _adb_shell_pipeline(
        serial, adb_path,
        "dumpsys window 2>/dev/null | grep mCurrentFocus || true",
        timeout=20, keep_chars=2000,
    )
    if rc != 0:
        return ""
    return _extract_current_focus(tail)


def _discover_wizard_packages(
    serial: str,
    adb_path: str,
    seed_packages: "list[str]",
    discover: bool,
) -> "list[str]":
    """种子名单 ∪ 机上向导名包 ∪ 占用 HOME 的非桌面包；只保留已安装。"""
    ordered: list[str] = []

    def _add(pkg: str) -> None:
        pkg = (pkg or "").strip()
        if not pkg or pkg in _EXCLUDE_PACKAGES or pkg in ordered:
            return
        if pkg in _LAUNCHER_PACKAGES:
            return
        ordered.append(pkg)

    for pkg in seed_packages:
        _add(pkg)

    if discover:
        for pkg in _list_installed_wizardish(serial, adb_path):
            _add(pkg)
        for pkg in _home_activity_packages(serial, adb_path):
            if _looks_like_wizard(pkg) or pkg in seed_packages:
                _add(pkg)
        focus_line = _read_current_focus(serial, adb_path)
        for pkg in seed_packages:
            if pkg and pkg.lower() in focus_line.lower():
                _add(pkg)
        for m in re.finditer(r"\b([a-zA-Z0-9_.]+)/[a-zA-Z0-9_.]+\b", focus_line):
            cand = m.group(1)
            if _looks_like_wizard(cand):
                _add(cand)

    installed: list[str] = []
    for pkg in ordered:
        if _package_installed(serial, adb_path, pkg):
            installed.append(pkg)
    return installed


def _package_disabled(serial: str, adb_path: str, package: str) -> bool:
    # -d 列表通常很短；整表回读避免 sh -c 管道在部分镜像上空输出。
    rc, tail = _adb_shell(
        serial, adb_path, ["pm", "list", "packages", "-d"],
        timeout=15, keep_chars=0,
    )
    if rc == 0 and f"package:{package}" in (tail or ""):
        return True
    # list -d 偶发漏报 priv-app：只取 User 0: 行（勿用宽 grep enabled=，
    # 会被 fallback_enabled=true 淹没并把真正的 User 0 行截掉）。
    rc2, tail2 = _adb_shell_pipeline(
        serial, adb_path,
        f"dumpsys package {package} 2>/dev/null | grep 'User 0:' || true",
        timeout=20, keep_chars=2000,
    )
    if rc2 != 0:
        return False
    # enabled=2 DISABLED, 3 DISABLED_USER, 4 DISABLED_UNTIL_USED
    matches = re.findall(r"User 0:.*?enabled=(\d+)", tail2)
    if not matches:
        return False
    try:
        return int(matches[0]) in (2, 3, 4)
    except ValueError:
        return False


def _disable_package(
    serial: str, adb_path: str, package: str,
) -> "tuple[int, str, str]":
    """优先 pm disable（跨重启更稳），失败再 disable-user。"""
    rc, tail = _adb_shell(serial, adb_path, ["pm", "disable", package])
    if rc == 0:
        return rc, tail, "pm-disable"
    rc2, tail2 = _adb_shell(
        serial, adb_path, ["pm", "disable-user", "--user", "0", package])
    return rc2, tail2, "pm-disable-user"


def _clear_suw_and_home(
    serial: str,
    adb_path: str,
    suw_packages: "list[str]",
    disable_suw: bool,
    metrics: dict,
    seq: "list[int]",
) -> None:
    """停止/禁用向导包并发送唤醒→解锁→HOME 键序。"""
    for idx, pkg in enumerate(suw_packages):
        rc, tail = _adb_shell(serial, adb_path, ["am", "force-stop", pkg])
        cmd_name = "force-stop-setupwizard" if idx == 0 else f"force-stop-extra:{pkg}"
        metrics["commands"].append({"name": cmd_name, "rc": rc, "tail": tail})
        if idx == 0:
            _emit_progress(seq, stage="force-stop-setupwizard", ok=rc == 0)
        if disable_suw:
            d_rc, d_tail, method = _disable_package(serial, adb_path, pkg)
            d_name = "disable-setupwizard" if idx == 0 else f"disable-extra:{pkg}"
            metrics["commands"].append({
                "name": d_name, "rc": d_rc, "tail": d_tail, "method": method,
            })

    for name, keycode in (("keyevent-wakeup", "224"),
                          ("keyevent-unlock", "82"),
                          ("keyevent-home", "3")):
        rc, tail = _adb_shell(serial, adb_path, ["input", "keyevent", keycode])
        metrics["commands"].append({"name": name, "rc": rc, "tail": tail})
        _emit_progress(seq, stage=name.replace("_", "-"), ok=rc == 0)


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


def _verify_wizard_cleared(
    serial: str, adb_path: str, suw_packages: "list[str]",
) -> dict:
    still_enabled = [
        pkg for pkg in suw_packages
        if not _package_disabled(serial, adb_path, pkg)
    ]
    home_pkg = _resolve_home_package(serial, adb_path)
    home_is_wizard = bool(
        home_pkg and (
            home_pkg in suw_packages or _looks_like_wizard(home_pkg)
        )
    )
    ok = not still_enabled and not home_is_wizard
    return {
        "ok": ok,
        "still_enabled": still_enabled,
        "home_package": home_pkg,
        "home_is_wizard": home_is_wizard,
    }


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
    verify_wizard = _as_bool(args.get("verify_wizard_cleared"), default=True)
    discover = _as_bool(args.get("discover_setupwizard"), default=True)
    suw_package = str(
        _param_or_env(args, "setupwizard_package", "", _DEFAULT_SUW_PACKAGE)
    ).strip() or _DEFAULT_SUW_PACKAGE
    seed_packages = _parse_suw_packages(
        suw_package, args.get("extra_setupwizard_packages"))
    disable_suw = _as_bool(args.get("disable_setupwizard"), default=True)

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

    # ── root（#4083：直调 adb root，不走 shell；失败仍继续 best-effort）──
    root_rc, root_tail = _adb_cmd(serial, adb_path, ["root"])
    metrics["root"] = {"rc": root_rc, "tail": root_tail}
    _emit_progress(seq, stage="root", ok=root_rc == 0)
    if root_rc == 0:
        # #816：adbd 重启需 2–5s+，固定 sleep(2) 常落在 offline 窗口使整步
        # 失败率偏高；改为轮询 get-state==device（上限 15s）再进入命令序列，
        # 超时仍继续（后续命令 rc / verify 兜底，保持 root best-effort 语义）。
        if not _wait_adbd_ready(serial, adb_path):
            metrics["root_wait_timeout"] = True

    suw_packages = _discover_wizard_packages(
        serial, adb_path, seed_packages, discover=discover)
    metrics["wizard_packages"] = suw_packages
    metrics["wizard_seeds"] = seed_packages
    _emit_progress(seq, stage="discover-wizards", packages=suw_packages)

    # ── 命令序列：provisioning 标志 + setup_wizard_has_run + locales ─
    shell_commands: "list[tuple[str, list[str]]]" = [
        ("setup-complete", ["settings", "put", "secure",
                            "user_setup_complete", "1"]),
        ("device-provisioned", ["settings", "put", "global",
                                "device_provisioned", "1"]),
        ("setup-skipped", ["settings", "put", "secure",
                           "setup_skipped", "1"]),
        ("setup-wizard-has-run", ["settings", "put", "global",
                                  "setup_wizard_has_run", "1"]),
        ("system-locales", ["settings", "put", "system",
                            "system_locales", locales]),
    ]
    for name, shell_args in shell_commands:
        rc, tail = _adb_shell(serial, adb_path, shell_args)
        metrics["commands"].append({"name": name, "rc": rc, "tail": tail})
        _emit_progress(seq, stage=name.replace("_", "-"), ok=rc == 0)

    # ── 清场：停掉/禁用探测到的向导并键序回桌面 ───────────────────────
    _clear_suw_and_home(serial, adb_path, suw_packages, disable_suw, metrics, seq)

    # ── 回读核验（标志位 + 向导清场）──────────────────────────────────
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

        # v1.1.2（#1591）：验证失败重试——刷机后系统忙碌，settings put
        # 可能未即时落盘（实测重试多可收敛；原实现立即失败）。
        if not oobe_done:
            try:
                retries = int(_param_or_env(args, "verify_retries", "", 2))
            except (TypeError, ValueError):
                retries = 2
            for attempt in range(1, max(1, retries) + 1):
                time.sleep(15)
                for _name, shell_args in shell_commands:
                    _adb_shell(serial, adb_path, shell_args)
                time.sleep(5)
                ok_all = True
                for name, read_args in checks:
                    rc, tail = _adb_shell(serial, adb_path, read_args)
                    value = tail.strip().splitlines()[-1] if tail.strip() else ""
                    ok = rc == 0 and value.strip() == "1"
                    verify_report[name] = {"value": value.strip(), "ok": ok}
                    ok_all = ok_all and ok
                oobe_done = ok_all
                verify_report["ok"] = oobe_done
                verify_report["retry_attempts"] = attempt
                _emit_progress(seq, stage="verify-retry",
                               attempt=attempt, ok=oobe_done)
                if oobe_done:
                    break

    wizard_report = {"enabled": verify_wizard}
    if verify_wizard and oobe_done:
        wizard_report.update(
            _verify_wizard_cleared(serial, adb_path, suw_packages))
        if not wizard_report["ok"]:
            # 再清一次后重检，覆盖 disable 竞态
            time.sleep(2)
            _clear_suw_and_home(
                serial, adb_path, suw_packages, disable_suw, metrics, seq)
            time.sleep(2)
            wizard_report.update(
                _verify_wizard_cleared(serial, adb_path, suw_packages))
        oobe_done = oobe_done and bool(wizard_report.get("ok"))
        _emit_progress(seq, stage="verify-wizard", ok=wizard_report.get("ok"))

    # ── UI 焦点诊断（设备侧 grep；若仍停在向导则补清一次）────────────
    focus_line = _read_current_focus(serial, adb_path)
    ui_on_su = any(pkg.lower() in focus_line.lower() for pkg in suw_packages)
    if ui_on_su:
        time.sleep(3)
        _clear_suw_and_home(serial, adb_path, suw_packages, disable_suw, metrics, seq)
        focus_line = _read_current_focus(serial, adb_path)
        ui_on_su = any(pkg.lower() in focus_line.lower() for pkg in suw_packages)
        if verify_wizard and oobe_done:
            wizard_report.update(
                _verify_wizard_cleared(serial, adb_path, suw_packages))
            oobe_done = oobe_done and bool(wizard_report.get("ok"))
    metrics["ui_focus"] = {
        "line": focus_line.split("mCurrentFocus=")[-1][:160] if focus_line else "",
        "on_setupwizard": ui_on_su,
    }
    _emit_progress(seq, stage="ui-focus", on_setupwizard=ui_on_su)

    metrics["verify"] = verify_report
    metrics["wizard_cleared"] = wizard_report
    metrics["duration_seconds"] = round(time.time() - started_at, 2)
    _emit_progress(seq, stage="done", ok=oobe_done)

    if not oobe_done:
        failed = [k for k, v in verify_report.items()
                  if isinstance(v, dict) and v.get("ok") is False]
        values = [verify_report[k].get("value") for k in failed]
        wizard_hint = ""
        if verify_wizard and not wizard_report.get("ok", True):
            wizard_hint = (
                f"; wizard_cleared={wizard_report.get('still_enabled')} "
                f"home={wizard_report.get('home_package')}"
            )
        hint = (
            " (UI still on setupwizard; flags may have been reset by a "
            "later re-flash — re-run this step)" if ui_on_su else "")
        _output(False, error_message=(
            f"OOBE skip verification failed: {failed} (values: {values})"
            + wizard_hint + hint),
                metrics=metrics)
        return

    _output(True, metrics=metrics)


if __name__ == "__main__":
    main()
