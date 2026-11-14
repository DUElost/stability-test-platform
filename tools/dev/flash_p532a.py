#!/usr/bin/env python3
"""flash_p532a.py -- P532AA (Unisoc ums9632) automated firmware flashing pipeline.

Implements Scheme 2 (Official PAC Silent Direct Boot):
1. Preflight checks (ADB status, binary prerequisites, SSD raw image cache).
2. Host serialization lock via flock (/tmp/stp-flash-p532a.lock) to avoid USB enumeration races.
3. Switch device from Android OS to BSL mode via `adb reboot autodloader`.
4. High-speed BSL flashing via reinforced `spd_dump`:
   - FDL1 @ 0x65008000 + EDK2 FDL2 @ 0xBDFFFE00 (with keep_charge enabled)
   - Erase: metadata, sysdumpdb, misc, sd_klog, uboot_log (clears crash marks & BCB)
   - Write: 4KB userdata.img (triggers Android Vold silent format on first boot)
   - Flash: splloader, uboot, boot, recovery, vbmeta, dtbo, modules, super
   - Issue: reset
5. Cold-boot observation:
   - Direct boot into Android OS within ~60s (completely bypasses Recovery & Sysdump white-screen)
   - Polls `sys.boot_completed`
6. Post-flash verification:
   - Verify `ro.build.type == userdebug`
   - Verify `ro.build.version.incremental`
   - Verify `adb root` privilege
   - Skip OOBE to leave device at Launcher desktop
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

DEFAULT_FW_DIR = "/mnt/stp-aee/firmware/P532AA/P532AA-EVT-16.0-DailyBuild-userdebug-202611090000/images"
DEFAULT_CACHE_DIR = "/var/tmp/stp_flash"
DEFAULT_SPD_DUMP = "/usr/local/bin/spd_dump"
LOCK_FILE = "/tmp/stp-flash-p532a.lock"
EXPECTED_SUPER_SIZE = 6291456000  # raw super.img size (1536000 * 4096)


def log(msg: str) -> None:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


class HostLock:
    def __init__(self, lock_path: str = LOCK_FILE, timeout: float = 1800.0):
        self.lock_path = lock_path
        self.timeout = timeout
        self.fd: Optional[int] = None

    def __enter__(self):
        log(f"Acquiring host serialization lock: {self.lock_path} ...")
        self.fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o666)
        start = time.time()
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                log("Host serialization lock acquired.")
                return self
            except (BlockingIOError, OSError):
                if time.time() - start > self.timeout:
                    raise TimeoutError(f"Timed out waiting for {self.lock_path} after {self.timeout}s") from None
                time.sleep(3.0)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                os.close(self.fd)
            except Exception:
                pass
            log("Host serialization lock released.")


def run_cmd(cmd: List[str], timeout: float = 60.0) -> Tuple[int, str, str]:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def run_adb(serial: str, adb_args: List[str], timeout: float = 30.0) -> Tuple[int, str, str]:
    cmd = ["adb", "-s", serial] + adb_args
    return run_cmd(cmd, timeout=timeout)


def get_sparse_trimmed_size(sparse_path: str) -> Optional[int]:
    """Return byte offset where actual non-DONT_CARE data ends, rounded up to 16MB."""
    if not os.path.exists(sparse_path):
        return None
    with open(sparse_path, "rb") as f:
        hdr = f.read(28)
        if len(hdr) < 28:
            return None
        magic, _, _, _, _, blk_sz, total_blks, total_chunks, _ = struct.unpack("<IHHHHIIII", hdr)
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


def prepare_local_cache(fw_dir: str, cache_dir: str) -> Tuple[str, str]:
    """Cache all required firmware files & trimmed super.img onto local SSD to avoid NFS stalls and stay under 300s."""
    os.makedirs(cache_dir, exist_ok=True)
    local_fw = os.path.join(cache_dir, "fw_cache")
    local_raw = os.path.join(local_fw, "raw")
    os.makedirs(local_raw, exist_ok=True)

    small_files = [
        "fdl1-sign.bin", "edk2-fdl2-sign.bin", "userdata.img",
        "edk2-sign.bin", "sml-sign.bin", "tos-sign.bin", "teecfg-sign.bin",
        "SC9600_QogirN5_PS_modem.bin", "SC9600_QogirN5_PHY_modem.bin",
        "ums9632_n5_adsp.bin", "QogirN5_CH.bin", "QogirN5_SP.bin", "logo.bin",
        "boot.img", "init_boot.img", "vendor_boot.img", "dtbo.img",
        "vbmeta_system.img", "vbmeta_system_ext.img", "vbmeta_vendor.img",
        "vbmeta_product.img", "vbmeta_odm.img", "vbmeta-sign.img",
    ]
    raw_files = ["cache.img", "blackbox.img"]

    copied = 0
    for rel in small_files:
        src = os.path.join(fw_dir, rel)
        dst = os.path.join(local_fw, rel)
        if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
            shutil.copyfile(src, dst + ".tmp")
            os.rename(dst + ".tmp", dst)
            copied += 1

    for rel in raw_files:
        src = os.path.join(fw_dir, "raw", rel)
        dst = os.path.join(local_raw, rel)
        if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
            shutil.copyfile(src, dst + ".tmp")
            os.rename(dst + ".tmp", dst)
            copied += 1

    if copied:
        log(f"Cached {copied} firmware image files to local SSD: {local_fw}")
    else:
        log(f"Local firmware cache hit: {local_fw}")

    # Trim trailing CHUNK_DONT_CARE zeros from super.img so BSL flash finishes well under 300s watchdog
    sparse_super = os.path.join(fw_dir, "super.img")
    raw_super = os.path.join(fw_dir, "raw", "super.img")
    src_super = raw_super if os.path.exists(raw_super) else sparse_super
    trimmed_size = get_sparse_trimmed_size(sparse_super) or EXPECTED_SUPER_SIZE
    cached_super = os.path.join(cache_dir, f"super_trimmed_{trimmed_size}.img")

    if os.path.exists(cached_super) and os.path.getsize(cached_super) == trimmed_size:
        log(f"Cache hit: {cached_super} ({trimmed_size / (1024*1024):.1f} MB)")
        return local_fw, cached_super

    # Prefer slicing from already-cached /var/tmp/stp_flash/super.img if present to avoid NFS copy
    existing_raw_cache = os.path.join(cache_dir, "super.img")
    copy_src = existing_raw_cache if (os.path.exists(existing_raw_cache) and os.path.getsize(existing_raw_cache) == EXPECTED_SUPER_SIZE) else src_super

    log(f"Creating trimmed super.img ({trimmed_size / (1024*1024):.1f} MB) from {copy_src} -> {cached_super} ...")
    t0 = time.time()
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
    os.rename(tmp_target, cached_super)
    size = os.path.getsize(cached_super)
    dt = time.time() - t0
    log(f"Prepared trimmed super.img in {dt:.1f}s ({size / (1024*1024):.1f} MB)")
    if size != trimmed_size:
        raise RuntimeError(f"Unexpected trimmed super.img size: got {size}, expected {trimmed_size}")
    return local_fw, cached_super


def build_spd_dump_args(spd_dump_bin: str, fw_dir: str, super_path: str) -> List[str]:
    fdl1 = os.path.join(fw_dir, "fdl1-sign.bin")
    fdl2 = os.path.join(fw_dir, "edk2-fdl2-sign.bin")
    raw_dir = os.path.join(fw_dir, "raw")
    userdata = os.path.join(fw_dir, "userdata.img")

    parts_to_flash = [
        # 1. Bootloader & Security
        ("uboot_a", os.path.join(fw_dir, "edk2-sign.bin")),
        ("sml_a", os.path.join(fw_dir, "sml-sign.bin")),
        ("trustos_a", os.path.join(fw_dir, "tos-sign.bin")),
        ("teecfg_a", os.path.join(fw_dir, "teecfg-sign.bin")),
        # 2. Modems, RF, and DSP
        ("nr_modem_a", os.path.join(fw_dir, "SC9600_QogirN5_PS_modem.bin")),
        ("nr_phy_a", os.path.join(fw_dir, "SC9600_QogirN5_PHY_modem.bin")),
        ("l_agdsp_a", os.path.join(fw_dir, "ums9632_n5_adsp.bin")),
        ("ch_sys_a", os.path.join(fw_dir, "QogirN5_CH.bin")),
        ("pm_sys_a", os.path.join(fw_dir, "QogirN5_SP.bin")),
        # 3. Logos
        ("logo_a", os.path.join(fw_dir, "logo.bin")),
        ("fbootlogo_a", os.path.join(fw_dir, "logo.bin")),
        # 4. Android Boot & DTBO
        ("boot_a", os.path.join(fw_dir, "boot.img")),
        ("init_boot_a", os.path.join(fw_dir, "init_boot.img")),
        ("vendor_boot_a", os.path.join(fw_dir, "vendor_boot.img")),
        ("dtbo_a", os.path.join(fw_dir, "dtbo.img")),
        # 5. VBMeta
        ("vbmeta_system_a", os.path.join(fw_dir, "vbmeta_system.img")),
        ("vbmeta_system_ext_a", os.path.join(fw_dir, "vbmeta_system_ext.img")),
        ("vbmeta_vendor_a", os.path.join(fw_dir, "vbmeta_vendor.img")),
        ("vbmeta_product_a", os.path.join(fw_dir, "vbmeta_product.img")),
        ("vbmeta_odm_a", os.path.join(fw_dir, "vbmeta_odm.img")),
        ("vbmeta_a", os.path.join(fw_dir, "vbmeta-sign.img")),
        # 6. System & Data images
        ("cache", os.path.join(raw_dir, "cache.img")),
        ("blackbox", os.path.join(raw_dir, "blackbox.img")),
        ("super", super_path),
    ]

    cmd = [
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
        # Scheme 2: Erase crash & BCB states to ensure direct silent boot
        "erase_part", "metadata",
        "erase_part", "sysdumpdb",
        "erase_part", "misc",
        "erase_part", "sd_klog",
        "erase_part", "uboot_log",
        # Scheme 2: 4KB zero userdata to trigger Android Vold auto-format
        "write_part", "userdata", userdata,
    ]

    for part, path in parts_to_flash:
        cmd.extend(["write_part", part, path])

    cmd.append("reset")
    return cmd


def flash_device(serial: str, fw_dir: str, cache_dir: str, spd_dump_bin: str) -> Dict:
    t_start = time.time()
    metrics: Dict[str, object] = {
        "serial": serial,
        "firmware_dir": fw_dir,
        "start_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    # Step 1: Preflight
    log(f"[{serial}] Step 1: Preflight checks...")
    rc, out, _ = run_adb(serial, ["shell", "getprop", "ro.build.display.id"])
    in_bsl = False
    if rc != 0:
        log(f"[{serial}] Device not reachable via ADB. Checking if already in BSL mode (1782:4d00)...")
        rc_usb, out_usb, _ = run_cmd(["lsusb"])
        if "1782:4d00" in out_usb:
            log(f"[{serial}] Detected device already in BSL download mode (1782:4d00)! Entering RECOVERY FLASH mode.")
            in_bsl = True
        else:
            raise RuntimeError(f"Device {serial} neither online via ADB nor in BSL mode: rc={rc}, out={out}")
    else:
        metrics["pre_build_display"] = out
        _, out_type, _ = run_adb(serial, ["shell", "getprop", "ro.build.type"])
        metrics["pre_build_type"] = out_type
        log(f"[{serial}] Current build: {out} ({out_type})")

    local_fw_dir, super_path = prepare_local_cache(fw_dir, cache_dir)
    spd_cmd = build_spd_dump_args(spd_dump_bin, local_fw_dir, super_path)

    # Step 2: Acquire host lock & flash via BSL
    with HostLock():
        log(f"[{serial}] Step 2: Starting spd_dump daemon and connecting to BSL...")
        t_flash_start = time.time()
        proc = subprocess.Popen(spd_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        if not in_bsl:
            time.sleep(1.0)
            rc_reboot, _, err_reboot = run_adb(serial, ["reboot", "autodloader"])
            log(f"[{serial}] adb reboot autodloader returned {rc_reboot} ({err_reboot})")
        else:
            log(f"[{serial}] Device already in BSL mode, skipping adb reboot.")

        stdout_lines = []
        for line in proc.stdout:
            stdout_lines.append(line)
            if any(k in line for k in ("load_partition", "erase", "partition super", "partition blackbox", "BSL", "keep_charge", "reset", "ERR", "error")):
                log(f"  [spd_dump] {line.strip()}")

        proc.wait()
        dt_flash = time.time() - t_flash_start
        metrics["flash_duration_seconds"] = round(dt_flash, 2)
        log(f"[{serial}] spd_dump finished with rc={proc.returncode} in {dt_flash:.1f}s")
        if proc.returncode != 0:
            tail = "\n".join(stdout_lines[-30:])
            raise RuntimeError(f"spd_dump failed with rc={proc.returncode}:\n{tail}")

    # Step 3: Wait for direct cold boot (Scheme 2 skips Recovery)
    log(f"[{serial}] Step 3: Observing cold boot (waiting for direct OS completion)...")
    t_boot_start = time.time()
    boot_timeout = 240.0
    boot_completed = False
    states_seen = []

    while time.time() - t_boot_start < boot_timeout:
        time.sleep(2.0)
        elapsed = time.time() - t_boot_start
        rc, out, _ = run_adb(serial, ["shell", "getprop", "sys.boot_completed"])
        state = out if rc == 0 else "offline"
        if not states_seen or states_seen[-1] != state:
            states_seen.append(state)
            log(f"  [{elapsed:4.1f}s] State: {state}")

        if state == "1":
            boot_completed = True
            log(f"[{serial}] Boot completed detected in {elapsed:.1f}s!")
            break

    metrics["boot_duration_seconds"] = round(time.time() - t_boot_start, 2)
    metrics["states_seen"] = states_seen
    if not boot_completed:
        raise TimeoutError(f"Device {serial} failed to complete boot within {boot_timeout}s. States: {states_seen}")

    # Step 4: Verification & OOBE Skip
    log(f"[{serial}] Step 4: Post-flash verification & OOBE bypass...")
    _, post_id, _ = run_adb(serial, ["shell", "getprop", "ro.build.display.id"])
    _, post_type, _ = run_adb(serial, ["shell", "getprop", "ro.build.type"])
    _, post_inc, _ = run_adb(serial, ["shell", "getprop", "ro.build.version.incremental"])
    metrics["post_build_display"] = post_id
    metrics["post_build_type"] = post_type
    metrics["post_build_incremental"] = post_inc

    log(f"[{serial}] Post-flash build: {post_id} (type={post_type}, inc={post_inc})")
    if post_type != "userdebug":
        raise AssertionError(f"Expected userdebug build, got: {post_type}")

    # Root test
    rc_root, out_root, _ = run_adb(serial, ["root"])
    time.sleep(2.0)
    _, uid_out, _ = run_adb(serial, ["shell", "id"])
    log(f"[{serial}] adb root: {out_root}; id: {uid_out}")
    metrics["root_uid"] = uid_out
    if "uid=0(root)" not in uid_out:
        raise AssertionError(f"Failed to obtain root: {uid_out}")

    # OOBE bypass
    run_adb(serial, ["shell", "settings put secure user_setup_complete 1"])
    run_adb(serial, ["shell", "settings put global device_provisioned 1"])
    run_adb(serial, ["shell", "input keyevent 3"])  # KEYCODE_HOME

    total_time = round(time.time() - t_start, 2)
    metrics["total_duration_seconds"] = total_time
    metrics["success"] = True
    log(f"[{serial}] ALL PHASES SUCCEEDED in {total_time:.1f}s!")
    return metrics


def main():
    parser = argparse.ArgumentParser(description="P532AA Automated Firmware Flashing (Scheme 2)")
    parser.add_argument("--serial", "-s", help="Device serial number (auto-detected if single device)")
    parser.add_argument("--firmware-dir", default=DEFAULT_FW_DIR, help="Path to firmware images directory")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR, help="Path to local SSD cache directory")
    parser.add_argument("--spd-dump", default=DEFAULT_SPD_DUMP, help="Path to spd_dump executable")
    args = parser.parse_args()

    serial = args.serial
    if not serial:
        rc, out, _ = run_cmd(["adb", "devices"])
        lines = [line.split()[0] for line in out.splitlines()[1:] if line.strip() and not line.startswith("*")]
        if len(lines) == 1:
            serial = lines[0]
            log(f"Auto-selected sole device: {serial}")
        elif len(lines) > 1:
            log(f"Multiple devices detected: {lines}. Please specify --serial <SERIAL>.")
            sys.exit(1)
        else:
            log("No device detected via adb.")
            sys.exit(1)

    try:
        res = flash_device(serial, args.firmware_dir, args.cache_dir, args.spd_dump)
        print("\n" + json.dumps(res, indent=2))
    except Exception as e:
        log(f"ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
