# -*- coding: utf-8 -*-
"""GPU 专项脚本共享库（gpu_setup / gpu_check / gpu_finish 共用）。

移植自 stability_GPU-Test（issue #462 P0c；G15 对齐见
docs/notes/feature/2026-08-31-toolkit-android-tools-g15-alignment.md §3.3）。

与 Sleep/PowerCycle 不同：toolkit 无 ps1/lib 三件套，编排全在 runAll----20260228.bat
+ 设备端 run_stress_gpu.sh。移植要点：
- RAM 分版：``ro.boot.ddrsize`` → ``/proc/meminfo`` 回退，``lite_max_gb``（默认 8）阈值，
  test_id 001（Antutu_v10）/ 002（Antutu_v10_Lite）——bat 的确定性逻辑直接移植；
- 每轮标记（G15 D1）：平台自产设备端循环脚本（``_gpu_stress_loop.sh``）逐轮
  ``am instrument -e loop 1`` + ``GPU_ROUND <n> rc=<rc>`` 标记行（原文 test_log.txt 备查）；
  ``-e loop 1`` × N 与 toolkit 的 ``-e loop N`` 语义等价性待真机冒烟验证；
- MTK 专属依赖（/proc/mtk_battery_cmd、com.debug.loggerui、aee_exp 清理）尽力而为，
  失败不阻断（平台 #220 生产只扫 MTK）；
- 结果：``/sdcard/Auto/test_log.txt``（instrument stdout + 标记行，无结构化格式）。

- 环境/参数/stdout 契约与 sleep/mtbf 三件套一致：
  ``STP_DEVICE_SERIAL`` / ``STP_STEP_PARAMS``（JSON）/ stdout 单行 JSON ``{"success": ...}``。
- 配置解析层级：STP_STEP_PARAMS > STP_GPU_* env >
  ``{STP_AEE_NFS_ROOT}/gpu/{project}/gpu_tool_config.ini``（可选，lite_max_gb 键）> 代码默认。
- 资源：``{gpu_resources_dir}/{project}/{variant}/`` 下 3 个 APK
  （variant = Antutu_v10 | Antutu_v10_Lite；默认 ``{agent}/resources/gpu/{project}/``）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# 设备端常量（manifest 实测包名）
_HOST_PKG = "com.transsion.testcaserepository"
_HOST_TEST_PKG = "com.transsion.testcaserepository.test"
_ANTUTU_FULL_PKG = "com.antutu.benchmark.full"
_ANTUTU_LITE_PKG = "com.antutu.benchmark.full.lite"
# 安装前卸载的旧包（bat 同款，含 MTBF 时代遗留）
_OLD_PACKAGES = (
    "com.tinno.reliabilityuiautomatortest",
    "com.tinno.reliabilityuiautomatortest.test",
    _HOST_PKG,
    _HOST_TEST_PKG,
)
# stop 阶段 force-stop 的目标（bat 注释：压测跑在 Antutu 进程内，只停框架不结束 Antutu）
_STOP_PACKAGES = (
    _HOST_PKG,
    _HOST_TEST_PKG,
    _ANTUTU_LITE_PKG,
    _ANTUTU_FULL_PKG,
)

_VARIANTS = {
    "Antutu_v10": {
        "test_id": "001",
        "antutu_pkg": _ANTUTU_FULL_PKG,
        "apks": ("antutu_benchmark_v10_3d.apk", "scripts-debug.apk", "scripts-debug-androidTest.apk"),
    },
    "Antutu_v10_Lite": {
        "test_id": "002",
        "antutu_pkg": _ANTUTU_LITE_PKG,
        "apks": ("Antutu_3D_Lite_10.2.9.apk", "scripts-debug.apk", "scripts-debug-androidTest.apk"),
    },
}
_DEFAULT_LITE_MAX_GB = 8
_DEFAULT_ROUNDS = 700   # 与 toolkit run_stress_gpu.sh 硬编码一致

_RESULT_LOG = "/sdcard/Auto/test_log.txt"
_DEVICE_SCRIPT = "/sdcard/Auto/gpu_stress_loop.sh"

# 标记行（平台自产设备端脚本写入；正则带行首锚定，rc 可为负——instrument 中止码如 -3）
_MARKER_START_RE = re.compile(r"^GPU_RUN_START test_id=(\d+) rounds=(\d+)$")
_MARKER_ROUND_RE = re.compile(r"^GPU_ROUND (\d+) rc=(-?\d+)$")
_MARKER_END_RE = re.compile(r"^GPU_RUN_END rc=(-?\d+)$")

# instrument/循环脚本进程匹配（v1.0.1：pgrep -f 全命令行匹配——Android `ps -A`
# 截断 args，进程只显示 app_process，匹配不到；bracket 技巧防 pgrep 匹配自身
# 命令行——adb shell 命令串里含 pattern 文本）
_INSTRUMENT_PGREP_PATTERN = "[g]pu_stress_loop|[T]estStressGpu|[A]ndroidJUnitRunner"


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


# ---------------------------------------------------------------------------
# 路径解析（G15 对齐 §4：与 Sleep/PowerCycle 统一）
# ---------------------------------------------------------------------------

def project_name(cfg: dict) -> str:
    return str(param_or_env(cfg, "project", "STP_GPU_PROJECT", "legacy"))


def suite_dir(project: str) -> Path:
    root = env("STP_AEE_NFS_ROOT", "")
    if not root:
        raise RuntimeError("STP_AEE_NFS_ROOT is not set")
    return Path(root) / "gpu" / project


def results_dir(project: str) -> Path:
    return suite_dir(project) / "results"


def _default_resources_root() -> Path:
    """默认 resources 根：相对 Agent 目录解析（aimonkey/mtbf/sleep 先例同构）。"""
    return Path(__file__).resolve().parents[3] / "resources" / "gpu"


def resources_dir(cfg: dict) -> Path:
    base = cfg.get("gpu_resources_dir") or env("STP_GPU_RESOURCES_DIR", str(_default_resources_root()))
    return Path(base) / project_name(cfg)


def parse_ini(content: str) -> dict:
    """gpu_tool_config.ini 解析：; 注释，key=value 去空格（与 properties 同规则）。"""
    cfg = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        match = re.match(r"^([^=]+)=(.*)$", line)
        if match:
            cfg[match.group(1).strip()] = match.group(2).strip()
    return cfg


def read_ini(project: str) -> dict:
    """可选配置层：{STP_AEE_NFS_ROOT}/gpu/{project}/gpu_tool_config.ini，缺失返回空。"""
    root = env("STP_AEE_NFS_ROOT", "")
    if not root:
        return {}
    path = Path(root) / "gpu" / project / "gpu_tool_config.ini"
    try:
        return parse_ini(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def gpu_config(cfg: dict) -> dict:
    """规范化配置（STP_STEP_PARAMS > STP_GPU_* env > ini > 代码默认）。"""
    project = project_name(cfg)
    ini = read_ini(project)

    def pick(key: str, env_key: str, ini_key: str | None, default):
        value = cfg.get(key)
        if value is not None and str(value) != "":
            return value
        raw = env(env_key, "")
        if raw != "":
            return raw
        if ini_key and ini.get(ini_key) not in (None, ""):
            return ini[ini_key]
        return default

    return {
        "project": project,
        "lite_max_gb": int(pick("lite_max_gb", "STP_GPU_LITE_MAX_GB", "lite_max_gb", _DEFAULT_LITE_MAX_GB)),
        "rounds": int(pick("rounds", "STP_GPU_ROUNDS", None, _DEFAULT_ROUNDS)),
        "install_apks": str(pick("install_apks", "STP_GPU_INSTALL_APKS", None, "true")).lower() == "true",
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
# RAM 分版（runAll----20260228.bat 的确定性逻辑直移）
# ---------------------------------------------------------------------------

def detect_ram_gb() -> float | None:
    """设备 RAM（GB）。``getprop ro.boot.ddrsize``（如 8G / 4096M）→
    ``/proc/meminfo MemTotal`` 回退（(kb+524288)//1048576，bat 同款取整）。"""
    out = adb_shell("getprop ro.boot.ddrsize", timeout=15).strip()
    match = re.match(r"^(\d+)\s*([GM])$", out, re.IGNORECASE)
    if match:
        value = int(match.group(1))
        return float(value) if match.group(2).upper() == "G" else round(value / 1024, 2)
    mem = adb_shell("cat /proc/meminfo", timeout=15)
    mm = re.search(r"MemTotal:\s*(\d+)\s*kB", mem)
    if mm:
        return float((int(mm.group(1)) + 524288) // 1048576)
    return None


def select_variant(ram_gb: float | None, lite_max_gb: int) -> tuple[str, dict]:
    """RAM <= lite_max_gb → Antutu_v10_Lite（test_id=002）；否则 Antutu_v10（test_id=001）。"""
    if ram_gb is None:
        raise RuntimeError("无法确定设备 RAM（ro.boot.ddrsize 与 /proc/meminfo 均不可读），无法分版")
    if ram_gb <= lite_max_gb:
        return "Antutu_v10_Lite", _VARIANTS["Antutu_v10_Lite"]
    return "Antutu_v10", _VARIANTS["Antutu_v10"]


# ---------------------------------------------------------------------------
# 设备交互（runAll----20260228.bat 各步移植；MTK 专属步骤尽力而为）
# ---------------------------------------------------------------------------

def is_root() -> bool:
    return adb_shell("id -u", timeout=15).strip() == "0"


def try_adb_root() -> bool:
    """尽力 adb root（清理 /data/*、写 proc 节点需要；bat 同款不阻断）。"""
    adb("root", timeout=15)
    for _ in range(3):
        time.sleep(1)
        if is_root():
            return True
    return False


def install_apks(apk_dir: Path, apks: tuple[str, ...]) -> dict:
    """卸载 4 个旧包 → install -r -g 三 APK（bat 同款），返回 sha256 清单。"""
    for pkg in _OLD_PACKAGES:
        adb("uninstall", pkg, timeout=60)   # 不存在时 rc!=0，忽略
    shas = {}
    for name in apks:
        apk = apk_dir / name
        if not apk.is_file():
            raise FileNotFoundError(f"APK 不存在: {apk}")
        shas[name] = sha256_file(apk)
        rc, out, _ = adb("install", "-r", "-g", str(apk), timeout=300)
        if rc != 0 or "Success" not in out:
            raise RuntimeError(f"安装失败: {name}: {out.strip() or 'rc=%d' % rc}")
    return shas


def prepare_device() -> None:
    """设备准备（bat 逐条移植；MTK 专属项失败不阻断）。

    - 通用：development_settings、/sdcard/Auto、WiFi 关、飞行模式开（Antutu 防上传保密）、
      常亮超时、锁屏关、解锁键、AEE 清理、appops on
    - MTK 专属：debug logger 广播、/proc/mtk_battery_cmd 节点、sys.audio.monkeycontrl
    """
    adb_shell("settings put global development_settings_enabled 1", timeout=30)
    adb_shell("mkdir -p /sdcard/Auto", timeout=30)
    adb_shell("cmd wifi set-wifi-enabled disabled", timeout=30)
    adb_shell("cmd connectivity airplane-mode enable", timeout=30)
    adb_shell("settings put system screen_off_timeout 1999999999", timeout=30)
    adb_shell("locksettings set-disabled true", timeout=30)
    for _ in range(3):
        adb_shell("input keyevent 82", timeout=30)
    if is_root():
        adb_shell("rm -rf /data/aee_exp/* /data/vendor/aee_exp/* /data/debuglogger/*", timeout=60)
    adb_shell("dumpsys activity appops on", timeout=30)
    # MTK debug logger（非 MTK 平台无此包，广播失败忽略）
    adb_shell(
        "am broadcast -a com.debug.loggerui.ADB_CMD "
        "-e cmd_name switch_taglog -e set_auto_start_1 -e set_total_log_size_10240 -e start "
        "--ei cmd_target 1 -e cmd_target 3",
        timeout=30,
    )
    # MTK 电池节点与音频开关（非 MTK 节点不存在，写失败忽略）
    adb_shell("echo 1 0 > /proc/mtk_battery_cmd/current_cmd", timeout=30)
    adb_shell("setprop sys.audio.monkeycontrl 1", timeout=30)


def push_device_script() -> None:
    """推送平台自产设备端循环脚本（LF；`_` 前缀辅助文件，scan 跳过）。"""
    local = Path(__file__).resolve().parent / "_gpu_stress_loop.sh"
    rc, _, err = adb("push", str(local), _DEVICE_SCRIPT, timeout=120)
    if rc != 0:
        raise RuntimeError(f"push gpu_stress_loop.sh 失败: {err.strip()}")
    adb_shell(f"chmod 755 {_DEVICE_SCRIPT}", timeout=30)


def launch_stress(rounds: int, test_id: str) -> None:
    """后台启动设备端循环脚本，stdout/stderr 全部进 test_log.txt（bat nohup 同款）。"""
    adb_shell(
        f"nohup sh {_DEVICE_SCRIPT} {rounds} {test_id} > {_RESULT_LOG} 2>&1 &",
        timeout=30,
    )


def instrument_alive() -> bool:
    """设备端循环脚本或 instrument 进程在跑（v1.0.1：pgrep -f 全命令行匹配）。

    冒烟发现 ④：`ps -A` 截断 args（进程只显示 app_process），grep 匹配不到；
    pgrep -f 按完整命令行匹配，bracket 技巧排除自身命令行（adb shell 命令串
    本身含 pattern 文本，不排除会恒真）。
    """
    out = adb_shell(f"pgrep -f '{_INSTRUMENT_PGREP_PATTERN}'", timeout=30)
    return bool(out.strip())


def stop_stress() -> None:
    """stop.bat 移植：force-stop 4 包 + pkill 循环脚本与 instrument。

    v1.0.1：pkill -f 同样有自匹配问题（shell 命令行含 pattern 会被杀，
    导致后续 force-stop 不执行）——bracket 技巧同款。
    """
    for pkg in _STOP_PACKAGES:
        adb_shell(f"am force-stop {pkg}", timeout=30)
    adb_shell(f"pkill -f '{_INSTRUMENT_PGREP_PATTERN}'", timeout=30)


def result_log_bytes() -> int:
    ls = adb_shell(f"ls -l {_RESULT_LOG}", timeout=30).strip()
    return parse_size_from_ls(ls)


def read_result_log() -> str:
    """拉取 test_log.txt 内容（文本；随 log 增长，直接读内容做标记解析）。"""
    _, out, _ = adb("shell", f"cat {_RESULT_LOG}", timeout=120)
    return out


# ---------------------------------------------------------------------------
# test_log.txt 标记解析（G15 D1：平台自产标记行，原文备查）
# ---------------------------------------------------------------------------

def parse_gpu_log(content: str) -> dict:
    """解析 test_log.txt → 摘要 + rounds。

    标记行（平台设备端脚本写入，行首锚定）：
      GPU_RUN_START test_id=<id> rounds=<N>
      GPU_ROUND <n> rc=<rc>            # 每轮 instrument 退出码（0 = 该轮成功）
      GPU_RUN_END rc=<rc>
    其余行（instrument 输出）原文忽略备查。
    """
    rounds: list[dict] = []
    start = None
    end_rc = None
    expected = 0
    for raw_line in content.splitlines():
        line = raw_line.strip()
        start_m = _MARKER_START_RE.match(line)
        if start_m:
            start = {"test_id": start_m.group(1)}
            expected = int(start_m.group(2))
            continue
        round_m = _MARKER_ROUND_RE.match(line)
        if round_m:
            rounds.append({"round": int(round_m.group(1)), "rc": int(round_m.group(2))})
            continue
        end_m = _MARKER_END_RE.match(line)
        if end_m:
            end_rc = int(end_m.group(1))
            continue
    rounds_done = rounds[-1]["round"] if rounds else 0
    failed = sum(1 for r in rounds if r["rc"] != 0)
    return {
        "started": start is not None,
        "test_id": start["test_id"] if start else None,
        "expected_rounds": expected,
        "rounds_done": rounds_done,
        "failed_rounds": failed,
        "end_rc": end_rc,
        "rounds": rounds,
    }
