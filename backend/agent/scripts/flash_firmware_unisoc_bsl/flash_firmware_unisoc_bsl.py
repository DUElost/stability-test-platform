#!/usr/bin/env python3
"""Flash one Unisoc (SPRD) device via BSL/FDL2 (spd_dump) using Scheme 2 silent direct boot.

Implements the validated P532AA Scheme 2 flow inside the STP agent script contract:
1. Resolve firmware images directory (explicit firmware_dir or family/version under NFS root).
2. Pre-warm local SSD cache (/var/tmp/stp_flash/fw_cache) and dynamically trim
   trailing CHUNK_DONT_CARE blocks from super.img to stay well under the ~300s BSL watchdog.
3. Acquire host-wide serialization lock (/tmp/stp-flash-firmware.lock, shared with
   flash_firmware and flash_firmware_unisoc), emitting stage=lock-wait PROGRESS stamps
   every 5s so queued devices are not killed by step stall detection.
4. Switch device to BSL mode via `adb reboot autodloader` and execute `spd_dump`:
   - FDL1 @ 0x65008000 + EDK2 FDL2 @ 0xBDFFFE00 (keep_charge=1, blk_size=0x8000)
   - Erase metadata, sysdumpdb, misc, sd_klog, uboot_log (clears crash marks & BCB)
   - Write 4KB userdata.img (triggers silent Android Vold format on first boot)
   - Flash all A-slot & common partitions + trimmed super.img, never touching NV/calibration
   - Issue reset
5. Observe direct cold boot to sys.boot_completed == "1" and verify build/root.

STP agent script contract:
    env  STP_DEVICE_SERIAL          target serial (required)
         STP_ADB_PATH               default "adb"
         STP_SPD_DUMP_PATH          default "/usr/local/bin/spd_dump" (or PATH)
         STP_NFS_ROOT               default "/mnt/stp-aee"
         STP_FLASH_FIRMWARE_ROOT    optional override for {STP_NFS_ROOT}/firmware
         STP_FLASH_FIRMWARE_VERSION optional override for version
         STP_STEP_PARAMS            JSON params
    out  one JSON line on stdout: {success, skipped, metrics|error_message}
    log  "PROGRESS {json}" lines on stderr
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from typing import List, Optional, Tuple

_PROGRESS_PREFIX = "PROGRESS "
_HOST_LOCK_PATH = "/tmp/stp-flash-firmware.lock"
_DEFAULT_CACHE_DIR = "/var/tmp/stp_flash"
_DEFAULT_NFS_ROOT = "/mnt/stp-aee"
_EXPECTED_SUPER_SIZE = 6291456000  # 1536000 * 4096

ERASE_PARTITIONS: Tuple[str, ...] = (
    "metadata",
    "sysdumpdb",
    "misc",
    "sd_klog",
    "uboot_log",
)

PROTECTED_NV_PARTITIONS = frozenset({
    "prodnv",
    "calinv",
    "miscdata",
    "persist",
    "splloader",
    "nr_factorynv",
    "nr_runtimenv1",
    "nr_runtimenv2",
})

SMALL_FW_FILES: Tuple[str, ...] = (
    "fdl1-sign.bin",
    "edk2-fdl2-sign.bin",
    "userdata.img",
    "edk2-sign.bin",
    "sml-sign.bin",
    "tos-sign.bin",
    "teecfg-sign.bin",
    "SC9600_QogirN5_PS_modem.bin",
    "SC9600_QogirN5_PHY_modem.bin",
    "ums9632_n5_adsp.bin",
    "QogirN5_CH.bin",
    "QogirN5_SP.bin",
    "logo.bin",
    "boot.img",
    "init_boot.img",
    "vendor_boot.img",
    "dtbo.img",
    "vbmeta_system.img",
    "vbmeta_system_ext.img",
    "vbmeta_vendor.img",
    "vbmeta_product.img",
    "vbmeta_odm.img",
    "vbmeta-sign.img",
)

RAW_FW_FILES: Tuple[str, ...] = ("cache.img", "blackbox.img")

_LOAD_PART_RE = re.compile(r"load_partition:\s*([^,\s]+)")


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _params() -> dict:
    raw = _env("STP_STEP_PARAMS", "{}")
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
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
        payload = {"seq": self.seq, "step": "flash_unisoc_bsl", **fields}
        sys.stderr.write(_PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stderr.flush()


def _output(success: bool, **kwargs) -> None:
    payload = {"success": success, "skipped": False, **kwargs}
    print(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def is_protected_partition(part: str) -> bool:
    return (
        part in PROTECTED_NV_PARTITIONS
        or part.startswith("nr_downloadnv_")
        or part.startswith("nr_deltanv_")
    )


def resolve_adb() -> str:
    return _env("STP_ADB_PATH", "adb") or "adb"


def resolve_spd_dump() -> str:
    candidates = [
        _env("STP_SPD_DUMP_PATH", ""),
        shutil.which("spd_dump") or "",
        "/usr/local/bin/spd_dump",
        "/usr/bin/spd_dump",
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    raise RuntimeError("spd_dump executable not found (checked STP_SPD_DUMP_PATH, PATH, /usr/local/bin/spd_dump)")


def run_adb(adb_bin: str, serial: str, args: List[str], timeout: int = 30) -> Tuple[int, str, str]:
    cmd = [adb_bin, "-s", serial] + args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"


def getprop(adb_bin: str, serial: str, prop: str) -> str:
    rc, out, _ = run_adb(adb_bin, serial, ["shell", "getprop", prop], timeout=15)
    return out.strip() if rc == 0 else ""


def resolve_firmware_images_dir(args: dict, adb_bin: str, serial: str) -> str:
    """Resolve the images/ directory containing fdl1-sign.bin."""
    explicit = str(args.get("firmware_dir") or "").strip()
    if explicit:
        if os.path.isfile(os.path.join(explicit, "fdl1-sign.bin")):
            return explicit
        sub = os.path.join(explicit, "images")
        if os.path.isfile(os.path.join(sub, "fdl1-sign.bin")):
            return sub
        raise RuntimeError(f"fdl1-sign.bin not found in firmware_dir={explicit}")

    fw_root = _env("STP_FLASH_FIRMWARE_ROOT", "") or os.path.join(
        _env("STP_NFS_ROOT", _DEFAULT_NFS_ROOT), "firmware"
    )
    family = str(args.get("family") or "").strip()
    if not family:
        raise RuntimeError("either firmware_dir or family must be provided")

    version = _env("STP_FLASH_FIRMWARE_VERSION", "") or str(args.get("version") or "").strip()
    if not version:
        latest_path = os.path.join(fw_root, family, "latest.json")
        if not os.path.isfile(latest_path):
            raise RuntimeError(f"version not provided and latest.json not found at {latest_path}")
        with open(latest_path, "r", encoding="utf-8") as fh:
            latest = json.load(fh)
        model = getprop(adb_bin, serial, "ro.product.model")
        versions_map = latest.get("versions") if isinstance(latest.get("versions"), dict) else {}
        version = str(versions_map.get(model) or latest.get("version") or "").strip()
        if not version:
            raise RuntimeError(f"could not resolve target version from {latest_path} (model={model!r})")

    images_dir = os.path.join(fw_root, family, version, "images")
    if not os.path.isfile(os.path.join(images_dir, "fdl1-sign.bin")):
        raise RuntimeError(f"fdl1-sign.bin not found at {images_dir}")
    return images_dir


def get_sparse_trimmed_size(sparse_path: str) -> Optional[int]:
    """Return byte offset where non-CHUNK_DONT_CARE data ends, aligned up to 16 MB."""
    if not os.path.isfile(sparse_path):
        return None
    with open(sparse_path, "rb") as f:
        hdr = f.read(28)
        if len(hdr) < 28:
            return None
        magic, _, _, _, _, blk_sz, _, total_chunks, _ = struct.unpack("<IHHHHIIII", hdr)
        if magic != 0xED26FF3A:
            return None
        off_blk = 0
        last_data_end_blk = 0
        for _ in range(total_chunks):
            chdr = f.read(12)
            if len(chdr) < 12:
                break
            ctype, _, cblks, csz = struct.unpack("<HHII", chdr)
            f.seek(csz - 12, 1)
            if ctype != 0xCAC3:  # not CHUNK_DONT_CARE
                last_data_end_blk = off_blk + cblks
            off_blk += cblks
    align = 16 * 1024 * 1024
    raw_bytes = last_data_end_blk * blk_sz
    return ((raw_bytes + align - 1) // align) * align


def prepare_local_cache(fw_dir: str, cache_dir: str, progress: Progress) -> Tuple[str, str]:
    """Cache firmware files and trimmed super.img on local SSD to avoid NFS stalls."""
    progress.stamp(stage="cache-prepare", cache_dir=cache_dir)
    os.makedirs(cache_dir, exist_ok=True)
    local_fw = os.path.join(cache_dir, "fw_cache")
    local_raw = os.path.join(local_fw, "raw")
    os.makedirs(local_raw, exist_ok=True)

    copied = 0
    for rel in SMALL_FW_FILES:
        src = os.path.join(fw_dir, rel)
        if not os.path.isfile(src):
            raise RuntimeError(f"required firmware file missing: {src}")
        dst = os.path.join(local_fw, rel)
        if not os.path.isfile(dst) or os.path.getsize(dst) != os.path.getsize(src):
            shutil.copyfile(src, dst + ".tmp")
            os.rename(dst + ".tmp", dst)
            copied += 1

    for rel in RAW_FW_FILES:
        src = os.path.join(fw_dir, "raw", rel)
        if not os.path.isfile(src):
            raise RuntimeError(f"required raw firmware file missing: {src}")
        dst = os.path.join(local_raw, rel)
        if not os.path.isfile(dst) or os.path.getsize(dst) != os.path.getsize(src):
            shutil.copyfile(src, dst + ".tmp")
            os.rename(dst + ".tmp", dst)
            copied += 1

    sparse_super = os.path.join(fw_dir, "super.img")
    raw_super = os.path.join(fw_dir, "raw", "super.img")
    src_super = raw_super if os.path.isfile(raw_super) else sparse_super
    if not os.path.isfile(src_super):
        raise RuntimeError(f"super.img not found under {fw_dir}")

    trimmed_size = get_sparse_trimmed_size(sparse_super) or _EXPECTED_SUPER_SIZE
    cached_super = os.path.join(cache_dir, f"super_trimmed_{trimmed_size}.img")

    if not (os.path.isfile(cached_super) and os.path.getsize(cached_super) == trimmed_size):
        existing_raw_cache = os.path.join(cache_dir, "super.img")
        copy_src = (
            existing_raw_cache
            if (os.path.isfile(existing_raw_cache) and os.path.getsize(existing_raw_cache) == _EXPECTED_SUPER_SIZE)
            else src_super
        )
        progress.stamp(stage="cache-trim-super", target_mb=round(trimmed_size / (1024 * 1024), 1))
        tmp_target = cached_super + ".tmp"
        remaining = trimmed_size
        buf_sz = 16 * 1024 * 1024
        with open(copy_src, "rb") as fin, open(tmp_target, "wb") as fout:
            while remaining > 0:
                chunk = fin.read(min(buf_sz, remaining))
                if not chunk:
                    break
                fout.write(chunk)
                remaining -= len(chunk)
                if remaining % (256 * 1024 * 1024) == 0:
                    progress.stamp(stage="cache-trim-super", remaining_mb=round(remaining / (1024 * 1024), 1))
        os.rename(tmp_target, cached_super)
        actual = os.path.getsize(cached_super)
        if actual != trimmed_size:
            raise RuntimeError(f"trimmed super.img size mismatch: got {actual}, expected {trimmed_size}")

    progress.stamp(stage="cache-ready", copied_files=copied, super_mb=round(trimmed_size / (1024 * 1024), 1))
    return local_fw, cached_super


def acquire_host_lock(progress: Progress, timeout_seconds: int = 7200, lock_path: str = _HOST_LOCK_PATH) -> int:
    """Blocking flock(LOCK_EX) with 5s lock-wait PROGRESS stamps."""
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o666)
    except PermissionError:
        # protected_regular=2 blocks O_CREAT in sticky /tmp when owned by another user (e.g. android vs root).
        fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
    waited = 0
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            progress.stamp(stage="lock-held", waited_seconds=waited)
            return fd
        except BlockingIOError:
            if waited >= timeout_seconds:
                os.close(fd)
                raise RuntimeError(f"timed out waiting for host flash lock {lock_path} after {waited}s") from None
            progress.stamp(stage="lock-wait", waited_seconds=waited)
            time.sleep(5)
            waited += 5


def build_spd_dump_args(spd_dump_bin: str, fw_dir: str, super_path: str) -> List[str]:
    """Build Scheme 2 spd_dump command line, asserting no protected NV partitions are touched."""
    fdl1 = os.path.join(fw_dir, "fdl1-sign.bin")
    fdl2 = os.path.join(fw_dir, "edk2-fdl2-sign.bin")
    raw_dir = os.path.join(fw_dir, "raw")
    userdata = os.path.join(fw_dir, "userdata.img")

    parts_to_flash: List[Tuple[str, str]] = [
        ("uboot_a", os.path.join(fw_dir, "edk2-sign.bin")),
        ("sml_a", os.path.join(fw_dir, "sml-sign.bin")),
        ("trustos_a", os.path.join(fw_dir, "tos-sign.bin")),
        ("teecfg_a", os.path.join(fw_dir, "teecfg-sign.bin")),
        ("nr_modem_a", os.path.join(fw_dir, "SC9600_QogirN5_PS_modem.bin")),
        ("nr_phy_a", os.path.join(fw_dir, "SC9600_QogirN5_PHY_modem.bin")),
        ("l_agdsp_a", os.path.join(fw_dir, "ums9632_n5_adsp.bin")),
        ("ch_sys_a", os.path.join(fw_dir, "QogirN5_CH.bin")),
        ("pm_sys_a", os.path.join(fw_dir, "QogirN5_SP.bin")),
        ("logo_a", os.path.join(fw_dir, "logo.bin")),
        ("fbootlogo_a", os.path.join(fw_dir, "logo.bin")),
        ("boot_a", os.path.join(fw_dir, "boot.img")),
        ("init_boot_a", os.path.join(fw_dir, "init_boot.img")),
        ("vendor_boot_a", os.path.join(fw_dir, "vendor_boot.img")),
        ("dtbo_a", os.path.join(fw_dir, "dtbo.img")),
        ("vbmeta_system_a", os.path.join(fw_dir, "vbmeta_system.img")),
        ("vbmeta_system_ext_a", os.path.join(fw_dir, "vbmeta_system_ext.img")),
        ("vbmeta_vendor_a", os.path.join(fw_dir, "vbmeta_vendor.img")),
        ("vbmeta_product_a", os.path.join(fw_dir, "vbmeta_product.img")),
        ("vbmeta_odm_a", os.path.join(fw_dir, "vbmeta_odm.img")),
        ("vbmeta_a", os.path.join(fw_dir, "vbmeta-sign.img")),
        ("cache", os.path.join(raw_dir, "cache.img")),
        ("blackbox", os.path.join(raw_dir, "blackbox.img")),
        ("super", super_path),
    ]

    for part in ERASE_PARTITIONS:
        if is_protected_partition(part):
            raise RuntimeError(f"refusing to erase protected NV partition: {part}")
    for part, _ in parts_to_flash:
        if is_protected_partition(part):
            raise RuntimeError(f"refusing to write protected NV partition: {part}")

    cmd: List[str] = [
        spd_dump_bin,
        "--verbose", "1",
        "--wait", "60",
        "keep_charge", "1",
        "fdl", fdl1, "0x65008000",
        "fdl", fdl2, "0xBDFFFE00",
        "disable_transcode",
        "timeout", "15000",
        "blk_size", "0x8000",
        "keep_charge", "1",
    ]
    for part in ERASE_PARTITIONS:
        cmd.extend(["erase_part", part])
    cmd.extend(["write_part", "userdata", userdata])
    for part, img_path in parts_to_flash:
        cmd.extend(["write_part", part, img_path])
    cmd.append("reset")
    return cmd


def run_spd_dump(cmd: List[str], adb_bin: str, serial: str, progress: Progress, log: list) -> float:
    """Launch spd_dump, reboot device to autodloader, and stream PROGRESS stamps."""
    t0 = time.time()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    time.sleep(1.5)
    progress.stamp(stage="autodloader")
    rc_adb, out_adb, err_adb = run_adb(adb_bin, serial, ["reboot", "autodloader"], timeout=15)
    log.append(f"adb reboot autodloader -> rc={rc_adb} {out_adb} {err_adb}".strip())

    assert proc.stdout is not None
    tail: List[str] = []
    for raw_line in proc.stdout:
        line = raw_line.rstrip()
        if not line:
            continue
        tail.append(line)
        if len(tail) > 40:
            tail.pop(0)
        m = _LOAD_PART_RE.search(line)
        if m:
            progress.stamp(stage="bsl-flash", partition=m.group(1), elapsed_s=round(time.time() - t0, 1))
        elif any(tag in line for tag in ("BSL_REP_VER", "FDL2", "erase_partition", "RESET")):
            progress.stamp(stage="bsl-flash", detail=line[:120], elapsed_s=round(time.time() - t0, 1))

    rc = proc.wait(timeout=360)
    elapsed = round(time.time() - t0, 1)
    log.extend(tail[-15:])
    if rc != 0:
        raise RuntimeError(f"spd_dump exited with rc={rc} after {elapsed}s: {' | '.join(tail[-5:])}")
    return elapsed


def wait_boot_completed(adb_bin: str, serial: str, progress: Progress, timeout_seconds: int) -> float:
    t0 = time.time()
    last_stamp = 0.0
    while time.time() - t0 < timeout_seconds:
        rc, out, _ = run_adb(adb_bin, serial, ["shell", "getprop", "sys.boot_completed"], timeout=10)
        if rc == 0 and out.strip() == "1":
            elapsed = round(time.time() - t0, 1)
            progress.stamp(stage="boot-ready", boot_seconds=elapsed)
            return elapsed
        now = time.time()
        if now - last_stamp >= 5.0:
            progress.stamp(stage="boot-wait", elapsed_s=round(now - t0, 1))
            last_stamp = now
        time.sleep(2.0)
    raise RuntimeError(f"device {serial} did not reach sys.boot_completed=1 within {timeout_seconds}s")


def run(args: dict, serial: str, progress: Progress, log: list) -> dict:
    t0 = time.time()
    adb_bin = resolve_adb()
    spd_dump_bin = resolve_spd_dump()
    progress.stamp(stage="preflight", serial=serial, spd_dump=spd_dump_bin)

    fw_dir = resolve_firmware_images_dir(args, adb_bin, serial)
    cache_dir = str(args.get("cache_dir") or _DEFAULT_CACHE_DIR).strip()

    pre_build_type = getprop(adb_bin, serial, "ro.build.type")
    pre_incremental = getprop(adb_bin, serial, "ro.build.version.incremental")
    target_build_type = str(args.get("verify_build_type") if args.get("verify_build_type") is not None else "userdebug").strip()
    target_incremental = str(args.get("verify_incremental") or "").strip()

    if (
        not _bool(args.get("force"), False)
        and _bool(args.get("skip_if_current"), False)
        and target_incremental
        and pre_incremental == target_incremental
        and (not target_build_type or pre_build_type == target_build_type)
    ):
        return {
            "skipped": True,
            "metrics": {
                "current_build_type": pre_build_type,
                "current_incremental": pre_incremental,
                "firmware_dir": fw_dir,
            },
        }

    if _bool(args.get("dry_run"), False):
        cmd = build_spd_dump_args(spd_dump_bin, fw_dir, os.path.join(fw_dir, "super.img"))
        return {
            "metrics": {
                "dry_run": True,
                "firmware_dir": fw_dir,
                "pre_build_type": pre_build_type,
                "pre_incremental": pre_incremental,
                "spd_dump_cmd_len": len(cmd),
            }
        }

    local_fw, cached_super = prepare_local_cache(fw_dir, cache_dir, progress)
    cmd = build_spd_dump_args(spd_dump_bin, local_fw, cached_super)

    lock_timeout = int(args.get("lock_timeout_seconds", 7200))
    lock_fd = acquire_host_lock(progress, timeout_seconds=lock_timeout)
    try:
        flash_seconds = run_spd_dump(cmd, adb_bin, serial, progress, log)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        except OSError:
            pass

    boot_seconds = wait_boot_completed(
        adb_bin, serial, progress, int(args.get("verify_wait_seconds", 300))
    )

    progress.stamp(stage="verify")
    post_build_type = getprop(adb_bin, serial, "ro.build.type")
    post_incremental = getprop(adb_bin, serial, "ro.build.version.incremental")
    if target_build_type and post_build_type != target_build_type:
        raise RuntimeError(f"post-flash ro.build.type={post_build_type!r} != {target_build_type!r}")
    if target_incremental and post_incremental != target_incremental:
        raise RuntimeError(f"post-flash incremental={post_incremental!r} != {target_incremental!r}")

    root_uid = ""
    if _bool(args.get("verify_root"), True):
        run_adb(adb_bin, serial, ["root"], timeout=15)
        time.sleep(2.0)
        _, root_uid, _ = run_adb(adb_bin, serial, ["shell", "id"], timeout=15)
        if "uid=0(root)" not in root_uid:
            raise RuntimeError(f"post-flash adb root failed: {root_uid!r}")

    return {
        "metrics": {
            "firmware_dir": fw_dir,
            "pre_build_type": pre_build_type,
            "pre_incremental": pre_incremental,
            "post_build_type": post_build_type,
            "post_incremental": post_incremental,
            "root_uid": root_uid,
            "flash_duration_seconds": flash_seconds,
            "boot_duration_seconds": boot_seconds,
            "duration_seconds": round(time.time() - t0, 1),
        }
    }


def main() -> None:
    args = _params()
    serial = _env("STP_DEVICE_SERIAL", "")

    if len(sys.argv) > 1:
        i = 1
        while i < len(sys.argv):
            a = sys.argv[i]
            if a == "--serial":
                serial = sys.argv[i + 1]; i += 2
            elif a == "--firmware-dir":
                args["firmware_dir"] = sys.argv[i + 1]; i += 2
            elif a == "--cache-dir":
                args["cache_dir"] = sys.argv[i + 1]; i += 2
            elif a == "--dry-run":
                args["dry_run"] = True; i += 1
            elif a == "--force":
                args["force"] = True; i += 1
            else:
                print(f"unknown arg {a}", file=sys.stderr)
                sys.exit(2)

    if not serial:
        _output(False, error_message="STP_DEVICE_SERIAL is not set")
        return

    progress = Progress()
    log: list = []
    try:
        result = run(args, serial, progress, log)
        if result.get("skipped"):
            _output(True, skipped=True, metrics=result.get("metrics", {}), error_message="")
        else:
            _output(True, metrics=result["metrics"])
    except Exception as exc:  # noqa: BLE001 - contract: always emit JSON
        _output(False, error_message=str(exc), metrics={"log_tail": log[-10:]})


if __name__ == "__main__":
    main()
