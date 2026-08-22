"""Flash firmware via SP Flash Tool (MTK platform).

Environment:
    STP_NFS_ROOT         (prepended to relative firmware_dir; firmware 根默认
                          取 {STP_NFS_ROOT}/firmware)
    STP_STEP_PARAMS      (required JSON)
    STP_FLASH_TOOL_DIR   (optional override for flash_tool location)
    STP_JOB_ID           (used to tag metrics)
    STP_DEVICE_SERIAL    (fingerprint 路由与刷前/刷后版本比对用)
    STP_ADB_PATH         (default adb)

STP_STEP_PARAMS schema:
    firmware_dir            : str  (显式固件目录；缺省走指纹路由，见下)
    da_file                 : str  (显式 DA 文件；可由 manifest.json 提供)
    scatter_file            : str  (显式 scatter 文件；可由 manifest.json 提供)
    command                 : str  (optional, default firmware-upgrade)
    boot_mode               : str  (optional, default auto)
    timeout_seconds         : int  (optional, default 1200)
    flash_tool_dir          : str  (optional; overrides STP_FLASH_TOOL_DIR)
    reboot_to_flash         : bool (optional, default true; adb reboot before flash_tool)
    reboot_target           : str  (optional, default "bootloader"; passed to adb reboot)
    pre_reboot_wait_seconds : int  (optional, default 5; sleep after adb reboot)
    firmware_root           : str  (optional; env STP_FLASH_FIRMWARE_ROOT；
                          默认 {STP_NFS_ROOT}/firmware)
    version                 : str  (optional; env STP_FLASH_FIRMWARE_VERSION；
                          缺省读 {root}/{family}/latest.json)
    family                  : str  (optional; 显式指定机型族，缺省按指纹路由)
    skip_if_current         : bool (optional, default true; env STP_FLASH_SKIP_IF_CURRENT；
                          刷前 getprop 比对，已是目标版本则 skipped 收场)
    verify_version          : bool (optional, default true; env STP_FLASH_VERIFY_VERSION；
                          刷后回读版本核验，不一致判失败)
    verify_wait_seconds     : int  (optional, default 180; 刷后等设备回到 adb 的上限)

v1.2.0 相对 v1.1.0（Honor 刷机自动化 · 方向 A）：
  - **固件指纹路由**（ADR-0029 v2「执行差异归脚本路由」先例）：firmware_dir
    缺省时按 `getprop ro.product.model` 路由到
    `{firmware_root}/{family}/{version}/`，family 由 `_MODEL_FAMILY_ROUTES`
    映射（MLD_LX2/LX3→MLD，ELA_LX2/LX3→ELA），version 取
    `STP_FLASH_FIRMWARE_VERSION` env 或 `{family}/latest.json` 指针文件。
    路由决策（decided_by/model/family/version/manifest 版本）全部写进
    metrics.route，step_trace 可审计；机型不在路由表 → fail-fast。
  - **manifest.json 固件清单**：每版本目录一份（family/version/version_prop/
    scatter_file/da_file/models），路由模式必读，显式 firmware_dir 模式存在
    则用于补缺 da/scatter 与提供比对版本。
  - **刷前版本比对**：目标版本与 `getprop {version_prop}`（默认
    ro.build.version.incremental）相同 → `skipped:true` 不刷。adb 不可达
    （设备已在 BROM 等）不阻断，记录 version_check 后照刷。
  - **刷后版本核验**：刷完等设备回 adb（get-state == device），回读版本与
    manifest 不一致 → 失败（重试可由 PlanStep.retry 承接）。verify 关闭时
    保持 v1.1.0 语义：枚举慢只记录不判败。
  - 参数解析对齐 MTBF P0 先例：`STP_STEP_PARAMS > STP_FLASH_* env > 代码默认`
    （平台 default_params 恒空、逐计划参数通道不存在，ADR-0029 D1 挂起）。

v1.1.0 相对 v1.0.1：**flash 阶段打 PROGRESS 戳**（#115 阶段 2 / #134）。
停滞判据只认 PROGRESS 戳（普通输出不算活），flash 是长耗时且时长不可预估
的步骤，必须自己打戳：
  - adb reboot 进入 flash 模式 → 一戳
  - flash_tool 运行期：解析其 stdout/stderr，识别到新阶段关键字
    （DA handshake / download / format / erase / verify 等）→ seq+1 打戳；
    识别到百分比 → 打 percent 字段戳（粒度更细）
  - 阶段内无输出 = seq 不涨 = **诚实的停滞信号**——阶段静默容忍在
    PlanStep 上配大 stall_seconds（如 600s），不靠重复打印制造"活着"的假象
  - flash_tool 退出后轮询 adb devices 等设备重新枚举（每 5s 一戳），
    最后打 done 戳

注：flash_tool 输出格式以 SP Flash Tool 为准；解析不到任何阶段关键字时，
脚本只在 reboot / done 打戳，长静默阶段依赖 PlanStep 的 stall_seconds 兜底。
"""

