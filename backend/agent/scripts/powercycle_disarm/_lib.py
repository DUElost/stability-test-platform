# -*- coding: utf-8 -*-
"""powercycle_disarm 共享库（#3730 / #3723 §3.6）。

自 powercycle_finish/_lib.py 同构拷贝：prefs 根优先回读、set_stop_flags、
stop_task 等与 finish 1.0.x 解除路径一致。本族只做解除/探针，不做结果收取。
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


# §3.6.2（v1.7.3）：上线后每次 adb 调用结束打一次 PROGRESS；等待期不加戳。
_EXEC_PROGRESS = {"on": False, "n": 0}


def enable_exec_progress() -> None:
    """开启上线后执行期进度戳（disarm 等待成功 / probe 在线且开机完成后）。"""
    _EXEC_PROGRESS["on"] = True


def reset_exec_progress() -> None:
    """测试用：关闭并清零执行期进度计数。"""
    _EXEC_PROGRESS["on"] = False
    _EXEC_PROGRESS["n"] = 0


def exec_progress_tick() -> None:
    if _EXEC_PROGRESS["on"]:
        _EXEC_PROGRESS["n"] += 1
        progress_stamp({"phase": "disarm_exec", "adb_calls": _EXEC_PROGRESS["n"]})


# ---------------------------------------------------------------------------
# ADB 封装
# ---------------------------------------------------------------------------

def decode_device_output(raw: bytes | None) -> str:
    """设备 / adb 输出解码：**宽容 UTF-8**（v1.0.7，#3463 G1/F2；#3069 形态）。

    设备侧输出可能是任意字节。``text=True`` 按 locale 严格解码，遇到一个非
    UTF-8 字节就抛 ``UnicodeDecodeError``——它不是 ``OSError``，调用点无从兜住。
    坏字节替换成 U+FFFD 不影响签名匹配与后续判定。port 自 ``gpu_setup/_lib.py``
    v1.2.3 参照实现（#3175 同批收口）。
    """
    return (raw or b"").decode("utf-8", errors="replace")


def adb(*args: str, timeout: int = 60) -> tuple[int, str, str]:
    """adb -s <serial> <args...>，返回 (returncode, stdout, stderr)。"""
    cmd = [adb_path(), "-s", device_serial()] + list(args)
    try:
        # v1.0.7（#3463 G1/F2，#3069 形态）：显式收字节再解宽容码，不用
        # ``text=True``——后者严格解码，一个坏字节即 UnicodeDecodeError，且
        # 无法从调用点兜住。
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        exec_progress_tick()
        return -1, "", "timeout"
    exec_progress_tick()
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


def device_online(timeout: int = 15) -> bool:
    """adb get-state == device（reboot 周期中设备离线属正常，check 用）。

    ``timeout`` 受调用方 15s 进度周期与剩余截止约束（#3723 §3.6.2）。
    """
    rc, out, _ = adb("get-state", timeout=max(1, int(timeout)))
    return rc == 0 and out.strip() == "device"


# ---------------------------------------------------------------------------
# 路径解析（G15 对齐 §4：与 Sleep 统一「配置走中心存储、工具走 resources」）
# ---------------------------------------------------------------------------

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


def permission_model() -> str:
    """权限模型（§3.6.1.7 v1.7.3）：按 ``id -u`` 实际输出记 root/non-root/unknown。"""
    out = adb_shell("id -u", timeout=15).strip()
    if out == "0":
        return "root"
    if out.isdigit():
        return "non-root"
    return "unknown"


def autotesttool_apk_sha256(timeout: int = 30) -> str | None:
    """设备上 AutoTestTool APK 的 SHA-256；失败返回 None（保留诊断，不据此判通过）。"""
    budget = max(1, int(timeout))
    path_out = adb_shell(f"pm path {_PKG}", timeout=min(15, budget)).strip()
    apk_path = ""
    for line in path_out.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            apk_path = line.split(":", 1)[1].strip()
            break
    if not apk_path:
        return None
    digest_out = adb_shell(f"sha256sum {apk_path}", timeout=budget).strip()
    if not digest_out:
        return None
    token = digest_out.split()[0].strip().lower()
    if len(token) == 64 and all(c in "0123456789abcdef" for c in token):
        return token
    return None


def autotesttool_installed(timeout: int = 15) -> bool | None:
    """AutoTestTool 是否已安装（§3.6.6 第 2 条）：true / false / null。

    - rc=0 且至少一行以 package: 开头 → true；
    - rc=1 且无 package: 行 → false；
    - 其它（rc=0 但无 package 行、rc 其它、超时）→ null。

    pm path 的 rc 必须与输出一起看；仅看输出会把 rc=1 的瞬态失败误判成
    未安装。本函数只读，不写设备。
    """
    rc, out, _err = adb("shell", f"pm path {_PKG}", timeout=max(1, int(timeout)))
    has_package = any(
        line.strip().startswith("package:") for line in (out or "").splitlines()
    )
    if rc == 0 and has_package:
        return True
    if rc == 1 and not has_package:
        return False
    return None


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


_PREFS_ABSENT_SENTINEL = "__STP_PREFS_ABSENT__"


def _root_read_prefs() -> tuple[str, str]:
    """root 下一次探测同时给出**存在性与内容**（#2979），返回 ``(kind, text)``：

    - ``"ok"``       可读且非空——text 即 prefs XML；
    - ``"empty"``    存在 + 读取成功但内容为空——repair 判据**唯一**接受的损坏证据；
    - ``"absent"``   明确不存在（无可删）；
    - ``"denied"``   读取被拒（SELinux / 属主异常等，root 下本不应出现——按未知处理）；
    - ``"transient"`` adb 超时 / rc 不可归类（未知）。

    为什么必须同源（#2979 对 #2846 判据的收紧）：两次独立 adb 调用（cat 与
    test -f）在设备重启窗里可以「一败一成」——第一次 cat 撞瞬时失败（超时
    rc=-1 / 设备下线）被当成「读不到」，紧接着第二次 test -f 成功回 present
    ⇒ 删除健康 prefs，续跑 current_count 归零、auto_resume 断链。port 自
    ``powercycle_setup/_lib.py`` 参照实现（#3463 G1/F1）。
    """
    probe = (
        f"if [ ! -f {_PREFS_DIR}/{_PREFS_FILE} ]; then echo {_PREFS_ABSENT_SENTINEL}; "
        f"else cat {_PREFS_DIR}/{_PREFS_FILE} 2>&1; fi"
    )
    rc, out, err = adb("shell", probe, timeout=30)
    text = (out or "").strip()
    # v1.7.7：rc=0 且输出含 <map 根元素即为可读；不得再按字符串值里的
    # 错误字样改判（prefs 的某个 string 值可能就写着这些字样）。
    if rc == 0 and "<map" in text:
        return "ok", text
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
    return "transient", ""


def get_prefs_xml() -> str:
    """读 prefs（#2846/#2979）：**root 优先**——单调用同源探测 ``_root_read_prefs``；
    无 root 才回落 `run-as`。

    v1.0.8（#3463 §9 G1-pc-2 ①，F1「六族内所有 prefs 读路径」收口）：port
    powercycle_setup 1.2.3+ 同构形态。为什么不能只走 run-as：AutoTestTool 是
    platform 签名 system app（shared uid ``android.uid.system``），AOSP 对
    non-debuggable / shared-uid 包**恒拒绝 run-as**——run-as-only 读路径恒空，
    调用方（start/verify）会把「读空」当「不存在」跳过写入或误判写失败。
    读不到/读空返回空串；**空串只表示"此刻读不到"，不表示"文件不存在"**
    （存在性判定见 ``_root_read_prefs``）。
    """
    if is_root():
        kind, text = _root_read_prefs()
        return text if kind == "ok" else ""
    rc, out, _ = adb("shell", f"run-as {_PKG} cat shared_prefs/{_PREFS_FILE}", timeout=30)
    text = (out or "").strip()
    # v1.7.7：rc=0 且含 <map 即返回；prefs 的 string 值可能含错误字样。
    if rc == 0 and "<map" in text:
        return text
    return ""


def read_prefs_classified() -> tuple[str, str, bool | None]:
    """读取 prefs 并分类（§3.6.6 第 2 条）：(kind, xml, installed)。

    kind 取 ok / not_installed / absent / denied / transient。root 使用
    _root_read_prefs 的同源探测；非 root 只按 run-as 的 rc 与 stdout/stderr
    分类，**不使用 test -f**——/data/data 对 shell 不可遍历，其「不存在」
    是假信号。

    installed 取 autotesttool_installed() 的 true/false/null；两者矛盾时按
    未知（transient）处理，调用方不得据此写 prefs。
    """
    installed = autotesttool_installed()
    if is_root():
        kind, xml = _root_read_prefs()
        if kind == "absent" and installed is False:
            kind = "not_installed"
        if kind in ("ok", "denied") and installed is False:
            kind = "transient"
        return kind, xml if kind == "ok" else "", installed

    rc, out, err = adb(
        "shell",
        f"run-as {_PKG} cat shared_prefs/{_PREFS_FILE}",
        timeout=30,
    )
    text = (out or "").strip()
    # v1.7.7：先看返回码与 <map 根元素；错误字样只在 rc!=0 或没有
    # prefs 内容时用于分类。
    if rc == 0 and "<map" in text:
        kind = "ok"
    else:
        blob = (out or "") + "\n" + (err or "")
        if "unknown package" in blob:
            kind = "not_installed"
        elif "No such file or directory" in blob:
            kind = "absent"
        elif "Permission denied" in blob:
            kind = "denied"
        else:
            kind = "transient"

    if kind == "not_installed" and installed is True:
        kind = "transient"
    elif kind == "absent" and installed is False:
        kind = "not_installed"
    elif kind in ("ok", "denied") and installed is False:
        kind = "transient"
    return kind, text if kind == "ok" else "", installed


def repair_prefs_ownership() -> None:
    """prefs 读不到时按**同源可读性证据**决定删旧重建（#2846，#2979 收紧）。

    v1.0.7（#3463 G1/F1，#3088）port 自 powercycle_setup v1.2.3 判据（对照旧拷贝的
    「run-as 读空 + 可 root ⇒ rm -f」——AutoTestTool 是 platform 签名 system app
    （shared uid ``android.uid.system``），AOSP 对 non-debuggable / shared-uid 包
    **恒拒绝 run-as**，旧判据因此每轮都删健康 prefs）：
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
    try:
        result = subprocess.run(cmd, input=content.encode("utf-8"), capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        exec_progress_tick()
        raise
    exec_progress_tick()
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
    """deploy/run 语义合并：repair → 读 current_count（reset_count=false 续跑）→ 整写，返回 current_count。

    v1.0.8（#3463 §9 G1-pc-2 ②，F1 同形态；empty 语义修订见 #3463 规划者裁定）：
    ``reset_count=false`` 的续跑计数读取按 ``_root_read_prefs`` 的 kind 区分——
    ``ok`` 解析 current_count；root 下 ``empty`` 不是「读不到」而是**已确定的损坏**
    （rc=0、文件存在、cat 成功、内容为空），由 repair 先删（empty 是其唯一接受的
    删除证据），本次探测即落 ``absent`` → 取 0 整写 fresh 完整 prefs（损坏文件没有
    可保留的计数，与 ``set_stop_flags`` 的既有 G1 语义一致）；``transient``/
    ``denied`` raise 且不写——F1 防的是「未知当作不存在」，**不得在读不到时以 0
    整写完整 prefs**（重启窗 adb 超时可把健康续跑计数覆盖为 0）。非 root 下
    run-as 读空（不可修复）同样不作 absent 证据，raise 暴露可重试失败。
    """
    repair_prefs_ownership()
    current_count = 0
    if not cfg["reset_count"]:
        if is_root():
            kind, existing = _root_read_prefs()
        else:
            existing = get_prefs_xml()
            kind = "ok" if existing else "unknown-non-root"
        if kind == "ok":
            match = re.search(r'name="current_count" value="(\d+)"', existing)
            if match:
                current_count = int(match.group(1))
        elif kind != "absent":
            raise RuntimeError(
                f"prefs 读取未知（kind={kind}）：不得以 current_count=0 整写覆盖续跑计数，请重试本步骤"
            )
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


class StopNotConfirmed(RuntimeError):
    """服务未确认停止（#3723 §3.6.6 第 1 条 v1.7.8）：携带各步返回码。"""

    def __init__(self, steps: dict[str, Any]):
        self.steps = dict(steps)
        super().__init__(
            "停止 PowerCycleService 失败：不写停止标志（%s）"
            % ", ".join(f"{k}={v}" for k, v in self.steps.items())
        )


def service_state(timeout: int = 30) -> tuple[str, int]:
    """服务状态三态 + 返回码（#3723 §3.6.6 第 1 条 v1.7.8）。

    返回 ``(alive|stopped|unknown, rc)``。``rc != 0``（含 adb 超时 -1）一律
    ``unknown``——查询失败不能当作「已停」，否则服务仍在运行时会照样写停止
    标志并报执行完成。
    """
    rc, out, _err = adb(
        "shell", f"dumpsys activity services {_PKG}", timeout=max(1, int(timeout))
    )
    if rc != 0:
        return "unknown", rc
    return ("alive" if "PowerCycleService" in (out or "") else "stopped"), rc


def start_task() -> None:
    """force-stop → running=true → Activity → 前台服务(START) → KEEPALIVE 广播（lib.ps1:Start-PowerCycleTask 同款）。

    先拉起 Activity 再起前台服务：部分机型（Z2582）后台 start-foreground-service 会被
    AutoLaunch 拦截（lib.ps1 注释同款）。

    v1.0.8（#3463 §9 G1-pc-2 ③，F1 同形态）：prefs 不可读（非 ``ok``）时
    raise——不得跳过 ``running=true`` 却照常启动服务（残留 stop flags 会让服务
    带着旧状态启动、后续 reboot 不再自动续跑）。
    """
    adb_shell(f"am force-stop {_PKG}", timeout=30)
    if is_root():
        kind, xml = _root_read_prefs()
        if kind != "ok":
            raise RuntimeError(
                f"prefs 读取未知（kind={kind}）：不启动任务（running=true 未写入），请重试本步骤"
            )
    else:
        xml = get_prefs_xml()
        if not xml:
            raise RuntimeError(
                "prefs run-as 读空（读空≠文件不存在）：不启动任务（running=true 未写入），请重试本步骤"
            )
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
    """auto_resume=false + running=false（lib.ps1:Set-PowerCycleStopFlags 同款）。

    v1.0.7（#3463 G1/F1 收紧，#3088）：整写最小 map 只认「文件明确不存在」
    （``absent``）。旧判据把 ``get_prefs_xml()==""`` 当「文件不存在」——而读空
    同样来自 run-as 被拒 / adb 瞬时失败，把健康 prefs 降成只有两个标志的最小图
    （current_count / test_times / mode 全丢，auto_resume 断链）。root 下用
    ``_root_read_prefs`` 的单调用同源证据分类：``transient``/``denied``（含
    repair 后仍 ``empty``）保留文件并 raise——步骤转红可重试，绝不整写；非 root
    下 run-as 读空同样不作 absent 证据，raise 暴露可重试失败。
    """
    repair_prefs_ownership()
    if is_root():
        kind, xml = _root_read_prefs()
        if kind == "absent":
            xml = ""
        elif kind != "ok":
            raise RuntimeError(
                f"prefs 读取未知（kind={kind}）：保留现有文件、不整写最小 map，请重试本步骤"
            )
    else:
        xml = get_prefs_xml()
        if not xml:
            raise RuntimeError(
                "prefs run-as 读空（读空≠文件不存在）：保留现有文件、不整写最小 map，请重试本步骤"
            )
    if xml:
        content = update_prefs_field(update_prefs_field(xml, "auto_resume", "false", "boolean"), "running", "false", "boolean")
    else:
        content = (
            "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n"
            "<map>\n"
            '    <boolean name="auto_resume" value="false"/>\n'
            '    <boolean name="running" value="false"/>\n'
            "</map>\n"
        )
    push_prefs_xml(content)


def remove_empty_prefs() -> None:
    """root 空文件是 #2979 的唯一确定性损坏：删除后重建最小 prefs。"""
    if not is_root():
        raise RuntimeError("非 root 空文件不整写")
    rc, _out, err = adb(
        "shell", f"rm -f {_PREFS_DIR}/{_PREFS_FILE}", timeout=30
    )
    if rc != 0:
        raise RuntimeError(
            "删除空 prefs 失败: " + ((err or "").strip() or "rc=%d" % rc)
        )


def build_minimal_stop_prefs() -> str:
    """最小停止 prefs（§3.6.6 第 5 条）：只含 running=false 与 auto_resume=false。"""
    return (
        "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n"
        "<map>\n"
        '    <boolean name="auto_resume" value="false"/>\n'
        '    <boolean name="running" value="false"/>\n'
        "</map>\n"
    )


def write_minimal_stop_flags() -> None:
    """写最小停止 prefs；非 root 先建 shared_prefs 目录（§3.6.6 第 5 条）。"""
    if not is_root():
        rc, _out, err = adb(
            "shell", f"run-as {_PKG} mkdir -p shared_prefs", timeout=30
        )
        if rc != 0:
            raise RuntimeError(
                "run-as 建 shared_prefs 目录失败: "
                + ((err or "").strip() or "rc=%d" % rc)
            )
    push_prefs_xml(build_minimal_stop_prefs())


def stop_service(force: bool) -> dict[str, Any]:
    """先 STOP、再 force-stop，按返回码确认服务已停（§3.6.6 第 1 条 v1.7.8）。

    - 状态查询：``rc != 0`` 即 ``unknown``；``unknown`` 时不发 STOP。
    - 服务存活时，STOP 与 force-stop 的返回码都必须为 0。
    - 最后一次状态查询必须 ``rc == 0`` 且无服务记录，才算确认停止。
    任一条件不满足 raise ``StopNotConfirmed``；调用方不得写 prefs。
    """
    del force  # 兼容旧签名；停止语义不随参数放宽（v1.7.8）
    steps: dict[str, Any] = {}
    state_before, rc_before = service_state()
    steps["state_before"] = state_before
    steps["state_before_rc"] = rc_before
    if state_before == "alive":
        rc, _out, _err = adb(
            "shell",
            f"am startservice -n {_PKG}/{_SERVICE} -a "
            "com.tinno.autotesttool.action.POWER_CYCLE_STOP",
            timeout=30,
        )
        steps["stop_rc"] = rc
        time.sleep(3)
    rc, _out, _err = adb("shell", f"am force-stop {_PKG}", timeout=30)
    steps["force_stop_rc"] = rc
    time.sleep(1)
    state_after, rc_after = service_state()
    steps["state_after"] = state_after
    steps["state_after_rc"] = rc_after
    if (
        steps.get("stop_rc", 0) != 0
        or steps["force_stop_rc"] != 0
        or rc_after != 0
        or state_after != "stopped"
    ):
        raise StopNotConfirmed(steps)
    return steps


def _verify_stop_flags(kind: str = "ok") -> None:
    """写后回读：running 与 auto_resume 必须同时为 false（§3.6.6 第 1 条）。

    不一致时按同一 kind 重写一次并再验证；仍不一致 raise。kind=absent
    走最小 prefs 写入，其余走 set_stop_flags。
    """
    for _attempt in (1, 2):
        xml = get_prefs_xml()
        if (
            'name="running" value="false"' in xml
            and 'name="auto_resume" value="false"' in xml
        ):
            return
        if kind in ("absent", "empty"):
            write_minimal_stop_flags()
        else:
            set_stop_flags()
    raise RuntimeError(
        "prefs running/auto_resume 未同时置 false（残留会导致 boot 后 "
        "AutoTestTool 自启叠加——#894 / #3723 §3.6.6）"
    )


def stop_task(force: bool, kind: str = "ok", installed: bool | None = True) -> dict[str, Any]:
    """停服务 → 写停止标志 → 同时验证两项（§3.6.6 第 1、5 条）。

    顺序不可交换：AutoTestTool 收到 STOP 时会把 auto_resume 改回 true；
    只有先 STOP + force-stop、按返回码确认服务已停后再写，写入值才是最终
    状态。``StopNotConfirmed`` 在任何写入之前抛出，调用方据此报 failed。
    kind 为 not_installed 时由调用方直接跳过；denied/transient 不写。
    """
    steps = stop_service(force)
    if kind == "not_installed":
        return steps
    if kind in ("denied", "transient"):
        raise RuntimeError("prefs 读取被拒/未知：不写停止标志")
    if kind not in ("ok", "absent", "empty"):
        raise RuntimeError(f"未知的 prefs 状态 {kind!r}：不写停止标志")
    if installed is not True:
        raise RuntimeError("无法确认 AutoTestTool 已安装：不写停止标志")
    if kind == "empty":
        remove_empty_prefs()
        write_minimal_stop_flags()
    elif kind == "absent":
        write_minimal_stop_flags()
    else:
        set_stop_flags()
    _verify_stop_flags(kind)
    return steps


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
