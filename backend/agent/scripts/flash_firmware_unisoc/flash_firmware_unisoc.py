#!/usr/bin/env python3
"""Flash one Unisoc (SPRD) device to a firmware version prepared from a .pac.

v1.0.6（2026-10-21）：host 串行锁——与 MTK `flash_firmware` 共用
/tmp/stp-flash-firmware.lock（flock，O_NOFOLLOW）。展锐 fastboot 没有 flash_tool
那样的 USB 口绑定能力（SN→端口映射经 adb/lsusb 不可靠，Owner 已裁定同 host 并发
禁止），run 1043 实测两路 fastboot 并发即互抢枚举。加锁后单 host 内由锁天然串行、
多 host 并行安全，一次下发多设备 PlanRun 不再依赖外置编排。等待每 5s 打
stage=lock-wait 戳（stall 钟不误杀等待者，对齐 MTK #142 结论）；锁随进程退出由
内核释放，正常/异常路径都不需要显式解锁。

v1.0.5（2026-10-21）：`fastboot reboot` 按「幂等收尾」处理——写满 24 分区并
擦除后，reboot 命令在慢枚举 host 上要等设备从总线消失才返回，单调用超时
（1.0.0–1.0.4 的 20s）会把**已经写完**的刷写判成失败，批量重试就整盘重刷
（15.75 实测：12:37 super/wipe 全部落盘后 `timeout after 20s: ... reboot`）。
现在 reboot 超时先探一次设备是否还停在 fastboot：已离开总线＝reboot 已生效，
继续走 adb 回读核验；仍在 fastboot＝显式重试一次，再失败才致命。

v1.0.4（2026-10-21）：fastboot `getvar current-slot` 的探测改为**非致命**——
部分 host 上 fastboot 首次应答要 11–22s（172.21.15.75 实测，12+ 设备共用 USB
hub 枚举慢），1.0.0–1.0.3 的 10s 单调用超时会把「枚举慢」变成抛异常：
wait_fastboot/probe_device 的等待窗口形同虚设，run 1043/1044/1045 与 host 侧
CLI 恢复都因此在刷写开始前中断、设备停在 fastboot。现在单调用超时 30s，且
超时按「还没就绪」继续等，真实的预算由外层等待窗口决定（probe 的 fastboot
窗口同步放宽到 90s）。

v1.0.3（2026-10-19）：进度读取改为**字符流**。fastboot 的 "Sending sparse 'super'
k/77" 是用 \r 覆盖同一行的，按行读只在进程退出时才拿到——v1.0.2 在 15.75 实测
super 阶段 600s 内零打戳，仍会被 stall 误杀。改为 os.read 后按缓冲区最后一个
k/N 打戳。

v1.0.2（2026-10-19）：① fastboot 改为流式读取，按 "Sending sparse 'super' k/N"
逐片打 PROGRESS 戳——12.75 GiB 的 super 单分区要刷 7–10 分钟，只按分区打戳会被
步骤级 stall_seconds 误杀（run 1031 实测）。② 启动时若 adb 不可见但 fastboot 能看到
目标 serial，进入**恢复模式**：跳过 getprop/SPL 比对，用显式 family+version 或
firmware_dir 继续整包刷写，把上次中断在 fastboot 的设备救回来。

v1.0.1（2026-10-19）：刷机前先解析 fastboot 可执行文件（STP_FASTBOOT_PATH →
PATH → 常见安装路径），找不到就在**重启进 bootloader 之前**失败——否则设备会被
留在 fastboot 里、agent 又没有 fastboot 可救。v1.0.0 的刷写/擦除/核验语义不变。

v1.0.0（2026-10-18）：展锐（Unisoc ums9230 / ZTE P615F03）整包刷机首版。
形态对齐 MTK 的 flash_firmware：固件目录 = {NFS}/firmware/<family>/<version>，
manifest.json 描述版本，latest.json 按 ro.product.model 指针分流；脚本自身只做
「解析 -> 刷写 -> 擦除 -> 回读核验」，不写 DB、不发包。

Firmware layout（prepare_firmware.py 产出，一处版本一份，放 NFS 只读）::

    {firmware_root}/{family}/latest.json            {"version": "...",
                                                     "versions": {"<model>": "..."}}
    {firmware_root}/{family}/{version}/manifest.json
    {firmware_root}/{family}/{version}/images/plan.json
    {firmware_root}/{family}/{version}/images/raw/{super,cache,blackbox,prodnv}.img

plan.json 由 PAC 里的 scheme XML 生成：每条 PAC 文件 -> 目标分区列表；NV 类
（ProdNV / Persist / NV_LTE / DELTANV / UserData）标 preserve，整包刷机也不碰，
保证 IMEI/校准不被覆盖。

STP agent script contract:
    env  STP_DEVICE_SERIAL   target serial (required)
         STP_ADB_PATH        default "adb"
         STP_FASTBOOT_PATH   default "fastboot"
         STP_NFS_ROOT        firmware_root 缺省的 {STP_NFS_ROOT}/firmware
         STP_FLASH_FIRMWARE_ROOT / STP_FLASH_FIRMWARE_VERSION 可选覆盖
         STP_STEP_PARAMS     JSON params (see below)
    out  one JSON line on stdout: {success, skipped, metrics|error_message}
    log  "PROGRESS {json}" lines on stderr, one per stage/partition

Params:
    firmware_dir       explicit version dir（含 manifest.json + images/）
    family, version    alternative to firmware_dir：{root}/<family>/<version>
    flash_bootloader   default true   (uboot_a / sml_a / trustos_a / teecfg_a)
    flash_modem        default true   (modem / dsp / pm_sys / logo)
    flash_super        default true
    flash_spl          "auto" | "never" | "always"   default "auto"
                       auto = 读 eMMC boot0 前 N 字节与 PAC 镜像比对；不同才写，
                       写完回读 sha256 校验；写失败立即中止（不改 SPL 不会变砖）
    allow_spl_write    default true；显式 false 时 SPL 不同即 fail-closed
    wipe               default true   (erase userdata + metadata + misc)
    skip_if_current    default true；force=true 时强制重刷
    allow_downgrade    default false  （本流水线只升级；降级需显式放开）
    verify_version     default true   （刷后回读 version_prop，必须等于目标）
    verify_wait_seconds default 300   （重启后等 adb + boot_completed 的预算）

CLI（产线排障用，绕过 STP_STEP_PARAMS）::

    ./flash_firmware_unisoc.py --serial X --firmware-dir DIR [--dry-run] [--force]
"""