import json
import os
import platform
import re
import subprocess
import sys
import threading
import time


_PASS_TOKENS = (
    "All command exec done",
    "All commands are executed successfully",
)
_FAIL_TOKENS = (
    "S_DA_HANDSHAKE_FAILED",
    "S_FT_DOWNLOAD_FAIL",
    "S_NOT_ENOUGH_STORAGE_SPACE",
    "S_FT_FORMAT_FAIL",
    "S_FT_GET_DEV_INFO_FAIL",
    "FAIL",
    "ERROR",
)

_LOCK_PATH = "/tmp/stp-flash-firmware.lock"

# ── v1.2.0 固件指纹路由 ─────────────────────────────────────────────
#
# getprop ro.product.model → 机型族（= firmware/ 下的一级目录）。
# 新机型接入 = 往这张表加一行 + NFS 放固件目录；表是权威源，
# 未列机型 fail-fast 而不是猜。ELA 先留路由项，固件包到位即可用。
_MODEL_FAMILY_ROUTES = {
    "MLD_LX2": "MLD",
    "MLD_LX3": "MLD",
    "ELA_LX2": "ELA",
    "ELA_LX3": "ELA",
}

# 每版本固件目录里的清单文件；latest.json 是族级版本指针
# （CIFS 上 symlink 不可靠，用指针文件）。
_MANIFEST_NAME = "manifest.json"
_LATEST_POINTER_NAME = "latest.json"
_DEFAULT_VERSION_PROP = "ro.build.version.incremental"

# ── PROGRESS 打戳（#115 阶段 2 / #134）──────────────────────────────
#
# 停滞判据只认 PROGRESS 戳（普通输出不算活）。flash 是长耗时且时长不可预估
# 的步骤，阶段推进时打戳；阶段内无输出 = seq 不涨 = 诚实的停滞信号 ——
# 阶段静默容忍在 PlanStep 上配大 stall_seconds（如 600s），不靠重复打印制造
# "活着"的假象。
#
# 阶段来源：解析 flash_tool 的 stdout/stderr（SP Flash Tool 输出），
# 识别到新阶段关键字 → seq+1 打戳；识别到百分比 → 附加 percent 字段打戳
# （粒度更细）。识别不到任何阶段输出时，只在 reboot 完成与设备重新枚举
# 时打戳。
_PHASE_TOKENS = (
    "S_DA_HANDSHAKE",
    "DOWNLOAD",
    "FORMAT",
    "ERASE",
    "VERIFY",
    "RECOVERY",
    "PRELOADER",
    "BOOTING",
)
_PROGRESS_PREFIX = "PROGRESS "


def _progress_stamp(seq: int, **fields) -> str:
    payload = {"seq": seq, "step": "flash", **fields}
    return _PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False)


def _emit_progress(seq: "list[int]", **fields) -> None:
    seq[0] += 1
    sys.stderr.write(_progress_stamp(seq[0], **fields) + "\n")
    sys.stderr.flush()


