# -*- coding: utf-8 -*-
"""Sleep 专项脚本共享库（sleep_setup / sleep_check / sleep_finish 三件套共用）。

移植自 stability_Sleep-Test/scripts/lib.ps1（issue #462 P0a；
G15 对齐见 docs/notes/feature/2026-08-31-toolkit-android-tools-g15-alignment.md §3.1）。

- 环境/参数/stdout 契约与 mtbf 三件套一致：
  ``STP_DEVICE_SERIAL`` / ``STP_STEP_PARAMS``（JSON）/ stdout 单行 JSON ``{"success": ...}``。
- 配置解析层级：STP_STEP_PARAMS > STP_SLEEP_* env > ``{STP_AEE_NFS_ROOT}/sleep/{project}/test-config.properties``
  （可选，lib.ps1:Read-SleepTestConfig 同款）> 代码默认。
- 资源：APK 在 ``{sleep_resources_dir}/{project}/AutoTestTool.apk``
  （默认 ``{agent}/resources/sleep/{project}/``，aimonkey/mtbf resources 先例）。
- 结果：``/sdcard/Android/data/com.tinno.autotesttool/files/SleepTest/sleep_test_result.txt``
  （旧路径 ``/sdcard/AutoTestTool/SleepTest/`` 兜底），行格式见 ``parse_sleep_result``。
- adb root 非硬性前置（prefs 有 run-as 兜底）；设备稳定性等设置尽力 root，失败不阻断
  （lib.ps1 同款语义）。
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

# 设备端常量（manifest 实测：AutoTestTool platform 签名、sharedUserId=android.uid.system）
_PKG = "com.tinno.autotesttool"
_SERVICE = "com.mediatek.schpwronoff.sleeptest.SleepTestService"
_ACTIVITY = "com.mediatek.schpwronoff.sleeptest.SleepTestActivity"
_KEEPALIVE_RECEIVER = "com.mediatek.schpwronoff.sleeptest.SleepTestKeepAliveReceiver"
_PREFS_FILE = "sleep_test_runner.xml"
_PREFS_DIR = f"/data/data/{_PKG}/shared_prefs"
_RESULT_PATHS = (
    f"/sdcard/Android/data/{_PKG}/files/SleepTest/sleep_test_result.txt",
    "/sdcard/AutoTestTool/SleepTest/sleep_test_result.txt",
)

# lib.ps1:Read-SleepTestConfig 内嵌默认值（文件缺键时用）
_DEFAULT_TEST_TIMES = 100
_DEFAULT_WAKE_SECONDS = 60
_DEFAULT_SLEEP_SECONDS = 300
_DEFAULT_TESTER = "tester"
_DEFAULT_AUTO_RESUME = "true"

# 结果文件行格式（设备端 SleepTestService 追加写，时间戳前缀可带可无）
_TS_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\s+(.*)$")
_CYCLE_RE = re.compile(r"cycle\s+(\d+)/(\d+)\s+(wake OK|wake FAIL)\s+screen=(ON|OFF)")
_SLEEP_RE = re.compile(r"go sleep\s+(\d+)s\s+screen=(ON|OFF)")
_FINISH_RE = re.compile(r"finished result=(PASS|FAIL)")


# ---------------------------------------------------------------------------
# 环境 / 参数 / 输出契约（与 mtbf _lib 同款）
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

def decode_device_output(raw: bytes | None) -> str:
    """设备 / adb 输出解码：**宽容 UTF-8**（#3463 G1 F2，port gpu_setup #3069 形态）。

    设备侧输出可能是任意字节。``text=True`` 按 locale 严格解码，遇到一个非
    UTF-8 字节就抛 ``UnicodeDecodeError``——它不是 ``OSError``，调用点无从兜住。
    坏字节替换成 U+FFFD，不影响子串匹配类判据。
    """
    return (raw or b"").decode("utf-8", errors="replace")


def adb(*args: str, timeout: int = 60) -> tuple[int, str, str]:
    """adb -s <serial> <args...>，返回 (returncode, stdout, stderr)。"""
    cmd = [adb_path(), "-s", device_serial()] + list(args)
    try:
        # #3463 G1（F2）：显式收字节再解宽容码，不用 ``text=True``——后者严格解码，
        # 一个坏字节即 UnicodeDecodeError，且无法从调用点兜住。
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    return (
        result.returncode,
        decode_device_output(result.stdout),
        decode_device_output(result.stderr),
    )


def adb_shell(command: str, timeout: int = 60) -> str:
    _, out, _ = adb("shell", command, timeout=timeout)
    return out


def _push_file(local: Path, remote: str) -> None:
    rc, _, err = adb("push", str(local), remote, timeout=120)
    if rc != 0:
        raise RuntimeError(f"push 失败 {local.name} -> {remote}: {err.strip() or 'rc=%d' % rc}")


# ---------------------------------------------------------------------------
# 路径解析（G15 对齐 §4：与 PowerCycle 统一「配置走中心存储、工具走 resources」）
# ---------------------------------------------------------------------------

def project_name(cfg: dict) -> str:
    return str(param_or_env(cfg, "project", "STP_SLEEP_PROJECT", "legacy"))


def suite_dir(project: str) -> Path:
    root = env("STP_AEE_NFS_ROOT", "")
    if not root:
        raise RuntimeError("STP_AEE_NFS_ROOT is not set")
    return Path(root) / "sleep" / project


def results_dir(project: str) -> Path:
    return suite_dir(project) / "results"


def _default_resources_root() -> Path:
    """默认 resources 根：相对 Agent 目录解析（aimonkey/mtbf 先例同构）。"""
    return Path(__file__).resolve().parents[3] / "resources" / "sleep"


def resources_dir(cfg: dict) -> Path:
    base = cfg.get("sleep_resources_dir") or env("STP_SLEEP_RESOURCES_DIR", str(_default_resources_root()))
    return Path(base) / project_name(cfg)


def parse_properties(content: str) -> dict:
    """test-config.properties 解析：跳过 # 注释，key=value 去空格（lib.ps1:Read-SleepTestConfig 同款）。"""
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
    """可选配置层：{STP_AEE_NFS_ROOT}/sleep/{project}/test-config.properties，缺失返回空。"""
    root = env("STP_AEE_NFS_ROOT", "")
    if not root:
        return {}
    path = Path(root) / "sleep" / project / "test-config.properties"
    try:
        return parse_properties(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def sleep_config(cfg: dict) -> dict:
    """规范化配置（STP_STEP_PARAMS > STP_SLEEP_* env > properties > 代码默认）。"""
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

    return {
        "project": project,
        "test_times": int(pick("test_times", "STP_SLEEP_TEST_TIMES", "test.times", _DEFAULT_TEST_TIMES)),
        "wake_seconds": int(pick("wake_seconds", "STP_SLEEP_WAKE_SECONDS", "wake.seconds", _DEFAULT_WAKE_SECONDS)),
        "sleep_seconds": int(pick("sleep_seconds", "STP_SLEEP_SLEEP_SECONDS", "sleep.seconds", _DEFAULT_SLEEP_SECONDS)),
        "tester": str(pick("tester", "STP_SLEEP_TESTER", "tester.name", _DEFAULT_TESTER)),
        "auto_resume": str(pick("auto_resume", "STP_SLEEP_AUTO_RESUME", "auto.resume", _DEFAULT_AUTO_RESUME)).lower() == "true",
        "install_apks": str(pick("install_apks", "STP_SLEEP_INSTALL_APKS", None, "true")).lower() == "true",
        "reset_count": str(pick("reset_count", "STP_SLEEP_RESET_COUNT", None, "true")).lower() == "true",
        "zte_optimize": str(pick("zte_optimize", "STP_SLEEP_ZTE_OPTIMIZE", None, "true")).lower() == "true",
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
# 设备交互（移植自 lib.ps1，函数级对应）
# ---------------------------------------------------------------------------

def is_root() -> bool:
    return adb_shell("id -u", timeout=15).strip() == "0"


def try_adb_root() -> bool:
    """尽力 adb root（sleep 非硬性前置；失败走 run-as 兜底，lib.ps1 同款不阻断）。

    adb root 后 adbd 会重启，shell 短暂不可用——重试数次，避免瞬时误判。
    """
    adb("root", timeout=15)
    for _ in range(3):
        time.sleep(1)
        if is_root():
            return True
    return False


def get_app_uid() -> int:
    """dumpsys package 解析 uid：android.uid.system → 1000（lib.ps1:Get-SleepTestAppUid 同款）。"""
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


_PREFS_ABSENT_SENTINEL = "__STP_PREFS_ABSENT__"


def _root_read_prefs() -> tuple[str, str]:
    """root 下一次探测同时给出**存在性与内容**（#2979，port 自 powercycle_setup），
    返回 ``(kind, text)``：

    - ``"ok"``       可读且非空——text 即 prefs XML；
    - ``"empty"``    存在 + 读取成功但内容为空——repair 判据**唯一**接受的损坏证据；
    - ``"absent"``   明确不存在（无可删）；
    - ``"denied"``   读取被拒（SELinux / 属主异常等，root 下本不应出现——按未知处理）；
    - ``"transient"`` adb 超时 / rc 不可归类（未知）。

    为什么必须同源（#2979）：cat 与 test -f 拆成两次独立 adb 调用时，「一败一成」
    的交错把瞬时读失败伪造成「文件在但读不到」的删除证据，误删健康 prefs
    （续跑 current_count 归零、auto_resume 断链）。
    """
    probe = (
        f"if [ ! -f {_PREFS_DIR}/{_PREFS_FILE} ]; then echo {_PREFS_ABSENT_SENTINEL}; "
        f"else cat {_PREFS_DIR}/{_PREFS_FILE} 2>&1; fi"
    )
    rc, out, err = adb("shell", probe, timeout=30)
    text = (out or "").strip()
    if rc != 0:
        if _PREFS_ABSENT_SENTINEL in text or "No such file" in text or "No such file" in (err or ""):
            return "absent", ""
        if "Permission denied" in text or "Permission denied" in (err or ""):
            return "denied", ""
        return "transient", ""      # adb 超时（rc=-1）与其它不可归类失败
    if text == _PREFS_ABSENT_SENTINEL:
        return "absent", ""
    if not text:
        return "empty", ""          # 存在 + 读成功 + 内容空 = 真损坏
    if "Permission denied" in text:
        return "denied", ""
    if "No such file" in text:
        return "absent", ""
    return "ok", text


def _run_as_read_prefs() -> tuple[str, str]:
    """非 root 的 run-as 读取证据（#3463 G1：与 ``_root_read_prefs`` 同一 kind 集）。

    AutoTestTool 是 platform 签名 shared-uid system app，AOSP 对 non-debuggable
    包恒拒绝 run-as——旧实现把这种恒「读空」当成不存在整写最小 map。这里把
    被拒显式化：``run-as:`` / Permission denied → ``denied``；No such file →
    ``absent``；rc≠0 不可归类 → ``transient``；读成功且内容为空 → ``empty``。
    """
    rc, out, err = adb("shell", f"run-as {_PKG} cat shared_prefs/{_PREFS_FILE}", timeout=30)
    text = (out or "").strip()
    if rc != 0:
        if "No such file" in text or "No such file" in (err or ""):
            return "absent", ""
        if "Permission denied" in text or "Permission denied" in (err or "") or "run-as:" in text:
            return "denied", ""
        return "transient", ""
    if "run-as:" in text or "Permission denied" in text:
        return "denied", ""
    if "No such file" in text:
        return "absent", ""
    if not text:
        return "empty", ""
    return "ok", text


def read_prefs_evidence() -> tuple[str, str]:
    """prefs 读取的同源证据：root → ``_root_read_prefs``；非 root → ``_run_as_read_prefs``。"""
    if is_root():
        return _root_read_prefs()
    return _run_as_read_prefs()


def get_prefs_xml() -> str:
    """读 prefs（#3463 G1，port #2846/#2979 形态）：**root 优先**，无 root 才走 run-as。

    读不到/读空返回空串；**空串只表示「此刻读不到」，不表示「文件不存在」**
    （存在性/删除判定统一走 ``read_prefs_evidence`` 的单调用同源证据）。
    """
    kind, text = read_prefs_evidence()
    return text if kind == "ok" else ""


def repair_prefs_ownership() -> None:
    """prefs 读不到时按**同源可读性证据**决定删旧重建（#3463 G1，收紧 #2979 形态）。

    - 非 root 一律不动；
    - 单次探测 ``_root_read_prefs``：``ok`` 不删；``absent`` 无可删；
      ``denied``/``transient``（rc≠0、超时、被拒）判**未知**——保留文件；
    - **只有**「文件存在 + 读取成功 + 内容为空」这一种确定性损坏形态才 ``rm -f``
      （健康 prefs 永不为空；空文件应用自己也读不回，重建是唯一出路）。
    """
    if not is_root():
        return
    kind, _ = _root_read_prefs()
    if kind == "empty":
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
    """字段替换/追加（lib.ps1:Update-SleepTestPrefsField 同款）。"""
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
    wake_seconds: int,
    sleep_seconds: int,
    tester: str,
    auto_resume: bool,
    current_count: int = 0,
) -> str:
    """整写 prefs（lib.ps1:Set-SleepTestPrefs 同款 map；current_count 由调用方解析读回）。"""
    resume = "true" if auto_resume else "false"
    return (
        "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n"
        "<map>\n"
        f'    <int name="test_times" value="{test_times}"/>\n'
        f'    <int name="current_count" value="{current_count}"/>\n'
        f'    <int name="wake_seconds" value="{wake_seconds}"/>\n'
        f'    <int name="sleep_seconds" value="{sleep_seconds}"/>\n'
        f'    <boolean name="auto_resume" value="{resume}"/>\n'
        '    <boolean name="running" value="false"/>\n'
        '    <string name="phase">idle</string>\n'
        f'    <string name="tester_name">{tester}</string>\n'
        "</map>\n"
    )


def set_prefs(cfg: dict) -> int:
    """deploy/run 语义合并：repair → 读 current_count（reset_count=false 续跑）→ 整写，返回 current_count。

    #3463 G1-sleep-2 ②：``reset_count=false`` 时按**同源读取证据** kind 分流——
    ``ok`` 解析 current_count；``absent`` 取 0（真的没有 prefs，正常部署形态）；
    ``transient`` / ``denied`` / ``empty``（内容空）**raise**——「读不到」不是
    「没有」，不得以 current_count=0 整写完整 prefs 覆盖续跑计数（#2979 同形态，
    只是写的是 full map 而非最小 map）。
    """
    repair_prefs_ownership()
    current_count = 0
    if not cfg["reset_count"]:
        kind, existing = read_prefs_evidence()
        if kind not in ("ok", "absent"):
            raise RuntimeError(
                f"prefs 读取证据不可判定（kind={kind}, file={_PREFS_FILE}）——"
                "拒绝以 current_count=0 整写完整 prefs 覆盖续跑计数（#3463 G1-sleep-2）；"
                "此为可重试失败，请重试本步骤"
            )
        match = re.search(r'name="current_count" value="(\d+)"', existing)
        if match:
            current_count = int(match.group(1))
    push_prefs_xml(build_prefs_xml(
        int(cfg["test_times"]), int(cfg["wake_seconds"]), int(cfg["sleep_seconds"]),
        str(cfg["tester"]), bool(cfg["auto_resume"]), current_count,
    ))
    return current_count


def set_zte_smart_optimize_allowed() -> None:
    """ZTE 智能优化白名单（lib.ps1:Set-ZteAppSmartOptimizeAllowed 同款；尽力而为）。

    仅当设备装有 com.zte.heartyservice.strategy 且可 root 时执行；否则静默跳过。
    写库失败不阻断（ps1 同款 warning 语义）。
    """
    zte_pkg = "com.zte.heartyservice.strategy"
    if "package:" not in adb_shell(f"pm path {zte_pkg}", timeout=30):
        return
    if not is_root():
        return
    db = f"/data/user/0/{zte_pkg}/databases/UserStrategy.db"
    sh = (
        f'if [ ! -f "{db}" ]; then exit 0; fi\n'
        f'sqlite3 "{db}" "INSERT INTO app_settings (pkg_name, editable, locked, self_start_mode, related_start_mode, bg_run_mode, app_user_install) SELECT \'{_PKG}\', 1, 0, 3, 3, 3, 1 WHERE NOT EXISTS (SELECT 1 FROM app_settings WHERE pkg_name=\'{_PKG}\');"\n'
        f'sqlite3 "{db}" "UPDATE app_settings SET self_start_mode=3, related_start_mode=3, bg_run_mode=3 WHERE pkg_name=\'{_PKG}\';"\n'
        f'sqlite3 "{db}" "SELECT pkg_name,self_start_mode,related_start_mode,bg_run_mode FROM app_settings WHERE pkg_name=\'{_PKG}\';"\n'
    )
    remote = "/data/local/tmp/sleeptest-zte-allow.sh"
    with tempfile.NamedTemporaryFile(suffix=".sh", delete=False) as tmp:
        tmp.write(sh.encode("utf-8"))
        tmp_path = tmp.name
    try:
        _push_file(Path(tmp_path), remote)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    rc, out, _ = adb("shell", f"sh {remote}", timeout=30)
    if rc != 0 or not re.search(rf"{re.escape(_PKG)}\|3\|3\|3", out):
        pass  # 尽力而为：写库失败不阻断


def set_device_stability() -> None:
    """休眠测试必须允许灭屏（与 MTBF/PowerCycle 相反）：关 USB 常亮、30 分钟超时、
    appops/deviceidle 白名单、锁屏关闭（lib.ps1:Set-SleepTestDeviceStability 同款）。"""
    try_adb_root()
    adb_shell("svc power stayon false", timeout=30)
    adb_shell("settings put global stay_on_while_plugged_in 0", timeout=30)
    adb_shell("settings put system screen_off_timeout 1800000", timeout=30)
    adb_shell(f"appops set {_PKG} WRITE_SETTINGS allow", timeout=30)
    adb_shell(f"appops set {_PKG} SYSTEM_ALERT_WINDOW allow", timeout=30)
    adb_shell(f"dumpsys deviceidle whitelist +{_PKG}", timeout=30)
    adb_shell(f"cmd appops set {_PKG} RUN_ANY_IN_BACKGROUND allow", timeout=30)
    adb_shell("locksettings set-disabled true", timeout=30)


def grant_storage() -> None:
    """存储权限 + MANAGE_EXTERNAL_STORAGE（lib.ps1:Grant-SleepTestStorage 同款）。"""
    adb_shell(f"pm grant {_PKG} android.permission.READ_EXTERNAL_STORAGE", timeout=30)
    adb_shell(f"pm grant {_PKG} android.permission.WRITE_EXTERNAL_STORAGE", timeout=30)
    try_adb_root()
    adb_shell(f"cmd appops set {_PKG} MANAGE_EXTERNAL_STORAGE allow", timeout=30)


def install_apk(apk: Path) -> None:
    """force-stop → uninstall → install -r（lib.ps1:Install-SleepTestApk 同款，platform 签名包）。"""
    adb_shell(f"am force-stop {_PKG}", timeout=30)
    adb("uninstall", _PKG, timeout=60)
    rc, out, _ = adb("install", "-r", str(apk), timeout=300)
    if rc != 0 or "Success" not in out:
        raise RuntimeError(f"安装失败: {apk.name}: {out.strip() or 'rc=%d' % rc}")


def service_alive() -> bool:
    return "SleepTestService" in adb_shell(f"dumpsys activity services {_PKG}", timeout=30)


def start_task() -> None:
    """force-stop → running=true → Activity → 前台服务(START) → KEEPALIVE 广播（lib.ps1:Start-SleepTestTask 同款）。

    #3463 G1-sleep-2 ③：prefs 证据非 ``ok`` 时 raise——旧形态「读空即跳过置
    running=true、照常启动服务」会让服务带着 stop flags 起跑，设备重启后
    boot receiver 不再续跑（表面启动、实际断链）。瞬时不可读按可重试失败处理，
    步骤重试时窗口即消失。
    """
    adb_shell(f"am force-stop {_PKG}", timeout=30)
    kind, xml = read_prefs_evidence()
    if kind != "ok":
        raise RuntimeError(
            f"prefs 读取证据非 ok（kind={kind}, file={_PREFS_FILE}）——"
            "拒绝在未置 running=true 的情况下启动服务（#3463 G1-sleep-2）；"
            "此为可重试失败，请重试本步骤"
        )
    push_prefs_xml(update_prefs_field(xml, "running", "true", "boolean"))
    adb_shell(f"am start -n {_PKG}/{_ACTIVITY}", timeout=30)
    time.sleep(2)
    adb_shell(
        f"am start-foreground-service -n {_PKG}/{_SERVICE} -a com.tinno.autotesttool.action.SLEEP_TEST_START",
        timeout=30,
    )
    time.sleep(2)
    adb_shell(
        f"am broadcast -a com.tinno.autotesttool.action.SLEEP_TEST_KEEPALIVE -n {_PKG}/{_KEEPALIVE_RECEIVER}",
        timeout=30,
    )


def set_stop_flags() -> None:
    """auto_resume=false + running=false（lib.ps1:Set-SleepTestStopFlags 同款；#3463 G1 收紧）。

    旧判据「读空＝不存在 → 整写最小 map」：读空含瞬态失败与 run-as 被拒，
    整写最小 map 会把健康 prefs 的 test_times/current_count 等整段丢掉
    （与 #2979 误删同形态）。收紧为按**同源读取证据**决定：

    - ``ok``：在现有内容上原位更新两字段；
    - ``absent``：整写最小 map（唯一合法的整写出口；root 下的 ``empty``
      已由 ``repair_prefs_ownership`` 删除，重探即 ``absent``）；
    - ``transient`` / ``denied``（及无 root 无法删除的 ``empty``）：
      **保留文件、不写任何字节**，抛可重试失败——瞬时窗口在步骤重试时
      即消失，重试语义由 plan step 的 ``retry`` 承载。
    """
    repair_prefs_ownership()
    kind, xml = read_prefs_evidence()
    if kind in ("transient", "denied", "empty"):
        raise RuntimeError(
            f"prefs 读取不可判定（kind={kind}, file={_PREFS_FILE}）——"
            "拒绝以最小 map 覆盖现有 prefs（#3463 G1）；此为可重试失败，请重试本步骤"
        )
    if kind == "absent":
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


def _verify_stop_flags() -> None:
    """#894：写后回读验证 running=false（#3463 G1-sleep-2 ④ 改走同源证据）。

    push_prefs_xml 的写入失败（boot 早期 /data 未挂载 run-as 失败等）可能
    静默——残留 running=true 会在设备 boot 后经 AutoTestTool boot receiver
    拉起服务。回读判据：root 下走 ``read_prefs_evidence`` 的 **root 探测**
    （旧形态在 get_prefs_xml 尚未 root 优先时按 run-as 回读，shared-uid 包
    run-as 恒拒 ⇒ root 写已成功也两轮读空 ⇒ 假失败）；``ok`` 且含
    running=false 才通过。非 ok/未置 false 按既有语义重试一次
    （set_stop_flags），仍不成立 raise——真正读不到时由 set_stop_flags 内部
    的同源判据抛可重试失败。

    （本函数曾在 main 上有两个同名定义、首个含 ``_verify_stop_flags()``
    自递归且被第二个定义整体遮蔽＝死代码；G1-sleep-2 ⑦ 删除首个、在此生效
    定义上完成 ④，并由反例测试以 SourceGuard 钉住「只出现一次」。）
    """
    for _attempt in (1, 2):
        kind, xml = read_prefs_evidence()
        if kind == "ok" and 'name="running" value="false"' in xml:
            return
        set_stop_flags()
    raise RuntimeError(
        "prefs running 未置 false（残留会导致 boot 后 AutoTestTool 自启叠加——#894）"
    )


def stop_task(force: bool) -> None:
    """set_stop_flags → 优雅 STOP → force-stop 兜底（lib.ps1:Stop-SleepTestTask 同款）。

    PC wake-watchdog 不移植（G15 决策：OEM 闹钟丢失场景记已知缺口，patrol 兜底）。
    """
    set_stop_flags()
    _verify_stop_flags()
    if service_alive():
        adb_shell(
            f"am startservice -n {_PKG}/{_SERVICE} -a com.tinno.autotesttool.action.SLEEP_TEST_STOP",
            timeout=30,
        )
        time.sleep(3)
    adb_shell(f"am force-stop {_PKG}", timeout=30)
    time.sleep(1)
    if force and service_alive():
        raise RuntimeError("停止 SleepTestService 失败（优雅停止 + force-stop 均未生效）")


def result_paths() -> tuple[str, ...]:
    """主路径 + 旧路径兜底（设备端 getResultDir 回退同款）。"""
    return _RESULT_PATHS


# ---------------------------------------------------------------------------
# sleep_test_result.txt 解析（纯文本行，无 XML；join 键 = cycle 分子/分母）
# ---------------------------------------------------------------------------

def parse_sleep_result(content: bytes) -> dict:
    """解析 sleep_test_result.txt → 摘要 + entries。

    行格式（设备端 SleepTestService 追加写，时间戳前缀可带可无）：
      cycle N/M wake OK|FAIL screen=ON|OFF
      go sleep Xs screen=ON|OFF      （screen=ON = 灭屏失败异常）
      stopped by user
      finished result=PASS|FAIL      （整包结果取最后一行 finished）
    """
    entries = []
    wake_failures = 0
    sleep_anomalies = 0
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
            if cycle.group(3) == "wake FAIL":
                wake_failures += 1
            entries.append({"kind": "cycle", "cycle": n, "total": total, "status": cycle.group(3), "screen": cycle.group(4)})
            continue
        sleep_line = _SLEEP_RE.search(line)
        if sleep_line:
            if sleep_line.group(2) == "ON":
                sleep_anomalies += 1
            entries.append({"kind": "sleep", "seconds": int(sleep_line.group(1)), "screen": sleep_line.group(2)})
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
        "wake_failures": wake_failures,
        "sleep_anomalies": sleep_anomalies,
        "final_status": final_status,
        "entries": entries,
    }