from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

_PROGRESS_PREFIX = "PROGRESS "
_MANIFEST = "manifest.json"
_SPL_PART = "splloader"
_FASTBOOT_BIN = ""
_FASTBOOT_CANDIDATES = (
    "/usr/bin/fastboot",
    "/usr/local/bin/fastboot",
    "/opt/android-sdk/platform-tools/fastboot",
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default


def _params() -> dict:
    raw = _env("STP_STEP_PARAMS", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _bool(value, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() not in ("0", "false", "no", "off")


class Progress:
    def __init__(self) -> None:
        self.seq = 0

    def stamp(self, **fields) -> None:
        self.seq += 1
        payload = {"seq": self.seq, "step": "flash_unisoc", **fields}
        sys.stderr.write(_PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stderr.flush()


def _output(success: bool, **kwargs) -> None:
    print(json.dumps({"success": success, "skipped": False, **kwargs}, ensure_ascii=False))
    sys.stdout.flush()


class Cmd:
    def __init__(self, exe: str, serial: str, progress: Progress, log: list) -> None:
        self.exe = exe
        self.serial = serial
        self.progress = progress
        self.log = log

    def run(self, argv: list, timeout: int = 60, check: bool = False) -> subprocess.CompletedProcess:
        cmd = [self.exe, "-s", self.serial] + argv
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"timeout after {timeout}s: {' '.join(cmd)}")
        except FileNotFoundError as exc:
            raise RuntimeError(f"{self.exe} not found: {exc}")
        self.log.append({"cmd": cmd, "rc": proc.returncode,
                         "out": (proc.stdout or "")[-400:], "err": (proc.stderr or "")[-400:]})
        if check and proc.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd)} -> rc={proc.returncode}: "
                               f"{(proc.stderr or proc.stdout or '').strip()[:300]}")
        return proc


def adb(serial: str, progress: Progress, log: list) -> Cmd:
    return Cmd(_env("STP_ADB_PATH", "adb"), serial, progress, log)