def _run_flash_tool_with_progress(
    cmd: list,
    cwd: str,
    env: dict,
    timeout: int,
    on_stage: "callable",
    on_percent: "callable",
) -> "tuple[str, int]":
    """Popen + 双 reader 线程跑 flash_tool，逐行喂给阶段/进度解析。

    不能用 subprocess.run(capture_output)：flash_tool 可能运行几十分钟，
    期间必须持续打戳让停滞判据满意；readline 阻塞问题用 reader 线程解决
    （与引擎 _pump_process 同构）。
    """
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    collected: list[str] = []
    seen_phases: set[str] = set()

    def _reader(stream) -> None:
        try:
            for line in stream:
                collected.append(line)
                upper = line.upper()
                matched = False
                for tok in _PHASE_TOKENS:
                    if tok in upper and tok not in seen_phases:
                        seen_phases.add(tok)
                        try:
                            on_stage(tok)
                        except Exception:
                            pass
                        matched = True
                        break
                if matched:
                    continue
                m = re.search(r"(\d{1,3})%", line)
                if m:
                    try:
                        on_percent(int(m.group(1)))
                    except Exception:
                        pass
        except (ValueError, OSError):
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    threads = [
        threading.Thread(target=_reader, args=(proc.stdout,), daemon=True),
        threading.Thread(target=_reader, args=(proc.stderr,), daemon=True),
    ]
    for th in threads:
        th.start()
    deadline = time.monotonic() + timeout
    try:
        while proc.poll() is None:
            if time.monotonic() >= deadline:
                try:
                    os.killpg(os.getpgid(proc.pid), 15)  # SIGTERM,杀整树
                except ProcessLookupError:
                    pass
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                proc.wait(timeout=10)
                raise subprocess.TimeoutExpired([str(cmd)], timeout)
            time.sleep(1)
        proc.wait(timeout=10)
    finally:
        for th in threads:
            th.join(timeout=2)
    return "".join(collected), proc.returncode


