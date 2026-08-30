# -*- coding: utf-8 -*-
"""PowerCycle 专项脚本共享库（powercycle_setup / powercycle_check / powercycle_finish 共用）。

移植自 stability_PowerCycle-Test/scripts/lib.ps1（AutoTestTool 后端，issue #462 P0b；
G15 对齐见 docs/notes/feature/2026-08-31-toolkit-android-tools-g15-alignment.md §3.2）。

P0 边界（G15 决策 D3/D4）：
- 固定 autotesttool 后端，不实现 MSSV（展锐相关，与 G7/#220 合并推进）；
- 只做 reboot 模式，poweroff（真关机+RTC 唤醒）配置直接校验失败；
- PC pc-watchdog 不移植——设备离线期间由平台心跳 UNKNOWN/恢复链路兜底，
  patrol 轮询对设备离线不判服务死亡。

- 环境/参数/stdout 契约与 sleep/mtbf 三件套一致：
  ``STP_DEVICE_SERIAL`` / ``STP_STEP_PARAMS``（JSON）/ stdout 单行 JSON ``{"success": ...}``。
- 配置解析层级：STP_STEP_PARAMS > STP_POWER_CYCLE_* env >
  ``{STP_AEE_NFS_ROOT}/power-cycle/{project}/test-config.properties``（可选）> 代码默认。
- 资源：APK 在 ``{powercycle_resources_dir}/{project}/AutoTestTool.apk``
  （默认 ``{agent}/resources/power-cycle/{project}/``；与 Sleep 同一 AutoTestTool.apk，
  包名 com.tinno.autotesttool，两专项在同一设备上互斥——部署互相覆盖）。
- 结果：``/sdcard/Android/data/com.tinno.autotesttool/files/PowerCycle/powercycle_result.txt``
  （旧路径 ``/sdcard/AutoTestTool/PowerCycle/`` 兜底），行格式见 ``parse_powercycle_result``。
- adb root 非硬性前置（prefs 有 run-as 兜底），但 REBOOT 权限是硬前置
  （``check_reboot_permission``：无 REBOOT 且无 su → fail-fast）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# 设备端常量（manifest 实测：与 Sleep 同一 AutoTestTool APK，PowerCycle 组件族）
_PKG = "com.tinno.autotesttool"
_SERVICE = "com.mediatek.schpwronoff.powercycle.PowerCycleService"
_ACTIVITY = "com.mediatek.schpwronoff.powercycle.PowerCycleActivity"
_KEEPALIVE_RECEIVER = "com.mediatek.schpwronoff.powercycle.PowerCycleAutoResumeReceiver"
_PREFS_FILE = "powercycle_runner.xml"
_PREFS_DIR = f"/data/data/{_PKG}/shared_prefs"
_RESULT_PATHS = (
    f"/sdcard/Android/data/{_PKG}/files/PowerCycle/powercycle_result.txt",
    "/sdcard/AutoTestTool/PowerCycle/powercycle_result.txt",
)

# lib.ps1:Read-PowerCycleConfig 内嵌默认值（文件缺键时用）
_DEFAULT_TEST_TIMES = 100
_DEFAULT_MODE = "reboot"
_DEFAULT_POWER_OFF_MINUTES = 1
_DEFAULT_WAIT_SECONDS = 3
_DEFAULT_TESTER = "tester"
_DEFAULT_AUTO_RESUME = "true"

# 结果文件行格式（设备端 PowerCycleService 追加写，时间戳前缀可带可无）
_TS_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\s+(.*)$")
_CYCLE_RE = re.compile(r"cycle\s+(\d+)/(\d+)\s+start")
_REBOOT_FAIL_RE = re.compile(r"reboot failed:\s*(.*)")
_FINISH_RE = re.compile(r"finished result=(PASS)")


# ---------------------------------------------------------------------------
# 环境 / 参数 / 输出契约（与 sleep/mtbf _lib 同款）
# ---------------------------------------------------------------------------

def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def param_or_env(cfg: dict, key: str, env_key: str, default):
    value = cfg.get(key)
    if value is not None and str(value) != "":
        return value
    raw = env(env_key, "")
    if raw != "":
        return raw
    return default


def adb_path() -> str:
    return env("STP_ADB_PATH", "adb")


def device_serial() -> str:
    serial = env("STP_DEVICE_SERIAL", "")
    if not serial:
        print(
            json.dumps({"success": False, "error_message": "STP_DEVICE_SERIAL is not set"}, ensure_ascii=False)
        )
        sys.exit(1)
    return serial


def params() -> dict:
    raw = env("STP_STEP_PARAMS", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def output_result(success: bool, **kwargs) -> None:
    print(json.dumps({"success": success, **kwargs}, ensure_ascii=False))


def progress_stamp(payload: dict) -> None:
    """#115 PROGRESS 打戳（stderr，reader B 识别并丢弃；不污染 stdout 结果契约）。"""
    sys.stderr.write(f"PROGRESS {json.dumps(payload, ensure_ascii=False)}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# ADB 封装
# ---------------------------------------------------------------------------

def adb(*args: str, timeout: int = 60) -> tuple[int, str, str]:
    """adb -s <serial> <args...>，返回 (returncode, stdout, stderr)。"""
    cmd = [adb_path(), "-s", device_serial()] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    return result.returncode, result.stdout or "", result.stderr or ""


def adb_shell(command: str, timeout: int = 60) -> str:
    _, out, _ = adb("shell", command, timeout=timeout)
    return out


def _push_file(local: Path, remote: str) -> None:
    rc, _, err = adb("push", str(local), remote, timeout=120)
    if rc != 0:
        raise RuntimeError(f"push 失败 {local.name} -> {remote}: {err.strip() or 'rc=%d' % rc}")


def device_online() -> bool:
    """adb get-state == device（reboot 周期中设备离线属正常，check 用）。"""
    rc, out, _ = adb("get-state", timeout=15)
    return rc == 0 and out.strip() == "device"


# ---------------------------------------------------------------------------
# 路径解析（G15 对齐 §4：与 Sleep 统一「配置走中心存储、工具走 resources」）
# ---------------------------------------------------------------------------

def project_name(cfg: dict) -> str:
    return str(param_or_env(cfg, "project", "STP_POWER_CYCLE_PROJECT", "legacy"))


def suite_dir(project: str) -> Path:
    root = env("STP_AEE_NFS_ROOT", "")
    if not root:
        raise RuntimeError("STP_AEE_NFS_ROOT is not set")
    return Path(root) / "power-cycle" / project


def results_dir(project: str) -> Path:
    return suite_dir(project) / "results"


def _default_resources_root() -> Path:
    """默认 resources 根：相对 Agent 目录解析（aimonkey/mtbf/sleep 先例同构）。"""
    return Path(__file__).resolve().parents[3] / "resources" / "power-cycle"


def resources_dir(cfg: dict) -> Path:
    base = cfg.get("powercycle_resources_dir") or env("STP_POWER_CYCLE_RESOURCES_DIR", str(_default_resources_root()))
    return Path(base) / project_name(cfg)


def parse_properties(content: str) -> dict:
    """test-config.properties 解析：跳过 # 注释，key=value 去空格（lib.ps1:Read-PowerCycleConfig 同款）。"""
    cfg = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([^=]+)=(.*)$", line)
        if match:
            cfg[match.group(1).strip()] = match.group(2).strip()
    return cfg


def read_properties(project: str) -> dict:
    """可选配置层：{STP_AEE_NFS_ROOT}/power-cycle/{project}/test-config.properties，缺失返回空。"""
    root = env("STP_AEE_NFS_ROOT", "")
    if not root:
        return {}
    path = Path(root) / "power-cycle" / project / "test-config.properties"
    try:
        return parse_properties(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def powercycle_config(cfg: dict) -> dict:
    """规范化配置（STP_STEP_PARAMS > STP_POWER_CYCLE_* env > properties > 代码默认）。

    P0 只做 reboot 模式（G15 D4）：mode=poweroff 直接校验失败（设备能力/充电保护未评估）。
    """
    project = project_name(cfg)
    props = read_properties(project)

    def pick(key: str, env_key: str, prop_key: str | None, default):
        value = cfg.get(key)
        if value is not None and str(value) != "":
            return value
        raw = env(env_key, "")
        if raw != "":
            return raw
        if prop_key and props.get(prop_key) not in (None, ""):
            return props[prop_key]
        return default

    mode = str(pick("mode", "STP_POWER_CYCLE_MODE", "test.mode", _DEFAULT_MODE))
    if mode != "reboot":
        raise ValueError(f"mode={mode!r} 不支持：P0 只做 reboot（poweroff 需 RTC 唤醒与充电保护评估，见 G15 D4）")
    return {
        "project": project,
        "test_times": int(pick("test_times", "STP_POWER_CYCLE_TEST_TIMES", "test.times", _DEFAULT_TEST_TIMES)),
        "mode": mode,
        "power_off_minutes": int(pick("power_off_minutes", "STP_POWER_CYCLE_POWER_OFF_MINUTES", "power.off.minutes", _DEFAULT_POWER_OFF_MINUTES)),
        "wait_seconds": int(pick("wait_seconds", "STP_POWER_CYCLE_WAIT_SECONDS", "wait.seconds", _DEFAULT_WAIT_SECONDS)),
        "tester": str(pick("tester", "STP_POWER_CYCLE_TESTER", "tester.name", _DEFAULT_TESTER)),
        "auto_resume": str(pick("auto_resume", "STP_POWER_CYCLE_AUTO_RESUME", "auto.resume", _DEFAULT_AUTO_RESUME)).lower() == "true",
        "install_apks": str(pick("install_apks", "STP_POWER_CYCLE_INSTALL_APKS", None, "true")).lower() == "true",
        "reset_count": str(pick("reset_count", "STP_POWER_CYCLE_RESET_COUNT", None, "true")).lower() == "true",
    }


# ---------------------------------------------------------------------------
# 文件工具
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_size_from_ls(ls: str) -> int:
    """从 ls -l 输出提取文件大小（busybox 变体字段不一，扫描首个纯数字 token）。"""
    if "No such file" in ls or not ls:
        return 0
    size = next((t for t in ls.split()[2:] if t.isdigit()), None)
    if size is None:
        return 0
    try:
        return int(size)
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# 设备交互（移植自 lib.ps1 AutoTestTool 后端，函数级对应）
# ---------------------------------------------------------------------------

def is_root() -> bool:
    return adb_shell("id -u", timeout=15).strip() == "0"


def try_adb_root() -> bool:
    """尽力 adb root（prefs 写入 run-as 兜底；lib.ps1 同款不阻断）。"""
    adb("root", timeout=15)
    for _ in range(3):
        time.sleep(1)
        if is_root():
            return True
    return False


def get_app_uid() -> int:
    """dumpsys package 解析 uid：android.uid.system → 1000（lib.ps1:Get-PowerCycleAppUid 同款）。"""
    info = adb_shell(f"dumpsys package {_PKG}", timeout=30)
    if "android.uid.system" in info:
        return 1000
    match = re.search(r"\buserId=(\d+)", info)
    if match:
        return int(match.group(1))
    match = re.search(r"\buid=(\d+)", info)
    if match:
        return int(match.group(1))
    raise RuntimeError(f"无法解析 {_PKG} 的 uid（dumpsys package 输出无 sharedUser/userId/uid）")


def get_prefs_xml() -> str:
    """run-as cat prefs（lib.ps1:Get-PowerCyclePrefsXml 同款；无文件/权限不足返回空串）。"""
    _, out, _ = adb("shell", f"run-as {_PKG} cat shared_prefs/{_PREFS_FILE}", timeout=30)
    text = out.strip()
    if text and not any(t in text for t in ("Permission denied", "No such file", "run-as:")):
        return text
    return ""


def repair_prefs_ownership() -> None:
    """prefs 读不到且可 root → 删旧文件重建（system uid 迁移坑，lib.ps1:Repair-PowerCyclePrefsOwnership 同款）。"""
    if get_prefs_xml():
        return
    if not is_root():
        return
    adb_shell(f"rm -f {_PREFS_DIR}/{_PREFS_FILE}", timeout=30)


def push_prefs_xml(content: str) -> None:
    """写 prefs：root → push + chown uid:uid + chmod 660；无 root → run-as 重定向兜底。"""
    if is_root():
        uid = get_app_uid()
        adb_shell(f"mkdir -p {_PREFS_DIR}", timeout=30)
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
            tmp.write(content.encode("utf-8"))
            tmp_path = tmp.name
        try:
            _push_file(Path(tmp_path), f"{_PREFS_DIR}/{_PREFS_FILE}")
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        adb_shell(f"chown {uid}:{uid} {_PREFS_DIR}/{_PREFS_FILE} && chmod 660 {_PREFS_DIR}/{_PREFS_FILE}", timeout=30)
        return
    cmd = [adb_path(), "-s", device_serial(), "shell", f"run-as {_PKG} sh -c 'cat > shared_prefs/{_PREFS_FILE}'"]
    result = subprocess.run(cmd, input=content.encode("utf-8"), capture_output=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"run-as 写 prefs 失败: {result.stderr.strip() or 'rc=%d' % result.returncode}")


def update_prefs_field(xml: str, name: str, value: str, type_: str) -> str:
    """字段替换/追加（lib.ps1:Update-PowerCyclePrefsField 同款）。"""
    if type_ == "int":
        replacement = f'<int name="{name}" value="{value}"/>'
    elif type_ == "string":
        replacement = f'<string name="{name}">{value}</string>'
    elif type_ == "boolean":
        replacement = f'<boolean name="{name}" value="{value}"/>'
    else:
        raise ValueError(f"unknown prefs field type: {type_}")
    if f'name="{name}"' in xml:
        return re.sub(rf'<(int|string|boolean) name="{name}"[^/]*/>', replacement, xml)
    return xml.replace("</map>", f"    {replacement}\n</map>")


def build_prefs_xml(
    test_times: int,
    mode: str,
    power_off_minutes: int,
    wait_seconds: int,
    tester: str,
    auto_resume: bool,
    current_count: int = 0,
) -> str:
    """整写 prefs（lib.ps1:Set-PowerCyclePrefs 同款 map；current_count 由调用方解析读回）。"""
    resume = "true" if auto_resume else "false"
    return (
        "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n"
        "<map>\n"
        f'    <int name="test_times" value="{test_times}"/>\n'
        f'    <int name="current_count" value="{current_count}"/>\n'
        f'    <string name="mode">{mode}</string>\n'
        f'    <int name="power_off_minutes" value="{power_off_minutes}"/>\n'
        f'    <int name="wait_seconds" value="{wait_seconds}"/>\n'
        f'    <boolean name="auto_resume" value="{resume}"/>\n'
        '    <boolean name="running" value="false"/>\n'
        f'    <string name="tester_name">{tester}</string>\n'
        "</map>\n"
    )


def set_prefs(cfg: dict) -> int:
    """deploy/run 语义合并：repair → 读 current_count（reset_count=false 续跑）→ 整写，返回 current_count。"""
    repair_prefs_ownership()
    current_count = 0
    if not cfg["reset_count"]:
        existing = get_prefs_xml()
        match = re.search(r'name="current_count" value="(\d+)"', existing)
        if match:
            current_count = int(match.group(1))
    push_prefs_xml(build_prefs_xml(
        int(cfg["test_times"]), str(cfg["mode"]), int(cfg["power_off_minutes"]),
        int(cfg["wait_seconds"]), str(cfg["tester"]), bool(cfg["auto_resume"]), current_count,
    ))
    return current_count


def check_reboot_permission() -> str | None:
    """返回 reboot 方式：'granted'（REBOOT 权限）| 'su'（su 兜底）| None（两者皆无）。

    lib.ps1:Test-PowerCycleRebootPermission 同款：REBOOT 权限或 su 任一可用即可，
    否则 PowerCycle 无法重启设备（后端分派已按 G15 D3 固定 autotesttool，无 MSSV 兜底）。
    """
    info = adb_shell(f"dumpsys package {_PKG}", timeout=30)
    if "android.permission.REBOOT: granted=true" in info:
        return "granted"
    if adb_shell("which su", timeout=15).strip():
        return "su"
    return None


def set_device_stability() -> None:
    """开关机测试要求屏幕常亮（与 Sleep 相反）：stayon true + 超长超时 + 插电常亮（lib.ps1 同款）。"""
    try_adb_root()
    adb_shell("svc power stayon true", timeout=30)
    adb_shell("settings put system screen_off_timeout 2147483647", timeout=30)
    adb_shell("settings put global stay_on_while_plugged_in 7", timeout=30)
    adb_shell("locksettings set-disabled true", timeout=30)


def grant_storage() -> None:
    """存储权限 + MANAGE_EXTERNAL_STORAGE（lib.ps1:Grant-PowerCycleStorage 同款）。"""
    adb_shell(f"pm grant {_PKG} android.permission.READ_EXTERNAL_STORAGE", timeout=30)
    adb_shell(f"pm grant {_PKG} android.permission.WRITE_EXTERNAL_STORAGE", timeout=30)
    try_adb_root()
    adb_shell(f"cmd appops set {_PKG} MANAGE_EXTERNAL_STORAGE allow", timeout=30)


def install_apk(apk: Path) -> None:
    """force-stop → uninstall → install -r（lib.ps1:Install-PowerCycleApk 同款，platform 签名包）。"""
    adb_shell(f"am force-stop {_PKG}", timeout=30)
    adb("uninstall", _PKG, timeout=60)
    rc, out, _ = adb("install", "-r", str(apk), timeout=300)
    if rc != 0 or "Success" not in out:
        raise RuntimeError(f"安装失败: {apk.name}: {out.strip() or 'rc=%d' % rc}")


def service_alive() -> bool:
    return "PowerCycleService" in adb_shell(f"dumpsys activity services {_PKG}", timeout=30)


def start_task() -> None:
    """force-stop → running=true → Activity → 前台服务(START) → KEEPALIVE 广播（lib.ps1:Start-PowerCycleTask 同款）。

    先拉起 Activity 再起前台服务：部分机型（Z2582）后台 start-foreground-service 会被
    AutoLaunch 拦截（lib.ps1 注释同款）。
    """
    adb_shell(f"am force-stop {_PKG}", timeout=30)
    xml = get_prefs_xml()
    if xml:
        push_prefs_xml(update_prefs_field(xml, "running", "true", "boolean"))
    adb_shell(f"am start -n {_PKG}/{_ACTIVITY}", timeout=30)
    time.sleep(2)
    adb_shell(
        f"am start-foreground-service -n {_PKG}/{_SERVICE} -a com.tinno.autotesttool.action.POWER_CYCLE_START",
        timeout=30,
    )
    time.sleep(2)
    adb_shell(
        f"am broadcast -a com.tinno.autotesttool.action.POWER_CYCLE_KEEPALIVE -n {_PKG}/{_KEEPALIVE_RECEIVER}",
        timeout=30,
    )


def set_stop_flags() -> None:
    """auto_resume=false + running=false（lib.ps1:Set-PowerCycleStopFlags 同款；prefs 缺失时整写最小 map）。"""
    repair_prefs_ownership()
    xml = get_prefs_xml()
    if not xml:
        content = (
            "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n"
            "<map>\n"
            '    <boolean name="auto_resume" value="false"/>\n'
            '    <boolean name="running" value="false"/>\n'
            "</map>\n"
        )
    else:
        content = update_prefs_field(update_prefs_field(xml, "auto_resume", "false", "boolean"), "running", "false", "boolean")
    push_prefs_xml(content)


def stop_task(force: bool) -> None:
    """set_stop_flags → 优雅 STOP → force-stop 兜底（lib.ps1:Stop-PowerCycleTask 同款）。

    PC pc-watchdog 不移植（G15 决策：设备离线由平台心跳 UNKNOWN/恢复链路兜底）。
    """
    set_stop_flags()
    if service_alive():
        adb_shell(
            f"am startservice -n {_PKG}/{_SERVICE} -a com.tinno.autotesttool.action.POWER_CYCLE_STOP",
            timeout=30,
        )
        time.sleep(3)
    adb_shell(f"am force-stop {_PKG}", timeout=30)
    time.sleep(1)
    if force and service_alive():
        raise RuntimeError("停止 PowerCycleService 失败（优雅停止 + force-stop 均未生效）")


def result_paths() -> tuple[str, ...]:
    """主路径 + 旧路径兜底（设备端 getResultDir 回退同款）。"""
    return _RESULT_PATHS


# ---------------------------------------------------------------------------
# powercycle_result.txt 解析（纯文本行，无 XML；join 键 = cycle 分子/分母）
# ---------------------------------------------------------------------------

def parse_powercycle_result(content: bytes) -> dict:
    """解析 powercycle_result.txt → 摘要 + entries。

    行格式（设备端 PowerCycleService 追加写，时间戳前缀可带可无）：
      cycle N/M start
      reboot failed: <原因>
      stopped by user
      finished result=PASS      （开关机测试只有 PASS 一种完成值；无该行 = 未收尾）
    """
    entries = []
    reboot_failures = 0
    cycles_done = 0
    expected_cycles = 0
    final_status = None
    for raw_line in content.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        match = _TS_PREFIX_RE.match(line)
        if match:
            line = match.group(1).strip()
        if not line:
            continue
        cycle = _CYCLE_RE.search(line)
        if cycle:
            n, total = int(cycle.group(1)), int(cycle.group(2))
            cycles_done = n
            expected_cycles = total
            entries.append({"kind": "cycle", "cycle": n, "total": total})
            continue
        fail = _REBOOT_FAIL_RE.search(line)
        if fail:
            reboot_failures += 1
            entries.append({"kind": "reboot_failed", "message": fail.group(1)})
            continue
        finish = _FINISH_RE.search(line)
        if finish:
            final_status = finish.group(1)
            entries.append({"kind": "finished", "result": final_status})
            continue
        if "stopped by user" in line:
            entries.append({"kind": "stopped"})
            continue
    return {
        "cycles_done": cycles_done,
        "expected_cycles": expected_cycles,
        "reboot_failures": reboot_failures,
        "final_status": final_status,
        "entries": entries,
    }