def fastboot(serial: str, progress: Progress, log: list) -> Cmd:
    return Cmd(_FASTBOOT_BIN or _env("STP_FASTBOOT_PATH", "fastboot"), serial, progress, log)


# Per-call budget for the readiness probe. 10s (1.0.0–1.0.3) is below the
# observed first-answer latency on slow-enumeration hosts (11–22s on
# 172.21.15.75), which turned "slow" into an exception instead of a retry.
_GETVAR_TIMEOUT = 30


def fastboot_getvar_ok(fb: Cmd) -> bool:
    """One non-fatal ``getvar current-slot`` probe: True only if it answered 0."""
    try:
        return fb.run(["getvar", "current-slot"], timeout=_GETVAR_TIMEOUT).returncode == 0
    except RuntimeError:
        return False


def reboot_out_of_fastboot(fb: Cmd, progress: Progress, log: list) -> None:
    """Leave fastboot for boot, tolerating hosts whose USB teardown outlasts
    the command budget: a reboot that timed out may still have landed (the
    device dropped off the bus). Probe once; retry the reboot only when the
    device is verifiably still resident. A second failure stays fatal."""
    try:
        fb.run(["reboot"], timeout=60)
        return
    except RuntimeError as exc:
        progress.stamp(stage="reboot_slow", note=str(exc)[:160])
    if fastboot_getvar_ok(fb):
        # still resident -> the reboot did not land; retry once, now fatal on error
        fb.run(["reboot"], timeout=60)
    else:
        log.append({"note": "reboot timed out but device left fastboot; assuming rebooted"})


def resolve_fastboot() -> str:
    """Locate fastboot *before* rebooting: a missing tool must not strand the unit."""
    explicit = _env("STP_FASTBOOT_PATH", "")
    if explicit:
        if os.path.isfile(explicit) and os.access(explicit, os.X_OK):
            return explicit
        raise RuntimeError(f"STP_FASTBOOT_PATH is not executable: {explicit}")
    found = shutil.which("fastboot")
    if found:
        return found
    for candidate in _FASTBOOT_CANDIDATES:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError(
        "fastboot not found on this agent host (PATH + "
        + ", ".join(_FASTBOOT_CANDIDATES)
        + "); install android-sdk-platform-tools before flashing")


# 与 MTK flash_firmware 同一把 host 锁（互斥跨两族：同 host 上 MTK 与展锐也不会
# 同时写 USB）。锁文件由内核在进程退出时自动释放。
_HOST_LOCK_PATH = "/tmp/stp-flash-firmware.lock"