def _wait_device_back(
    serial: str, adb_path: str, timeout: int, on_tick: "callable",
) -> bool:
    """flash_tool 退出后轮询 adb devices，等设备重新枚举（打戳阶段）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            proc = subprocess.run(
                [adb_path, "devices"], capture_output=True, text=True, timeout=10,
            )
            if serial and serial in (proc.stdout or ""):
                return True
        except Exception:
            pass
        try:
            on_tick()
        except Exception:
            pass
        time.sleep(5)
    return False


_DEFAULT_REL_FLASH_TOOL = (
    "..", "..", "..", "resources", "flashtool",
    "SP_Flash_Tool_Selector_exe_Linux_v1.2444.00.100",
)


def _step_params() -> dict:
    raw = os.environ.get("STP_STEP_PARAMS", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _output(success: bool, **kwargs) -> None:
    payload = {"success": success, "skipped": False, **kwargs}
    print(json.dumps(payload, ensure_ascii=False))


def _resolve_under(root: str, candidate: str) -> str:
    if os.path.isabs(candidate):
        return candidate
    return os.path.normpath(os.path.join(root, candidate))


def _resolve_firmware_dir(rel: str) -> str:
    if os.path.isabs(rel):
        return rel
    nfs_root = os.environ.get("STP_NFS_ROOT", "")
    if nfs_root:
        return os.path.normpath(os.path.join(nfs_root, rel))
    return rel


def _locate_flash_tool_dir(params_override) -> str:
    if params_override:
        return params_override
    env_override = os.environ.get("STP_FLASH_TOOL_DIR", "")
    if env_override:
        return env_override
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(script_dir, *_DEFAULT_REL_FLASH_TOOL))


def _pick_flash_tool_exe(tool_dir: str):
    names = ("flash_tool", "flash_tool.exe")
    search_roots = [tool_dir, os.path.join(tool_dir, "SP_Flash_Tool_V5")]
    for root in search_roots:
        for name in names:
            path = os.path.join(root, name)
            if os.path.isfile(path):
                return path
    return None


def _scan_output_for_verdict(stdout: str, stderr: str):
    combined = "\n".join(s for s in (stdout, stderr) if s)
    upper = combined.upper()
    for tok in _FAIL_TOKENS:
        if tok.upper() in upper:
            return False, f"fail token hit: {tok}"
    for tok in _PASS_TOKENS:
        if tok in combined:
            return True, f"pass token hit: {tok}"
    return False, "no pass token found"


def _acquire_host_lock(on_wait_tick: "callable | None" = None):
    if platform.system() == "Windows":
        return None
    import fcntl

    lock_fd = open(_LOCK_PATH, "w")
    # **轮询式等待**（#142 review）：flock(LOCK_EX) 阻塞期间不打任何戳，
    # permit cap=5 下同一 host 多个设备进 flash 时，等待中的设备会被停滞钟
    # 误杀。改 LOCK_NB 轮询 + 每 5s 打一次 stage="lock-wait" 戳——等待本身
    # 也是可见的进度。
    waited = 0
    while True:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except (IOError, OSError):
            waited += 5
            if on_wait_tick is not None:
                try:
                    on_wait_tick(waited)
                except Exception:
                    pass
            time.sleep(5)
    return lock_fd


def _release_host_lock(lock_fd) -> None:
    if lock_fd is None:
        return
    try:
        lock_fd.close()
    except OSError:
        pass


def _build_subprocess_env(tool_dir: str) -> dict:
    """Mirror flash_tool.sh: prepend tool_dir and tool_dir/lib to LD_LIBRARY_PATH on Linux.

    Without this, flash_tool fails to dlopen libflashtool.so / libQt5Core.so under lib/.
    No-op on Windows (Qt DLLs resolved via PATH or co-located).
    """
    env = os.environ.copy()
    if platform.system() == "Windows":
        return env
    import posixpath
    lib_dir = posixpath.join(tool_dir, "lib")
    prefix = f"{tool_dir}:{lib_dir}"
    existing = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = f"{prefix}:{existing}" if existing else prefix
    return env


def _adb_device_state(serial: str, adb_path: str) -> str:
    """Probe ADB get-state; returns 'device', 'offline', 'unauthorized', 'no-device', or 'unknown'."""
    if not serial or not adb_path:
        return "no-device"
    try:
        proc = subprocess.run(
            [adb_path, "-s", serial, "get-state"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "unknown"
    if proc.returncode != 0:
        # adb returns non-zero when device isn't visible; stderr usually contains 'not found' / 'no device'
        return "no-device"
    return (proc.stdout or "").strip() or "unknown"


def _adb_getprop(prop: str, adb_path: str, serial: str, timeout: int = 10) -> "str | None":
    """读设备 prop；任何失败返回 None（调用方决定是否阻断）。"""
    if not serial or not adb_path:
        return None
    try:
        proc = subprocess.run(
            [adb_path, "-s", serial, "shell", "getprop", prop],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    value = (proc.stdout or "").strip()
    return value or None


def _reboot_into_flash_mode(serial: str, target: str, adb_path: str, wait_seconds: int) -> dict:
    """Best-effort: ask the device to reboot into <target> via ADB.

    Returns a metrics dict; never raises. flash_tool will USB-poll for the device
    regardless of this outcome — if the device is already in preloader/BROM, the
    adb call simply no-ops (no device visible) and flash_tool takes over.
    """
    result: dict = {"attempted": False, "target": target}
    if not serial:
        result["skip_reason"] = "STP_DEVICE_SERIAL not set"
        return result
    if not adb_path:
        result["skip_reason"] = "STP_ADB_PATH not set"
        return result

    pre_state = _adb_device_state(serial, adb_path)
    result["pre_state"] = pre_state
    # Only "device" means the ADB channel is fully usable. offline / unauthorized /
    # no-device / unknown all mean reboot via adb would either be rejected, hang the
    # 15s best-effort timeout, or produce confusing stderr — let flash_tool USB-poll instead.
    if pre_state != "device":
        result["skip_reason"] = f"device not ready for adb reboot (state={pre_state}); flash_tool will wait on USB"
        return result

    result["attempted"] = True
    try:
        proc = subprocess.run(
            [adb_path, "-s", serial, "reboot", target],
            capture_output=True, text=True, timeout=15,
        )
        result["exit_code"] = proc.returncode
        if proc.returncode != 0:
            result["stderr_tail"] = (proc.stderr or "")[-300:]
    except subprocess.TimeoutExpired:
        result["error"] = "adb reboot timed out after 15s"
    except FileNotFoundError as exc:
        result["error"] = f"adb not found: {exc}"
    except Exception as exc:
        result["error"] = f"adb reboot failed: {exc}"

    if wait_seconds > 0:
        time.sleep(wait_seconds)
        result["waited_seconds"] = wait_seconds
    return result


# ── v1.2.0：参数/env 解析 + 固件路由 ─────────────────────────────────


def _param_or_env(cfg: dict, key: str, env_key: str, default):
    """STP_STEP_PARAMS > STP_FLASH_* env > 代码默认（MTBF P0 先例同款）。

    平台 scan 注册的脚本 default_params 恒为空、逐计划参数通道不存在
    （ADR-0029 D1 挂起）；部署级配置经 hot-update 同步的 env 注入。
    """
    value = cfg.get(key)
    if value is not None and str(value) != "":
        return value
    raw = os.environ.get(env_key, "")
    if raw != "":
        return raw
    return default


def _as_bool(value, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _load_json_file(path: str):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _load_manifest(firmware_dir: str) -> "tuple[dict | None, str | None]":
    """读版本目录的 manifest.json；(manifest, error) 二选一。"""
    path = os.path.join(firmware_dir, _MANIFEST_NAME)
    if not os.path.isfile(path):
        return None, None
    data = _load_json_file(path)
    if data is None:
        return None, f"manifest.json is missing or malformed: {path}"
    scatter = data.get("scatter_file") or ""
    da = data.get("da_file") or ""
    if not scatter or not da:
        return None, (
            f"manifest.json must define scatter_file and da_file: {path}"
        )
    return data, None


def _resolve_route(args: dict) -> "tuple[dict | None, str | None]":
    """解析固件目标：显式 firmware_dir 优先，缺省按设备指纹路由。

    返回 (route, error)。route 字段：
      decided_by: "params"（显式 firmware_dir）| "fingerprint"（指纹路由）
      model / family / version / version_prop / firmware_dir / da_file /
      scatter_file / manifest_path
    version 为 None 表示无比对基准（显式目录且无 manifest）。
    """
    serial = os.environ.get("STP_DEVICE_SERIAL", "")
    adb_path = os.environ.get("STP_ADB_PATH", "adb")

    firmware_dir_raw = str(args.get("firmware_dir") or "").strip()
    if firmware_dir_raw:
        return _resolve_explicit_dir(args, firmware_dir_raw)
    return _resolve_by_fingerprint(args, serial, adb_path)


def _resolve_explicit_dir(args: dict, firmware_dir_raw: str) -> "tuple[dict | None, str | None]":
    firmware_dir = _resolve_firmware_dir(firmware_dir_raw)
    if not os.path.isdir(firmware_dir):
        return None, f"firmware_dir not found: {firmware_dir}"

    manifest, err = _load_manifest(firmware_dir)
    if err:
        return None, err

    da_raw = str(args.get("da_file") or "").strip()
    scatter_raw = str(args.get("scatter_file") or "").strip()
    da_name = da_raw or (manifest or {}).get("da_file") or ""
    scatter_name = scatter_raw or (manifest or {}).get("scatter_file") or ""
    if not da_name:
        return None, "da_file is required (param or manifest.json)"
    if not scatter_name:
        return None, "scatter_file is required (param or manifest.json)"

    da_file = _resolve_under(firmware_dir, da_name)
    scatter_file = _resolve_under(firmware_dir, scatter_name)
    version = (manifest or {}).get("version")
    version_prop = (manifest or {}).get("version_prop") or _DEFAULT_VERSION_PROP

    route = {
        "decided_by": "params",
        "model": None,
        "family": (manifest or {}).get("family"),
        "version": version,
        "version_prop": version_prop,
        "firmware_dir": firmware_dir,
        "da_file": da_file,
        "scatter_file": scatter_file,
        "manifest_path": (
            os.path.join(firmware_dir, _MANIFEST_NAME) if manifest else None
        ),
    }
    return route, None


def _resolve_by_fingerprint(
    args: dict, serial: str, adb_path: str,
) -> "tuple[dict | None, str | None]":
    model = _adb_getprop("ro.product.model", adb_path, serial)
    if not model:
        return None, (
            "fingerprint routing failed: cannot read ro.product.model via adb "
            "(device not reachable?); set firmware_dir explicitly or wait for "
            "the device to come back to Android"
        )

    family = str(args.get("family") or "").strip()
    if not family:
        family = _MODEL_FAMILY_ROUTES.get(model)
        if not family:
            known = ", ".join(sorted(_MODEL_FAMILY_ROUTES))
            return None, (
                f"no firmware family route for model {model}; "
                f"known models: {known}"
            )

    firmware_root = str(
        _param_or_env(args, "firmware_root", "STP_FLASH_FIRMWARE_ROOT", "")
    ).strip()
    if not firmware_root:
        nfs_root = os.environ.get("STP_NFS_ROOT", "").strip()
        if not nfs_root:
            return None, (
                "firmware root unknown: set STP_FLASH_FIRMWARE_ROOT or STP_NFS_ROOT"
            )
        firmware_root = os.path.join(nfs_root, "firmware")

    version = str(
        _param_or_env(args, "version", "STP_FLASH_FIRMWARE_VERSION", "")
    ).strip()
    if not version:
        pointer_path = os.path.join(firmware_root, family, _LATEST_POINTER_NAME)
        pointer = _load_json_file(pointer_path)
        version = str((pointer or {}).get("version") or "").strip()
        if not version:
            return None, (
                f"no target version: set STP_FLASH_FIRMWARE_VERSION or write "
                f'{{"version": "..."}} to {pointer_path}'
            )

    firmware_dir = os.path.join(firmware_root, family, version)
    if not os.path.isdir(firmware_dir):
        return None, f"firmware_dir not found: {firmware_dir}"

    manifest, err = _load_manifest(firmware_dir)
    if err:
        return None, err
    if manifest is None:
        return None, (
            f"fingerprint routing requires manifest.json in {firmware_dir}"
        )

    allowed_models = manifest.get("models")
    if isinstance(allowed_models, list) and allowed_models \
            and model not in [str(m) for m in allowed_models]:
        return None, (
            f"model {model} not in manifest models {allowed_models} "
            f"of {firmware_dir}"
        )

    manifest_version = str(manifest.get("version") or "")
    if manifest_version != version:
        return None, (
            f"manifest version {manifest_version} != resolved dir version "
            f"{version} under {firmware_dir}"
        )

    route = {
        "decided_by": "fingerprint",
        "model": model,
        "family": family,
        "version": manifest_version,
        "version_prop": manifest.get("version_prop") or _DEFAULT_VERSION_PROP,
        "firmware_dir": firmware_dir,
        "da_file": _resolve_under(firmware_dir, manifest["da_file"]),
        "scatter_file": _resolve_under(firmware_dir, manifest["scatter_file"]),
        "manifest_path": os.path.join(firmware_dir, _MANIFEST_NAME),
    }
    return route, None


def _precheck_version(route: dict, serial: str, adb_path: str) -> dict:
    """刷前版本比对。返回 {"skip": bool, ...}；adb 不可达不阻断。"""
    target = route.get("version")
    if not target:
        return {"checked": False, "reason": "no target version (no manifest)"}
    current = _adb_getprop(route.get("version_prop") or _DEFAULT_VERSION_PROP,
                           adb_path, serial)
    if current is None:
        return {
            "checked": False,
            "reason": "adb getprop unavailable; proceeding with flash",
            "target": target,
        }
    return {
        "checked": True,
        "current": current,
        "target": target,
        "skip": current == target,
    }


def _wait_device_ready(
    serial: str, adb_path: str, timeout: int, on_tick: "callable",
) -> bool:
    """等设备回到完全可用的 adb 状态（get-state == device）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _adb_device_state(serial, adb_path) == "device":
            return True
        try:
            on_tick()
        except Exception:
            pass
        time.sleep(5)
    return False


def _verify_after_flash(
    route: dict, serial: str, adb_path: str, wait_seconds: int,
    on_tick: "callable",
) -> "tuple[bool, dict]":
    """刷后核验：设备回 adb + 回读版本与 manifest 比对。"""
    report: dict = {"wait_seconds": wait_seconds}
    target = route.get("version")
    if not target:
        report["skipped_reason"] = "no target version (no manifest)"
        return True, report

    if not _wait_device_ready(serial, adb_path, wait_seconds, on_tick):
        report["error"] = (
            f"device did not become adb-ready within {wait_seconds}s after flash"
        )
        return False, report

    actual = _adb_getprop(
        route.get("version_prop") or _DEFAULT_VERSION_PROP, adb_path, serial,
    )
    report["current"] = actual
    report["target"] = target
    if actual is None:
        report["error"] = "post-flash version readback failed (getprop)"
        return False, report
    if actual != target:
        report["error"] = f"post-flash version mismatch: expected {target}, got {actual}"
        return False, report
    return True, report


def main() -> None:
    args = _step_params()
    serial = os.environ.get("STP_DEVICE_SERIAL", "")
    adb_path = os.environ.get("STP_ADB_PATH", "adb")

    # ── v1.2.0：固件目标解析（显式 firmware_dir / 指纹路由）────────
    route, route_error = _resolve_route(args)
    if route_error is not None or route is None:
        _output(False, error_message=route_error or "route resolution failed")
        return

    skip_if_current = _as_bool(
        _param_or_env(args, "skip_if_current", "STP_FLASH_SKIP_IF_CURRENT", ""),
        default=True,
    )
    verify_version = _as_bool(
        _param_or_env(args, "verify_version", "STP_FLASH_VERIFY_VERSION", ""),
        default=True,
    )

    started_at = time.time()
    # seq 必须先于锁等待定义：锁被占用时第一次 tick 就要打戳，
    # 定义晚了会 NameError(被 _acquire_host_lock 的 except 吞掉 → 不打戳)。
    seq: list[int] = [0]

    # ── v1.2.0：刷前版本比对（同版本 → skipped 收场）────────────────
    # 必须先于 da/scatter 与 flash_tool 校验：skipped 语义是"无事可做"，
    # 不应因刷机工具未部署（flashtool 二进制不进 git，CI/新 worktree 无）
    # 或包内个别文件缺失而失败——真要刷的设备才需要完整的包与工具。
    version_check = _precheck_version(route, serial, adb_path)
    _emit_progress(seq, stage="version-check", result=json.dumps(
        version_check, ensure_ascii=False))
    if skip_if_current and version_check.get("skip"):
        _output(True, skipped=True, metrics={
            "route": route,
            "version_check": version_check,
            "duration_seconds": round(time.time() - started_at, 2),
        })
        return

    da_file = route["da_file"]
    scatter_file = route["scatter_file"]
    firmware_dir = route["firmware_dir"]
    if not os.path.isfile(da_file):
        _output(False, error_message=f"da_file not found: {da_file}",
                metrics={"route": route, "version_check": version_check})
        return
    if not os.path.isfile(scatter_file):
        _output(False, error_message=f"scatter_file not found: {scatter_file}",
                metrics={"route": route, "version_check": version_check})
        return

    tool_dir = _locate_flash_tool_dir(args.get("flash_tool_dir"))
    if not os.path.isdir(tool_dir):
        _output(False, error_message=f"flash_tool_dir not found: {tool_dir}",
                metrics={"route": route, "version_check": version_check})
        return
    flash_tool_exe = _pick_flash_tool_exe(tool_dir)
    if not flash_tool_exe:
        _output(False, error_message=f"flash_tool executable not found under {tool_dir}",
                metrics={"route": route, "version_check": version_check})
        return

    command = args.get("command") or "firmware-upgrade"
    boot_mode = args.get("boot_mode") or "auto"
    try:
        timeout = int(args.get("timeout_seconds", 1200))
    except (TypeError, ValueError):
        timeout = 1200
    try:
        verify_wait = int(args.get("verify_wait_seconds", 180))
    except (TypeError, ValueError):
        verify_wait = 180

    cmd = [flash_tool_exe, "-c", command, "-d", da_file, "-s", scatter_file, "-b", boot_mode]
    subprocess_env = _build_subprocess_env(os.path.dirname(flash_tool_exe))

    try:
        lock_fd = _acquire_host_lock(
            on_wait_tick=lambda waited: _emit_progress(
                seq, stage="lock-wait", waited_seconds=waited,
            )
        )
    except OSError as exc:
        _output(False, error_message=f"lock setup failed: {exc}",
                metrics={"route": route, "version_check": version_check})
        return
    lock_acquired_at = time.time()

    # Best-effort: hand the device into flash mode via ADB before invoking flash_tool.
    # flash_tool itself USB-polls, so a failed reboot doesn't break the flow.
    pre_reboot: dict = {"attempted": False, "skip_reason": "disabled by params"}
    if bool(args.get("reboot_to_flash", True)):
        pre_reboot = _reboot_into_flash_mode(
            serial=serial,
            target=args.get("reboot_target") or "bootloader",
            adb_path=adb_path,
            wait_seconds=int(args.get("pre_reboot_wait_seconds", 5) or 0),
        )

    # reboot 进入 flash 模式本身是一个阶段（#134）
    if pre_reboot.get("attempted"):
        _emit_progress(seq, stage="reboot", target=args.get("reboot_target") or "bootloader")

    try:
        output, flash_rc = _run_flash_tool_with_progress(
            cmd,
            cwd=os.path.dirname(flash_tool_exe),
            env=subprocess_env,
            timeout=timeout,
            on_stage=lambda tok: _emit_progress(seq, stage=tok),
            on_percent=lambda pct: _emit_progress(seq, percent=pct),
        )
    except subprocess.TimeoutExpired:
        _output(False,
                error_message=f"flash_tool timed out after {timeout}s",
                metrics={"command_argv": cmd,
                         "route": route,
                         "version_check": version_check,
                         "pre_reboot": pre_reboot,
                         "duration_seconds": round(time.time() - started_at, 2)})
        return
    except FileNotFoundError as exc:
        _output(False,
                error_message=f"flash_tool not executable ({exc}); chmod +x or check libs",
                metrics={"command_argv": cmd, "route": route,
                         "version_check": version_check, "pre_reboot": pre_reboot})
        return
    except Exception as exc:
        _output(False,
                error_message=f"flash_tool launch failed: {exc}",
                metrics={"command_argv": cmd, "route": route,
                         "version_check": version_check, "pre_reboot": pre_reboot})
        return
    finally:
        _release_host_lock(lock_fd)

    # flash_tool 退出后：等设备重新枚举（最长 60s），期间打戳。
    # 设备没回来**不判失败**——flash 成功但枚举慢是常态，记录字段供诊断。
    reenumerated = _wait_device_back(
        serial=serial,
        adb_path=adb_path,
        timeout=60,
        on_tick=lambda: _emit_progress(seq, stage="re-enumerate"),
    )

    # ── v1.2.0：刷后版本核验（verify 开启时不一致/等不到设备 → 失败）──
    if verify_version:
        verify_ok, verify_report = _verify_after_flash(
            route, serial, adb_path, verify_wait,
            on_tick=lambda: _emit_progress(seq, stage="verify-wait"),
        )
        _emit_progress(seq, stage="verify", ok=verify_ok)
    else:
        verify_ok, verify_report = True, {"skipped_reason": "verify_version disabled"}
    _emit_progress(seq, stage="done")

    stdout = output
    stderr = ""
    duration = round(time.time() - started_at, 2)
    lock_wait = round(lock_acquired_at - started_at, 2)

    metrics = {
        "duration_seconds": duration,
        "lock_wait_seconds": lock_wait,
        "device_reenumerated": reenumerated,
        "exit_code": flash_rc,
        "command_argv": cmd,
        "da_file": da_file,
        "scatter_file": scatter_file,
        "firmware_dir": firmware_dir,
        "route": route,
        "version_check": version_check,
        "post_flash_verify": verify_report,
        "pre_reboot": pre_reboot,
        "stdout_tail": stdout[-1500:],
        "stderr_tail": stderr[-500:],
    }

    if flash_rc != 0:
        _output(False,
                error_message=f"flash_tool exited {flash_rc}: {(stderr or stdout)[:1500]}",
                metrics=metrics)
        return

    verdict_ok, evidence = _scan_output_for_verdict(stdout, stderr)
    if not verdict_ok:
        _output(False, error_message=f"verdict failed: {evidence}", metrics=metrics)
        return

    if not verify_ok:
        _output(False,
                error_message=f"post-flash verify failed: {verify_report.get('error')}",
                metrics=metrics)
        return

    metrics["verdict"] = evidence
    _output(True, metrics=metrics)


if __name__ == "__main__":
    main()