def acquire_host_lock(progress: "Progress") -> int:
    """Blocking flock(LOCK_EX) with visible waiting (lock-wait stamps every 5s)."""
    import platform as _platform
    if _platform.system() != "Linux":
        return -1
    import fcntl
    fd = os.open(_HOST_LOCK_PATH, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    waited = 0
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            progress.stamp(stage="lock-held", waited_seconds=waited)
            return fd
        except OSError:
            waited += 5
            progress.stamp(stage="lock-wait", waited_seconds=waited)
            time.sleep(5)


# ── routing ────────────────────────────────────────────────────────────────

def _nfs(rel: str) -> str:
    if os.path.isabs(rel):
        return rel
    root = _env("STP_NFS_ROOT", "")
    return os.path.normpath(os.path.join(root, rel)) if root else rel


def _load_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# model -> firmware family (project name under {NFS}/firmware). Keep the same
# shape as the MTK script so both platforms publish under one tree.
_MODEL_FAMILY_ROUTES = {
    "Z2581": "A57",
    "P615F03": "A57",
}


def _read_latest_version(pointer: dict, model: str) -> str:
    """{"version": "..."} or {"versions": {"<model>": "..."}}.

    Accept both Z2581 and Z2581-GEN style keys, mirroring the MTK pointer reader.
    """
    if not isinstance(pointer, dict):
        return ""
    versions = pointer.get("versions")
    if isinstance(versions, dict):
        for key in (model, model.replace("-", "_"), model.replace("_", "-")):
            value = versions.get(key)
            if value:
                return str(value).strip()
        return ""
    return str(pointer.get("version") or "").strip()


def resolve_firmware(args: dict, serial: str, progress: Progress, log: list) -> tuple[str, dict]:
    explicit = str(args.get("firmware_dir") or "").strip()
    model = ""
    if explicit:
        fw_dir = _nfs(explicit)
    else:
        family = str(args.get("family") or "").strip()
        version = str(args.get("version") or _env("STP_FLASH_FIRMWARE_VERSION", "")).strip()
        model = device = ""
        if not family or not version:
            # 只有走指纹路由时才需要 adb；恢复模式（设备停在 fastboot）必须靠显式参数
            dev = adb(serial, progress, log)
            for _ in range(5):                      # adbd 刚起来时 getprop 可能空
                model = getprop(dev, "ro.product.model")
                device = getprop(dev, "ro.product.device")
                if model or device:
                    break
                time.sleep(3)
        if not family:
            family = _MODEL_FAMILY_ROUTES.get(model) or _MODEL_FAMILY_ROUTES.get(device) or ""
        if not family:
            raise RuntimeError(
                f"no firmware family route for model {model!r}/device {device!r}; "
                f"known models: {', '.join(sorted(_MODEL_FAMILY_ROUTES))}")
        root = _env("STP_FLASH_FIRMWARE_ROOT", "") or os.path.join(_env("STP_NFS_ROOT", ""), "firmware")
        if not version:
            pointer_path = os.path.join(root, family, "latest.json")
            pointer = _load_json(pointer_path) or {}
            version = _read_latest_version(pointer, model or device)
            if not version:
                raise RuntimeError(
                    f"no target version for model {model!r}: set params.version, or write "
                    f'{{"versions": {{"{model}": "..."}}}} to {pointer_path}')
        fw_dir = os.path.join(root, family, version)
    manifest = _load_json(os.path.join(fw_dir, _MANIFEST))
    if manifest is None:
        raise RuntimeError(f"manifest.json missing/malformed in {fw_dir}")

    dir_version = os.path.basename(os.path.normpath(fw_dir))
    if manifest.get("version") and manifest["version"] != dir_version:
        raise RuntimeError(f"manifest version {manifest['version']!r} != directory {dir_version!r}")
    allowed = manifest.get("models")
    if model and isinstance(allowed, list) and allowed and model not in [str(m) for m in allowed]:
        raise RuntimeError(f"model {model} not in manifest models {allowed} of {fw_dir}")
    return fw_dir, manifest


def load_plan(fw_dir: str, manifest: dict) -> list:
    rel = manifest.get("plan") or os.path.join("images", "plan.json")
    path = rel if os.path.isabs(rel) else os.path.join(fw_dir, rel)
    plan = _load_json(path)
    if not plan or "entries" not in plan:
        raise RuntimeError(f"plan missing: {path}")
    return plan["entries"]


# ── version helpers ────────────────────────────────────────────────────────

def version_key(text: str) -> tuple:
    """'GEN_NEEA_P615F03V1.0.0B19' -> (1,0,0,19); tolerant of other schemes."""
    if not text:
        return ()
    m = re.findall(r"\d+", text)
    return tuple(int(x) for x in m[-4:]) if m else ()


def getprop(cmd: Cmd, name: str) -> str:
    p = cmd.run(["shell", "getprop", name], timeout=15)
    return (p.stdout or "").strip()


# ── flashing primitives ────────────────────────────────────────────────────

def wait_adb(serial: str, progress: Progress, log: list, timeout: int) -> Cmd:
    """Wait until adb sees the device in 'device' state."""
    dev = adb(serial, progress, log)
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = dev.run(["get-state"], timeout=10)
        if (p.stdout or "").strip() == "device":
            return dev
        time.sleep(3)
    raise RuntimeError(f"adb device {serial} not available within {timeout}s")


def wait_fastboot(serial: str, progress: Progress, log: list, timeout: int) -> Cmd:
    fb = fastboot(serial, progress, log)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fastboot_getvar_ok(fb):
            return fb
        time.sleep(2)
    raise RuntimeError(f"fastboot device {serial} not available within {timeout}s")


def ensure_root(dev: Cmd, progress: Progress, log: list) -> str:
    """adb root + 等 adbd 回来；SPL 回读需要 root（boot0 是 root-only）。

    非 userdebug 机型 root 会失败，这里只记录 uid 不判死——后续 SPL 比对读不到
    时会走 fail-closed 分支，错误信息里能看到原因。
    """
    dev.run(["root"], timeout=20)
    time.sleep(2)
    for _ in range(10):
        q = dev.run(["get-state"], timeout=10)
        if (q.stdout or "").strip() == "device":
            break
        time.sleep(1)
    uid = (dev.run(["shell", "id", "-u"], timeout=10).stdout or "").strip()
    progress.stamp(stage="root", uid=uid or "?")
    return uid


def probe_device(serial: str, args: dict, progress: Progress, log: list):
    """Return ('adb', dev, None) or ('fastboot', None, fb).

    恢复模式：上一次刷写被中断后设备停在 fastboot，此时 adb 永远等不到。只要
    fastboot 能看到目标 serial，就继续整包刷写（路由需显式 family+version）。
    """
    dev = adb(serial, progress, log)
    deadline = time.time() + int(args.get("adb_probe_seconds", 60))
    while time.time() < deadline:
        if (dev.run(["get-state"], timeout=10).stdout or "").strip() == "device":
            return "adb", dev, None
        time.sleep(3)
    fb = fastboot(serial, progress, log)
    deadline = time.time() + 90
    while time.time() < deadline:
        if fastboot_getvar_ok(fb):
            return "fastboot", None, fb
        time.sleep(2)
    raise RuntimeError(f"device {serial} is neither adb-reachable nor visible to fastboot")


def boot_slot(serial: str, progress: Progress, log: list) -> str:
    fb = fastboot(serial, progress, log)
    p = fb.run(["getvar", "current-slot"], timeout=_GETVAR_TIMEOUT)
    text = (p.stdout or "") + (p.stderr or "")
    m = re.search(r"current-slot:\s*(\S+)", text)
    return m.group(1) if m else ""


def image_for(fw_dir: str, entry: dict) -> str:
    rel = entry.get("raw") or entry["file"]
    return rel if os.path.isabs(rel) else os.path.join(fw_dir, "images", rel)


_ORDER = [
    "uboot_a", "sml_a", "trustos_a", "teecfg_a",
    "l_modem_a", "l_ldsp_a", "l_gdsp_a", "l_agdsp_a", "pm_sys_a",
    "logo_a", "fbootlogo",
    "boot_a", "init_boot_a", "vendor_boot_a", "dtbo_a",
    "vbmeta_a", "vbmeta_system_a", "vbmeta_system_ext_a",
    "vbmeta_vendor_a", "vbmeta_product_a", "vbmeta_odm_a",
    "cache", "blackbox", "super",
]


def _rank(part: str) -> int:
    try:
        return _ORDER.index(part)
    except ValueError:
        return len(_ORDER)


def _flash_stream(part: str, path: str, serial: str, timeout: int,
                  progress: Progress, idx: int, total: int, log: list) -> None:
    """Run fastboot flash with line-streamed progress.

    super 是 12.75 GiB 的单个分区，fastboot 会打印
    "Sending sparse 'super' k/77 (…)"；按片打戳，步骤级 stall_seconds 才不会被
    7–10 分钟的无输出误杀。
    """
    exe = _FASTBOOT_BIN or _env("STP_FASTBOOT_PATH", "fastboot")
    cmd = [exe, "-s", serial, "flash", part, path]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    lines: list = []
    last = [0.0]

    def _reader() -> None:
        # fastboot 的进度是 "\rSending sparse 'super' k/77 ..."——没有换行，
        # 按行读要等进程结束才吐出来（v1.0.2 实测就是这样被 stall 误杀）。
        # 按字符流读，再从缓冲区里取最后一个 k/N。
        fd = proc.stdout.fileno()
        buf = ""
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            buf += chunk.decode("utf-8", "ignore")
            lines.append(buf[-200:])
            found = re.findall(r"(\d+)\s*/\s*(\d+)", buf)
            if found:
                cur, tot = found[-1]
                now = time.time()
                if now - last[0] >= 1.0:
                    last[0] = now
                    progress.stamp(stage="flash", partition=part, index=idx, total=total,
                                   chunk=f"{cur}/{tot}")
            if len(buf) > 16384:
                buf = buf[-4096:]

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError(f"fastboot flash {part} timed out after {timeout}s")
    reader.join(timeout=5)
    text = "".join(lines)
    log.append({"cmd": cmd, "rc": rc, "out": text[-400:], "err": ""})
    if rc != 0 or "FAILED" in text:
        raise RuntimeError(f"fastboot flash {part} failed: {text.strip()[:400]}")


def flash_partitions(fw_dir: str, entries: list, serial: str, args: dict,
                     progress: Progress, log: list) -> dict:
    flash_bl = _bool(args.get("flash_bootloader"), True)
    flash_modem = _bool(args.get("flash_modem"), True)
    flash_super = _bool(args.get("flash_super"), True)

    todo = []
    for e in entries:
        if e.get("preserve") or not e.get("partitions"):
            continue
        for part in e["partitions"]:
            if part == _SPL_PART:
                continue
            if not flash_bl and part in ("uboot_a", "sml_a", "trustos_a", "teecfg_a"):
                continue
            if not flash_modem and part in ("l_modem_a", "l_ldsp_a", "l_gdsp_a",
                                            "l_agdsp_a", "pm_sys_a", "logo_a", "fbootlogo"):
                continue
            if not flash_super and part == "super":
                continue
            todo.append((part, e))
    todo.sort(key=lambda x: _rank(x[0]))

    total = len(todo)
    t0 = time.time()
    for idx, (part, entry) in enumerate(todo, 1):
        path = image_for(fw_dir, entry)
        if not os.path.isfile(path):
            raise RuntimeError(f"image missing: {path}")
        progress.stamp(stage="flash", partition=part, index=idx, total=total,
                       size=os.path.getsize(path))
        _flash_stream(part, path, serial, int(args.get("timeout_seconds", 2400)),
                      progress, idx, total, log)
    return {"partitions": total, "flash_seconds": round(time.time() - t0, 1)}


def wipe(serial: str, progress: Progress, log: list, args: dict) -> dict:
    fb = fastboot(serial, progress, log)
    done = []
    for part in ("userdata", "metadata", "misc"):
        progress.stamp(stage="wipe", partition=part)
        p = fb.run(["erase", part], timeout=180)
        text = (p.stdout or "") + (p.stderr or "")
        ok = p.returncode == 0 and "FAILED" not in text
        done.append({"partition": part, "ok": ok, "detail": text.strip()[:120]})
        if not ok:
            raise RuntimeError(f"erase {part} failed: {text.strip()[:300]}")
    return {"erases": done}


def spl_decision(fw_dir: str, entries: list, serial: str, args: dict,
                 progress: Progress, log: list) -> dict:
    mode = str(args.get("flash_spl") or "auto").lower()
    if mode == "never":
        return {"mode": mode, "action": "skip"}
    dev = adb(serial, progress, log)
    storage = "emmc"
    cmdline = dev.run(["shell", "cat /proc/cmdline"], timeout=15).stdout or ""
    m = re.search(r"sprdboot.flash=(\w+)", cmdline)
    if m:
        storage = m.group(1).lower()
    want = "SPLLoaderUFS" if storage == "ufs" else "SPLLoaderEMMC"
    entry = next((e for e in entries if e["id"] == want), None) \
        or next((e for e in entries if _SPL_PART in (e.get("partitions") or [])), None)
    if entry is None:
        return {"mode": mode, "action": "no-image", "storage": storage}
    image = image_for(fw_dir, entry)
    if mode == "always":
        return {"mode": mode, "action": "write", "image": image, "storage": storage}
    # auto: compare boot0 head with the image, never write silently
    size = os.path.getsize(image)
    p = dev.run(["shell", f"dd if=/dev/block/by-name/spl_a bs={size} count=1 2>/dev/null | sha256sum"],
                timeout=30)
    remote = (p.stdout or "").split()[0] if p.returncode == 0 and (p.stdout or "").strip() else ""
    import hashlib
    h = hashlib.sha256()
    with open(image, "rb") as f:
        left = os.path.getsize(image)
        while left:
            buf = f.read(min(left, 1 << 20))
            h.update(buf)
            left -= len(buf)
    local = h.hexdigest()
    if remote and remote == local:
        return {"mode": mode, "action": "skip", "reason": "spl unchanged",
                "sha256": local, "storage": storage}
    return {"mode": mode, "action": "differ", "sha256": local, "device_sha256": remote,
            "image": image, "storage": storage}


def write_spl(image: str, serial: str, progress: Progress, log: list, args: dict) -> dict:
    """Write eMMC boot0 through adb dd and verify the readback.

    The write itself is what the vendor tool does over BSL; the risk is a power
    loss mid-write, so keep the image local, fsync, and compare afterwards.
    """
    if not _bool(args.get("allow_spl_write"), True):
        raise RuntimeError(
            "SPL differs from the PAC image and allow_spl_write=false; "
            "flashing eMMC boot0 can brick the unit - enable it deliberately")
    import hashlib
    dev = adb(serial, progress, log)
    size = os.path.getsize(image)
    remote = "/data/local/tmp/stp_spl.bin"
    progress.stamp(stage="spl", action="push", size=size)
    dev.run(["push", image, remote], timeout=120, check=True)
    dev.run(["shell", "echo 0 > /sys/block/mmcblk0boot0/force_ro"], timeout=15)
    proc = dev.run(["shell", f"dd if={remote} of=/dev/block/mmcblk0boot0 bs=4096 conv=fsync"],
                   timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"spl write failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    dev.run(["shell", "sync"], timeout=60)
    dev.run(["shell", "echo 1 > /sys/block/mmcblk0boot0/force_ro"], timeout=15)
    back = dev.run(["shell", f"dd if=/dev/block/mmcblk0boot0 bs={size} count=1 2>/dev/null | sha256sum"],
                   timeout=60)
    remote_hash = (back.stdout or "").split()[0] if back.returncode == 0 else ""
    h = hashlib.sha256()
    with open(image, "rb") as f:
        h.update(f.read())
    if remote_hash != h.hexdigest():
        raise RuntimeError(f"spl readback mismatch: {remote_hash!r} != {h.hexdigest()!r}")
    dev.run(["shell", f"rm -f {remote}"], timeout=15)
    progress.stamp(stage="spl", action="written", sha256=h.hexdigest()[:16])
    return {"action": "written", "image": os.path.basename(image), "sha256": h.hexdigest()[:16]}


def wait_boot(serial: str, progress: Progress, log: list, timeout: int) -> dict:
    dev = wait_adb(serial, progress, log, timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if getprop(dev, "sys.boot_completed") == "1":
            return {"boot_completed": True}
        time.sleep(3)
    return {"boot_completed": False}


# ── main flow ──────────────────────────────────────────────────────────────

def run(args: dict, serial: str, progress: Progress, log: list) -> dict:
    t0 = time.time()
    # 指纹路由要读 ro.product.model，所以先等设备回来再解析固件目录
    global _FASTBOOT_BIN
    _FASTBOOT_BIN = resolve_fastboot()
    progress.stamp(stage="preflight", fastboot=_FASTBOOT_BIN)
    mode, dev, fb0 = probe_device(serial, args, progress, log)
    progress.stamp(stage="mode", mode=mode)
    root_uid = ensure_root(dev, progress, log) if mode == "adb" else "n/a"
    fw_dir, manifest = resolve_firmware(args, serial, progress, log)
    entries = load_plan(fw_dir, manifest)
    progress.stamp(stage="route", firmware_dir=fw_dir, version=manifest.get("version"),
                   family=manifest.get("family"))

    cur_version = (getprop(dev, manifest.get("version_prop") or "ro.build.sw_internal_version")
                   if mode == "adb" else "")
    cur_date = getprop(dev, "ro.build.date.utc") if mode == "adb" else ""
    target = manifest.get("version") or ""
    progress.stamp(stage="precheck", current=cur_version, target=target, current_date=cur_date)

    if (not _bool(args.get("force"), False) and _bool(args.get("skip_if_current"), True)
            and cur_version and cur_version == target):
        return {"skipped": True, "reason": "already on target version",
                "metrics": {"current": cur_version, "target": target}}

    if (not _bool(args.get("allow_downgrade"), False)
            and version_key(cur_version) and version_key(target)
            and version_key(target) < version_key(cur_version)):
        raise RuntimeError(f"downgrade not allowed: {cur_version} -> {target} "
                           f"(set allow_downgrade=true to override)")

    lock_fd = -1
    if not _bool(args.get("dry_run"), False):
        # host 锁覆盖一切破坏性面：SPL boot0 写、进 bootloader、逐分区刷写。
        lock_fd = acquire_host_lock(progress)

    if mode == "adb":
        spl = spl_decision(fw_dir, entries, serial, args, progress, log)
    else:
        spl = {"mode": "auto", "action": "skipped",
               "reason": "recovery: device started in fastboot, boot0 not touched"}
    progress.stamp(stage="spl-check", **{k: v for k, v in spl.items() if k != "image"})

    if _bool(args.get("dry_run"), False):
        order = []
        for e in entries:
            if e.get("preserve") or not e.get("partitions"):
                continue
            for part in e["partitions"]:
                if part != _SPL_PART:
                    order.append((_rank(part), part, e["file"]))
        order.sort()
        return {"metrics": {"dry_run": True, "firmware_dir": fw_dir, "target": target,
                            "current": cur_version, "spl": spl,
                            "partitions": [{"partition": p_, "image": f_} for _, p_, f_ in order]}}

    if spl["action"] in ("differ", "write"):
        spl = write_spl(spl["image"], serial, progress, log, args)

    if mode == "adb":
        progress.stamp(stage="reboot-bootloader")
        dev.run(["reboot", "bootloader"], timeout=15)
        time.sleep(3)
        fb = wait_fastboot(serial, progress, log, 90)
    else:
        fb = fb0
    slot = boot_slot(serial, progress, log)
    progress.stamp(stage="fastboot", slot=slot)

    metrics = {"firmware_dir": fw_dir, "target": target, "previous": cur_version,
               "spl": spl, "root_uid": root_uid, "fastboot": _FASTBOOT_BIN,
               "host_lock_fd": lock_fd}
    metrics.update(flash_partitions(fw_dir, entries, serial, args, progress, log))
    if _bool(args.get("wipe"), True):
        metrics.update(wipe(serial, progress, log, args))

    progress.stamp(stage="reboot")
    reboot_out_of_fastboot(fb, progress, log)
    boot = wait_boot(serial, progress, log, int(args.get("verify_wait_seconds", 300)))
    metrics["boot"] = boot

    result = {"metrics": metrics}
    if _bool(args.get("verify_version"), True):
        dev = adb(serial, progress, log)
        now = getprop(dev, manifest.get("version_prop") or "ro.build.sw_internal_version")
        state = getprop(dev, "ro.boot.verifiedbootstate")
        setup = getprop(dev, "sys.boot_completed")
        user_setup = (dev.run(["shell", "settings", "get", "secure", "user_setup_complete"],
                              timeout=15).stdout or "").strip()
        metrics["verify"] = {"version": now, "verifiedbootstate": state, "boot_completed": setup,
                             "user_setup_complete": user_setup}
        if now != target:
            raise RuntimeError(f"post-flash version mismatch: {now!r} != {target!r}")
        if state and state != "green":
            raise RuntimeError(f"verified boot state is {state!r}, expected green")
    metrics["duration_seconds"] = round(time.time() - t0, 1)
    return result


def main() -> None:
    args = _params()
    serial = _env("STP_DEVICE_SERIAL", "")

    # CLI mode for bench debugging
    if len(sys.argv) > 1:
        i = 1
        while i < len(sys.argv):
            a = sys.argv[i]
            if a == "--serial":
                serial = sys.argv[i + 1]; i += 2
            elif a == "--firmware-dir":
                args["firmware_dir"] = sys.argv[i + 1]; i += 2
            elif a == "--no-wipe":
                args["wipe"] = False; i += 1
            elif a == "--dry-run":
                args["dry_run"] = True; i += 1
            elif a == "--force":
                args["force"] = True; i += 1
            else:
                print(f"unknown arg {a}", file=sys.stderr); sys.exit(2)

    if not serial:
        _output(False, error_message="STP_DEVICE_SERIAL is not set")
        return

    progress = Progress()
    log: list = []
    try:
        result = run(args, serial, progress, log)
        if result.get("skipped"):
            _output(True, skipped=True, metrics=result.get("metrics", {}),
                    error_message="")
        else:
            _output(True, metrics=result["metrics"])
    except Exception as exc:  # noqa: BLE001 - contract: always emit JSON
        _output(False, error_message=str(exc), metrics={"log_tail": log[-8:]})


if __name__ == "__main__":
    main()
