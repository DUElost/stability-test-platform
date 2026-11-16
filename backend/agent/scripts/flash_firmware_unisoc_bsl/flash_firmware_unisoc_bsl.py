#!/usr/bin/env python3
"""Flash one Unisoc (SPRD) device via BSL/FDL2 (spd_dump) using Scheme 2 silent direct boot.

Implements Scheme 2 flashing inside the STP agent script contract:
1. Resolve firmware target (explicit firmware_dir, family+version, or family+latest.json)
   and bind device identity (ro.product.model / device / name) against manifest.json.
2. Load strict flash specification from source images/plan.json (FDL1/FDL2 files & load
   bases, whitelist-filtered EraseFlash2 list, UserData, writable partitions).
3. Enforce pre-flash safety gates: battery level (battery_min_level=5, skip_battery=false)
   and absence of any Unisoc download port (1782:4d00) on the host USB bus.
4. Acquire host-wide serialization lock (/tmp/stp-flash-firmware.lock, shared with
   flash_firmware and flash_firmware_unisoc), emitting stage=lock-wait PROGRESS stamps
   every 5s so queued devices are not killed by step stall detection.
5. Prepare content-verified (SHA-256) isolated SSD cache and trimmed super.img, cleaning
   up any staging files on failure.
6. Switch device to BSL mode via `adb reboot autodloader` and execute `spd_dump` under a
   bounded total duration timeout, terminating and reaping the child on any failure.
7. Verify positive reboot evidence and read back manifest.version_prop == manifest.version
   after cold boot, plus optional build_type / incremental / root checks.

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
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import uuid
from typing import Dict, List, Optional, Tuple

_PROGRESS_PREFIX = "PROGRESS "
_HOST_LOCK_PATH = "/tmp/stp-flash-firmware.lock"
_DEFAULT_CACHE_DIR = "/var/tmp/stp_flash"
_DEFAULT_NFS_ROOT = "/mnt/stp-aee"
_SYSFS_USB_BASE = "/sys/bus/usb/devices"
_UNISOC_DOWNLOAD_VID = "1782"
_UNISOC_DOWNLOAD_PID = "4d00"
_DEFAULT_BATTERY_MIN_LEVEL = 5
_DEFAULT_SPD_DUMP_TIMEOUT_SECONDS = 3600

_ALLOWED_ERASE_PARTITIONS = frozenset({
    "metadata",
    "misc",
    "sysdumpdb",
    "uboot_log",
    "sd_klog",
})

PROTECTED_NV_PARTITIONS = frozenset({
    "prodnv",
    "calinv",
    "miscdata",
    "persist",
    "spl",
    "splloader",
    "nr_factorynv",
    "nr_downloadnv",
    "nr_deltanv",
    "nr_runtimenv1",
    "nr_runtimenv2",
    "l_fixnv1",
    "l_fixnv2",
    "l_runtimenv1",
    "l_runtimenv2",
})

_PROTECTED_NV_PREFIXES: Tuple[str, ...] = (
    "nr_downloadnv_",
    "nr_deltanv_",
    "nr_fixnv",
    "nr_runtimenv",
    "l_fixnv",
    "l_runtimenv",
    "l_deltanv",
)

_TAIL_PARTITIONS = frozenset({"cache", "blackbox"})
_SKIP_PLAN_IDS = frozenset({"FDL", "FDL2"})
_SKIP_PLAN_TYPES = frozenset({"FDL", "NAND_FDL", "EraseFlash2"})

_LOAD_PART_RE = re.compile(r"load_partition:\s*([^,\s]+)")


class FlashRejectedError(RuntimeError):
    """Rejection before or during flashing carrying route decision metadata."""

    def __init__(self, message: str, route: Optional[dict] = None) -> None:
        super().__init__(message)
        self.route = route


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


def _norm_ident(value: str) -> str:
    return value.strip().replace("-", "_")


def _strip_slot_suffix(part: str) -> str:
    if part.endswith("_a") or part.endswith("_b"):
        return part[:-2]
    return part


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
    p = part.strip().lower()
    base = _strip_slot_suffix(p)
    for candidate in (p, base):
        if candidate in PROTECTED_NV_PARTITIONS or candidate.startswith(_PROTECTED_NV_PREFIXES):
            return True
    return False


def resolve_firmware_cache_root(fw_dir: str, cache_dir: str) -> str:
    """Return an isolated SSD cache subdirectory per firmware path to avoid cross-model collisions."""
    norm = os.path.normpath(os.path.abspath(fw_dir))
    label_src = os.path.basename(os.path.dirname(norm)) if os.path.basename(norm) == "images" else os.path.basename(norm)
    safe_label = re.sub(r"[^A-Za-z0-9._-]+", "_", label_src).strip("._-") or "fw"
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:8]
    return os.path.join(cache_dir, f"{safe_label[:48]}_{digest}")


_LAST_REBOOT_SENT_AT: Dict[str, Optional[float]] = {"value": None}


def _parse_positive_finite_float(
    raw, name: str, default: float, route: Optional[dict] = None
) -> float:
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return default
    if isinstance(raw, bool):
        raise FlashRejectedError(
            f"{name}={raw!r} must be a finite positive number (expected > 0 and finite)",
            route=route,
        )
    try:
        val = float(raw)
    except (TypeError, ValueError) as exc:
        raise FlashRejectedError(
            f"{name}={raw!r} must be a finite positive number (expected > 0 and finite)",
            route=route,
        ) from exc
    if not math.isfinite(val) or val <= 0:
        raise FlashRejectedError(
            f"{name}={raw!r} must be a finite positive number (expected > 0 and finite)",
            route=route,
        )
    return val


def _load_json_dict(path: str, label: str, route: Optional[dict] = None) -> dict:
    if not os.path.isfile(path):
        raise FlashRejectedError(f"{label} missing at {path} (expected JSON object file)", route=route)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise FlashRejectedError(
            f"{label} unreadable/malformed at {path} (expected JSON object): {exc}",
            route=route,
        ) from exc
    if not isinstance(data, dict):
        raise FlashRejectedError(
            f"{label} invalid type at {path} (expected JSON object, got {type(data).__name__})",
            route=route,
        )
    return data


def _load_flash_spec(fw_dir: str, route: Optional[dict] = None) -> Dict[str, object]:
    """Load strict FDL, erase, and write-partition specification from images/plan.json without defaults."""
    plan_path = os.path.join(fw_dir, "plan.json")
    plan = _load_json_dict(plan_path, "plan.json", route=route)

    entries = plan.get("entries")
    if not isinstance(entries, list) or not entries:
        raise FlashRejectedError(
            f"plan.json key 'entries' must be a non-empty list in {plan_path} "
            f"(got {type(entries).__name__ if 'entries' in plan else 'missing'})",
            route=route,
        )

    fdl_cfg = plan.get("fdl")
    if not isinstance(fdl_cfg, dict):
        raise FlashRejectedError(
            f"plan.json key 'fdl' must be a JSON object in {plan_path} "
            f"(got {type(fdl_cfg).__name__ if 'fdl' in plan else 'missing'})",
            route=route,
        )
    fdl1_cfg = fdl_cfg.get("fdl1")
    fdl2_cfg = fdl_cfg.get("fdl2")
    if not isinstance(fdl1_cfg, dict) or not isinstance(fdl2_cfg, dict):
        raise FlashRejectedError(
            f"plan.json keys 'fdl.fdl1' and 'fdl.fdl2' must be JSON objects in {plan_path}",
            route=route,
        )

    fdl1_file = str(fdl1_cfg.get("file") or "").strip()
    fdl1_base = str(fdl1_cfg.get("base") or "").strip()
    fdl2_file = str(fdl2_cfg.get("file") or "").strip()
    fdl2_base = str(fdl2_cfg.get("base") or "").strip()
    if not (fdl1_file and fdl1_base and fdl2_file and fdl2_base):
        raise FlashRejectedError(
            f"plan.json keys 'fdl.fdl1.file', 'fdl.fdl1.base', 'fdl.fdl2.file', 'fdl.fdl2.base' "
            f"must be non-empty strings in {plan_path}",
            route=route,
        )

    if "erases" not in plan or not isinstance(plan.get("erases"), list):
        raise FlashRejectedError(
            f"plan.json key 'erases' must be a list in {plan_path} "
            f"(got {type(plan.get('erases')).__name__ if 'erases' in plan else 'missing'})",
            route=route,
        )

    erases: List[str] = []
    skipped_erases: List[str] = []
    seen_erases = set()
    for raw_part in plan["erases"]:
        part = str(raw_part or "").strip()
        if not part or part in seen_erases:
            continue
        seen_erases.add(part)
        if part in _ALLOWED_ERASE_PARTITIONS and not is_protected_partition(part):
            erases.append(part)
        else:
            skipped_erases.append(part)

    userdata_rel = ""
    userdata_count = 0
    super_rel = ""
    super_file = ""
    super_sparse = False
    head_parts: List[Tuple[str, str]] = []
    tail_parts: List[Tuple[str, str]] = []
    seen_parts = set()
    has_super = False

    for item in entries:
        if not isinstance(item, dict):
            continue
        fid = str(item.get("id") or "").strip()
        ftype = str(item.get("flush_type") or "").strip()
        if fid in _SKIP_PLAN_IDS or ftype in _SKIP_PLAN_TYPES:
            continue
        parts = [str(p).strip() for p in (item.get("partitions") or []) if str(p).strip()]
        if "userdata" in parts and fid != "UserData":
            raise FlashRejectedError(
                f"plan.json 'entries' item id={fid!r} must not target 'userdata' "
                f"(expected only id='UserData' with 'userdata' in 'partitions') in {plan_path}",
                route=route,
            )
        fname = str(item.get("file") or "").strip()
        raw_rel = str(item.get("raw") or "").strip()
        is_sparse = bool(item.get("sparse"))
        if fid == "UserData" and "userdata" in parts:
            ud_rel = raw_rel if (is_sparse and raw_rel) else fname
            if not ud_rel:
                raise FlashRejectedError(
                    f"plan.json 'UserData' entry missing non-empty 'file' string in {plan_path}",
                    route=route,
                )
            userdata_count += 1
            if userdata_count > 1:
                raise FlashRejectedError(
                    f"plan.json must contain exactly one 'entries' item with id='UserData' "
                    f"and 'userdata' in 'partitions' in {plan_path}",
                    route=route,
                )
            userdata_rel = ud_rel
        if not fname and not (raw_rel and "super" in parts):
            continue
        rel_path = raw_rel if (is_sparse and raw_rel) else fname
        if not parts:
            continue
        preserve = bool(item.get("preserve"))

        for part in parts:
            if part == "userdata":
                continue
            if preserve or is_protected_partition(part):
                continue
            if part == "super":
                has_super = True
                super_file = fname
                super_sparse = is_sparse
                super_rel = raw_rel or fname
                continue
            if part in seen_parts:
                continue
            seen_parts.add(part)
            if part in _TAIL_PARTITIONS:
                tail_parts.append((part, rel_path))
            else:
                head_parts.append((part, rel_path))

    if userdata_count != 1 or not userdata_rel:
        raise FlashRejectedError(
            f"plan.json missing required 'entries' item with id='UserData' and 'userdata' in 'partitions' "
            f"(expected exactly one) in {plan_path}",
            route=route,
        )
    rel_parts = head_parts + tail_parts
    if not rel_parts and not has_super:
        raise FlashRejectedError(
            f"plan.json key 'entries' contains no writable partitions (excluding 'userdata') in {plan_path}",
            route=route,
        )

    return {
        "has_plan": True,
        "fdl1": (fdl1_file, fdl1_base),
        "fdl2": (fdl2_file, fdl2_base),
        "erases": erases,
        "skipped_erases": skipped_erases,
        "userdata_rel": userdata_rel,
        "rel_parts": rel_parts,
        "has_super": has_super,
        "super_rel": super_rel,
        "super_file": super_file,
        "super_sparse": super_sparse,
    }


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


def run_adb(adb_bin: str, serial: str, args: List[str], timeout: float = 30) -> Tuple[int, str, str]:
    cmd = [adb_bin, "-s", serial] + args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return -1, "", f"{adb_bin} not found: {exc}"


def getprop(adb_bin: str, serial: str, prop: str) -> str:
    rc, out, _ = run_adb(adb_bin, serial, ["shell", "getprop", prop], timeout=15)
    return out.strip() if rc == 0 else ""


def read_device_identity(adb_bin: str, serial: str) -> Dict[str, str]:
    return {
        "model": getprop(adb_bin, serial, "ro.product.model"),
        "device": getprop(adb_bin, serial, "ro.product.device"),
        "product_name": getprop(adb_bin, serial, "ro.product.name"),
    }


def _resolve_explicit_firmware_dirs(explicit: str) -> Tuple[str, str]:
    path = explicit
    if not os.path.isabs(path) and not os.path.exists(path):
        nfs_root = _env("STP_NFS_ROOT", "")
        if nfs_root:
            path = os.path.normpath(os.path.join(nfs_root, path))
    norm = os.path.normpath(path)
    if os.path.basename(norm) == "images":
        return os.path.dirname(norm), norm
    images_sub = os.path.join(norm, "images")
    if os.path.isdir(images_sub) or os.path.isfile(os.path.join(norm, "manifest.json")):
        return norm, images_sub
    if os.path.isfile(os.path.join(norm, "plan.json")):
        return norm, norm
    return norm, images_sub


def _resolve_latest_version(latest: dict, latest_path: str, ident: Dict[str, str], route: dict) -> str:
    if "versions" in latest and latest["versions"] is not None:
        versions_map = latest["versions"]
        if not isinstance(versions_map, dict):
            raise FlashRejectedError(
                f"latest.json key 'versions' must be a JSON object in {latest_path} "
                f"(got {type(versions_map).__name__})",
                route=route,
            )
        norm_versions = {_norm_ident(str(k)): str(v or "").strip() for k, v in versions_map.items()}
        for candidate in (ident["model"], ident["device"], ident["product_name"]):
            c_norm = _norm_ident(candidate)
            if c_norm and norm_versions.get(c_norm):
                return norm_versions[c_norm]
        raise FlashRejectedError(
            f"no target version in {latest_path} versions map for device identity "
            f"(model={ident['model']!r}, device={ident['device']!r}, name={ident['product_name']!r}); "
            f"known models={sorted(versions_map.keys())}",
            route=route,
        )
    version = str(latest.get("version") or "").strip()
    if not version:
        raise FlashRejectedError(
            f"latest.json key 'version' must be a non-empty string in {latest_path}",
            route=route,
        )
    return version


def resolve_firmware_target(args: dict, adb_bin: str, serial: str) -> Tuple[str, str, dict, dict]:
    """Resolve firmware version dir, images dir, validated manifest, and route decision across all 3 routes."""
    explicit = str(args.get("firmware_dir") or "").strip()
    family = str(args.get("family") or "").strip()
    explicit_version = _env("STP_FLASH_FIRMWARE_VERSION", "") or str(args.get("version") or "").strip()

    route = {
        "decided_by": "params" if (explicit or explicit_version) else "fingerprint",
        "model": "",
        "device": "",
        "product_name": "",
        "family": family,
        "version": explicit_version,
        "manifest": "",
    }

    version_dir = ""
    images_dir = ""
    if explicit:
        version_dir, images_dir = _resolve_explicit_firmware_dirs(explicit)
        route["family"] = os.path.basename(os.path.dirname(os.path.abspath(version_dir)))
        route["version"] = os.path.basename(os.path.abspath(version_dir))
        route["manifest"] = os.path.join(version_dir, "manifest.json")

    ident: Dict[str, str] = {"model": "", "device": "", "product_name": ""}
    try:
        for key, prop in (
            ("model", "ro.product.model"),
            ("device", "ro.product.device"),
            ("product_name", "ro.product.name"),
        ):
            val = getprop(adb_bin, serial, prop)
            ident[key] = val
            route[key] = val
    except Exception as exc:
        raise FlashRejectedError(
            f"failed to read device identity via adb ({type(exc).__name__}: {exc})",
            route=route,
        ) from exc

    if not explicit:
        if not family:
            raise FlashRejectedError(
                "parameters must provide non-empty 'firmware_dir' or 'family' (expected non-empty string)",
                route=route,
            )
        fw_root = _env("STP_FLASH_FIRMWARE_ROOT", "") or os.path.join(
            _env("STP_NFS_ROOT", _DEFAULT_NFS_ROOT), "firmware"
        )
        version = explicit_version
        if not version:
            latest_path = os.path.join(fw_root, family, "latest.json")
            latest = _load_json_dict(latest_path, "latest.json", route=route)
            version = _resolve_latest_version(latest, latest_path, ident, route)
        route["version"] = version
        version_dir = os.path.join(fw_root, family, version)
        images_dir = os.path.join(version_dir, "images")
        route["manifest"] = os.path.join(version_dir, "manifest.json")

    manifest_path = route["manifest"]
    manifest = _load_json_dict(manifest_path, "manifest.json", route=route)

    platform = str(manifest.get("platform") or "").strip()
    if platform != "unisoc":
        raise FlashRejectedError(
            f"manifest platform={platform!r} != expected 'unisoc' in {manifest_path}",
            route=route,
        )

    raw_models = manifest.get("models")
    models = [str(m).strip() for m in raw_models if str(m).strip()] if isinstance(raw_models, list) else []
    if not models:
        raise FlashRejectedError(
            f"manifest.json key 'models' must be a non-empty list in {manifest_path} "
            f"(got {type(raw_models).__name__ if 'models' in manifest else 'missing'})",
            route=route,
        )

    device_idents = {_norm_ident(v) for v in ident.values() if v.strip()}
    allowed_idents = {_norm_ident(m) for m in models}
    if not device_idents or not (device_idents & allowed_idents):
        raise FlashRejectedError(
            f"device identity (model={ident['model']!r}, device={ident['device']!r}, "
            f"name={ident['product_name']!r}) does not match manifest models={models} in {manifest_path}",
            route=route,
        )

    target_version = str(manifest.get("version") or "").strip()
    version_prop = str(manifest.get("version_prop") or "").strip()
    if not target_version:
        raise FlashRejectedError(
            f"manifest.json key 'version' must be a non-empty string in {manifest_path}",
            route=route,
        )
    if not version_prop:
        raise FlashRejectedError(
            f"manifest.json key 'version_prop' must be a non-empty string in {manifest_path}",
            route=route,
        )

    return version_dir, images_dir, manifest, route


def resolve_firmware_images_dir(args: dict, adb_bin: str, serial: str) -> str:
    """Resolve and validate the firmware images/ directory via manifest.json + device identity."""
    _, images_dir, _, _ = resolve_firmware_target(args, adb_bin, serial)
    return images_dir


def check_battery(adb_bin: str, serial: str, min_level: int, route: Optional[dict] = None) -> int:
    rc, out, err = run_adb(adb_bin, serial, ["shell", "dumpsys", "battery"], timeout=15)
    m = re.search(r"level:\s*(\d+)", out or "")
    if rc != 0 or not m:
        raise FlashRejectedError(
            f"battery level unreadable (rc={rc}, expected >= {min_level}%, detail={err or out!r})",
            route=route,
        )
    level = int(m.group(1))
    if level < min_level:
        raise FlashRejectedError(
            f"battery {level}% < min {min_level}% — charge before flashing",
            route=route,
        )
    return level


def _sysfs_read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def find_unisoc_download_ports(base: Optional[str] = None) -> List[str]:
    usb_base = _SYSFS_USB_BASE if base is None else base
    names = sorted(os.listdir(usb_base))
    hits: List[str] = []
    for name in names:
        if ":" in name or "-" not in name:
            continue
        dev_dir = os.path.join(usb_base, name)
        vid = _sysfs_read(os.path.join(dev_dir, "idVendor")).lower()
        pid = _sysfs_read(os.path.join(dev_dir, "idProduct")).lower()
        if vid == _UNISOC_DOWNLOAD_VID and pid == _UNISOC_DOWNLOAD_PID:
            hits.append(name)
    return hits


def check_no_unisoc_download_port(base: Optional[str] = None, route: Optional[dict] = None) -> None:
    usb_base = _SYSFS_USB_BASE if base is None else base
    try:
        hits = find_unisoc_download_ports(usb_base)
    except OSError as exc:
        raise FlashRejectedError(
            f"USB bus directory unreadable at {usb_base}: {exc} "
            f"(expected readable sysfs directory with no {_UNISOC_DOWNLOAD_VID}:{_UNISOC_DOWNLOAD_PID})",
            route=route,
        ) from exc
    if hits:
        raise FlashRejectedError(
            f"Unisoc download port {_UNISOC_DOWNLOAD_VID}:{_UNISOC_DOWNLOAD_PID} already present on USB bus "
            f"(ports={hits}, expected none)",
            route=route,
        )


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


def _sha256_file(path: str, limit_bytes: Optional[int] = None) -> str:
    h = hashlib.sha256()
    remaining = limit_bytes
    buf_sz = 8 * 1024 * 1024
    with open(path, "rb") as fh:
        while True:
            to_read = buf_sz if remaining is None else min(buf_sz, remaining)
            if to_read <= 0:
                break
            chunk = fh.read(to_read)
            if not chunk:
                break
            h.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    return h.hexdigest()


def _unlink_if_exists(path: str) -> None:
    try:
        if os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass


def _copy_if_changed(src: str, dst: str, tmp_paths: Optional[List[str]] = None) -> bool:
    if os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src):
        if _sha256_file(dst) == _sha256_file(src):
            return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp_file = f"{dst}.tmp.{os.getpid()}"
    if tmp_paths is not None:
        tmp_paths.append(tmp_file)
    try:
        shutil.copyfile(src, tmp_file)
        os.replace(tmp_file, dst)
    except Exception:
        _unlink_if_exists(tmp_file)
        raise
    return True


def prepare_local_cache(
    fw_dir: str,
    cache_dir: str,
    progress: Progress,
    spec: Optional[Dict[str, object]] = None,
) -> Tuple[str, str]:
    """Cache firmware files and trimmed super image in an isolated SSD directory with SHA-256 verification."""
    if spec is None:
        spec = _load_flash_spec(fw_dir)
    fw_cache_root = resolve_firmware_cache_root(fw_dir, cache_dir)
    progress.stamp(stage="cache-prepare", cache_dir=fw_cache_root)
    local_fw = os.path.join(fw_cache_root, "fw_cache")
    os.makedirs(local_fw, exist_ok=True)

    tmp_paths: List[str] = []
    try:
        copied = 0
        plan_src = os.path.join(fw_dir, "plan.json")
        if _copy_if_changed(plan_src, os.path.join(local_fw, "plan.json"), tmp_paths):
            copied += 1

        fdl1_rel, _ = spec["fdl1"]  # type: ignore[misc]
        fdl2_rel, _ = spec["fdl2"]  # type: ignore[misc]
        userdata_rel = str(spec["userdata_rel"])
        rel_files = [fdl1_rel, fdl2_rel, userdata_rel] + [
            rel for _, rel in spec["rel_parts"]  # type: ignore[attr-defined]
        ]
        seen_rel = set()
        for rel in rel_files:
            if not rel or rel in seen_rel:
                continue
            seen_rel.add(rel)
            src = os.path.join(fw_dir, rel)
            if not os.path.isfile(src):
                raise RuntimeError(f"required firmware file missing: {src}")
            if _copy_if_changed(src, os.path.join(local_fw, rel), tmp_paths):
                copied += 1

        if not spec["has_super"]:
            progress.stamp(stage="cache-ready", copied_files=copied, super_mb=0.0)
            return local_fw, ""

        super_rel = str(spec.get("super_rel") or "").strip()
        super_file = str(spec.get("super_file") or "").strip()
        super_sparse = bool(spec.get("super_sparse"))
        src_super = os.path.join(fw_dir, super_rel)
        if not super_rel or not os.path.isfile(src_super):
            raise RuntimeError(f"required super firmware file missing: {src_super}")

        sparse_super = os.path.join(fw_dir, super_file) if (super_sparse and super_file) else ""
        trimmed_size = (get_sparse_trimmed_size(sparse_super) if sparse_super else None) or os.path.getsize(src_super)
        trimmed_size = min(trimmed_size, os.path.getsize(src_super))
        cached_super = os.path.join(fw_cache_root, f"super_trimmed_{trimmed_size}.img")

        need_rebuild = True
        if os.path.isfile(cached_super) and os.path.getsize(cached_super) == trimmed_size:
            if _sha256_file(cached_super) == _sha256_file(src_super, limit_bytes=trimmed_size):
                need_rebuild = False

        if need_rebuild:
            progress.stamp(stage="cache-trim-super", target_mb=round(trimmed_size / (1024 * 1024), 1))
            tmp_target = f"{cached_super}.tmp.{os.getpid()}"
            tmp_paths.append(tmp_target)
            remaining = trimmed_size
            buf_sz = 16 * 1024 * 1024
            with open(src_super, "rb") as fin, open(tmp_target, "wb") as fout:
                while remaining > 0:
                    chunk = fin.read(min(buf_sz, remaining))
                    if not chunk:
                        break
                    fout.write(chunk)
                    remaining -= len(chunk)
                    if remaining % (256 * 1024 * 1024) == 0:
                        progress.stamp(stage="cache-trim-super", remaining_mb=round(remaining / (1024 * 1024), 1))
            actual = os.path.getsize(tmp_target)
            if actual != trimmed_size:
                raise RuntimeError(f"trimmed super.img size mismatch: got {actual}, expected {trimmed_size}")
            os.replace(tmp_target, cached_super)

        progress.stamp(stage="cache-ready", copied_files=copied, super_mb=round(trimmed_size / (1024 * 1024), 1))
        return local_fw, cached_super
    except Exception:
        for tmp_path in tmp_paths:
            _unlink_if_exists(tmp_path)
        raise


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


def build_spd_dump_args(
    spd_dump_bin: str,
    fw_dir: str,
    super_path: str,
    spec: Optional[Dict[str, object]] = None,
) -> List[str]:
    """Build Scheme 2 spd_dump command line from plan.json, asserting no protected NV partitions are touched."""
    if spec is None:
        spec = _load_flash_spec(fw_dir)
    fdl1_rel, fdl1_base = spec["fdl1"]  # type: ignore[misc]
    fdl2_rel, fdl2_base = spec["fdl2"]  # type: ignore[misc]
    erases: List[str] = list(spec["erases"])  # type: ignore[arg-type]
    fdl1 = os.path.join(fw_dir, fdl1_rel)
    fdl2 = os.path.join(fw_dir, fdl2_rel)
    userdata = os.path.join(fw_dir, str(spec["userdata_rel"]))

    parts_to_flash: List[Tuple[str, str]] = [
        (part, os.path.join(fw_dir, rel))
        for part, rel in spec["rel_parts"]  # type: ignore[attr-defined]
    ]
    if spec["has_super"]:
        parts_to_flash.append(("super", super_path))

    for part in erases:
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
        "fdl", fdl1, fdl1_base,
        "fdl", fdl2, fdl2_base,
        "disable_transcode",
        "timeout", "15000",
        "blk_size", "0x8000",
        "keep_charge", "1",
    ]
    for part in erases:
        cmd.extend(["erase_part", part])
    cmd.extend(["write_part", "userdata", userdata])
    for part, img_path in parts_to_flash:
        cmd.extend(["write_part", part, img_path])
    cmd.append("reset")
    return cmd


def _terminate_and_reap(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass


def run_spd_dump(
    cmd: List[str],
    adb_bin: str,
    serial: str,
    progress: Progress,
    log: list,
    timeout_seconds: float = _DEFAULT_SPD_DUMP_TIMEOUT_SECONDS,
) -> float:
    """Launch spd_dump, reboot device to autodloader, stream PROGRESS stamps, and enforce total timeout."""
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError(f"spd_dump_timeout_seconds must be a finite positive number, got {timeout_seconds!r}")
    t0 = time.time()
    deadline = t0 + float(timeout_seconds)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    tail: List[str] = []
    reader: Optional[threading.Thread] = None

    def _read_stdout() -> None:
        if proc.stdout is None:
            return
        try:
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
        except (OSError, ValueError):
            pass

    try:
        sleep_s = min(1.5, max(0.0, deadline - time.time()))
        if sleep_s > 0:
            try:
                early_rc = proc.wait(timeout=sleep_s)
                raise RuntimeError(
                    f"spd_dump exited prematurely (rc={early_rc}) before adb reboot autodloader"
                )
            except subprocess.TimeoutExpired:
                pass
        early_rc = proc.poll()
        if early_rc is not None:
            raise RuntimeError(
                f"spd_dump exited prematurely (rc={early_rc}) before adb reboot autodloader"
            )
        remaining_before_adb = deadline - time.time()
        if remaining_before_adb <= 0:
            raise RuntimeError(f"spd_dump timed out after {timeout_seconds}s before adb reboot autodloader")

        progress.stamp(stage="autodloader")
        _LAST_REBOOT_SENT_AT["value"] = time.time()
        rc_adb, out_adb, err_adb = run_adb(
            adb_bin, serial, ["reboot", "autodloader"], timeout=min(15.0, remaining_before_adb)
        )
        log.append(f"adb reboot autodloader -> rc={rc_adb} {out_adb} {err_adb}".strip())
        if rc_adb != 0:
            raise RuntimeError(f"adb reboot autodloader failed (rc={rc_adb}): {err_adb or out_adb}")
        if time.time() >= deadline:
            raise RuntimeError(f"spd_dump timed out after {timeout_seconds}s")

        reader = threading.Thread(target=_read_stdout, daemon=True)
        reader.start()

        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise RuntimeError(f"spd_dump timed out after {timeout_seconds}s")
            rc = proc.poll()
            if rc is not None:
                break
            try:
                rc = proc.wait(timeout=min(remaining, 0.2))
                if time.time() >= deadline:
                    raise RuntimeError(f"spd_dump timed out after {timeout_seconds}s")
                break
            except subprocess.TimeoutExpired:
                continue

        reader.join(timeout=2)
        if time.time() >= deadline:
            raise RuntimeError(f"spd_dump timed out after {timeout_seconds}s")
        elapsed = round(time.time() - t0, 1)
        log.extend(tail[-15:])
        if rc != 0:
            raise RuntimeError(f"spd_dump exited with rc={rc} after {elapsed}s: {' | '.join(tail[-5:])}")
        return elapsed
    finally:
        _terminate_and_reap(proc)
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass
        if reader is not None:
            reader.join(timeout=1)


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


def _read_boot_id(adb_bin: str, serial: str) -> str:
    rc, out, _ = run_adb(adb_bin, serial, ["shell", "cat", "/proc/sys/kernel/random/boot_id"], timeout=10)
    if rc != 0:
        return ""
    val = out.strip()
    if not val or "permission denied" in val.lower() or "no such file" in val.lower():
        return ""
    return val


def _read_uptime_seconds_with_ts(adb_bin: str, serial: str) -> Tuple[Optional[float], Optional[float]]:
    rc, out, _ = run_adb(adb_bin, serial, ["shell", "cat", "/proc/uptime"], timeout=10)
    read_at = time.time()
    if rc != 0 or not out.strip():
        return None, None
    try:
        val = float(out.strip().split()[0])
    except (ValueError, IndexError):
        return None, None
    if not math.isfinite(val) or val < 0:
        return None, None
    return val, read_at


def _read_uptime_seconds(adb_bin: str, serial: str) -> Optional[float]:
    val, _ = _read_uptime_seconds_with_ts(adb_bin, serial)
    return val


def _capture_pre_reboot_evidence(adb_bin: str, serial: str) -> dict:
    boot_id = _read_boot_id(adb_bin, serial)
    uptime = _read_uptime_seconds(adb_bin, serial)
    nonce = uuid.uuid4().hex[:16]
    rc_set, _, _ = run_adb(adb_bin, serial, ["shell", "setprop", "debug.stp_bsl_nonce", nonce], timeout=10)
    nonce_set = rc_set == 0 and getprop(adb_bin, serial, "debug.stp_bsl_nonce") == nonce
    return {
        "pre_boot_id": boot_id,
        "pre_uptime": uptime,
        "nonce": nonce if nonce_set else "",
        "nonce_set": nonce_set,
    }


def _verify_post_reboot_evidence(
    adb_bin: str,
    serial: str,
    pre: dict,
    reboot_sent_at: Optional[float] = None,
) -> dict:
    post_boot_id = _read_boot_id(adb_bin, serial)
    post_uptime, post_uptime_ts = _read_uptime_seconds_with_ts(adb_bin, serial)
    rc_nonce, post_nonce_out, _ = run_adb(
        adb_bin, serial, ["shell", "getprop", "debug.stp_bsl_nonce"], timeout=10
    )
    post_nonce = post_nonce_out.strip() if rc_nonce == 0 else None

    pre_boot_id = pre.get("pre_boot_id") or ""
    if pre_boot_id and post_boot_id and pre_boot_id == post_boot_id:
        raise RuntimeError(f"device did not reboot: boot_id unchanged ({pre_boot_id})")

    if pre.get("nonce_set") and post_nonce == pre.get("nonce"):
        raise RuntimeError("device did not reboot: volatile debug.stp_bsl_nonce persisted")

    estimated_boot_at: Optional[float] = None
    if post_uptime is not None and post_uptime_ts is not None:
        estimated_boot_at = post_uptime_ts - post_uptime
        if reboot_sent_at is not None and estimated_boot_at <= reboot_sent_at:
            raise RuntimeError(
                "device did not reboot: estimated boot time "
                f"({estimated_boot_at:.3f}) <= reboot command time ({reboot_sent_at:.3f})"
            )

    boot_id_changed = bool(pre_boot_id and post_boot_id and pre_boot_id != post_boot_id)
    pre_uptime = pre.get("pre_uptime")
    uptime_rebooted = bool(
        pre_uptime is not None
        and estimated_boot_at is not None
        and reboot_sent_at is not None
        and estimated_boot_at > reboot_sent_at
    )

    if not (boot_id_changed or uptime_rebooted):
        raise RuntimeError(
            "no positive evidence that device rebooted "
            f"(boot_id={pre_boot_id!r}->{post_boot_id!r}, uptime={pre_uptime}->{post_uptime}, "
            f"estimated_boot_at={estimated_boot_at}, reboot_sent_at={reboot_sent_at}, "
            f"nonce_set={pre.get('nonce_set')}, post_nonce={post_nonce!r})"
        )

    return {
        "boot_id_changed": boot_id_changed,
        "uptime_rebooted": uptime_rebooted,
        "pre_boot_id": pre_boot_id,
        "post_boot_id": post_boot_id,
        "pre_uptime": pre_uptime,
        "post_uptime": post_uptime,
        "estimated_boot_at": estimated_boot_at,
        "reboot_sent_at": reboot_sent_at,
    }


def run(args: dict, serial: str, progress: Progress, log: list) -> dict:
    t0 = time.time()
    adb_bin = resolve_adb()
    spd_dump_bin = resolve_spd_dump()
    progress.stamp(stage="preflight", serial=serial, spd_dump=spd_dump_bin)

    route: Optional[dict] = None
    try:
        _, fw_dir, manifest, route = resolve_firmware_target(args, adb_bin, serial)
        spec = _load_flash_spec(fw_dir, route=route)

        skip_battery = _bool(args.get("skip_battery"), False)
        min_battery_raw = args.get("battery_min_level")
        battery_min_level = (
            _DEFAULT_BATTERY_MIN_LEVEL
            if min_battery_raw is None or str(min_battery_raw).strip() == ""
            else int(min_battery_raw)
        )
        battery_level: Optional[int] = None
        if not skip_battery:
            battery_level = check_battery(adb_bin, serial, battery_min_level, route=route)

        check_no_unisoc_download_port(route=route)

        spd_dump_timeout = _parse_positive_finite_float(
            args.get("spd_dump_timeout_seconds"),
            "spd_dump_timeout_seconds",
            float(_DEFAULT_SPD_DUMP_TIMEOUT_SECONDS),
            route=route,
        )

        cache_dir = str(args.get("cache_dir") or _DEFAULT_CACHE_DIR).strip()
        pre_build_type = getprop(adb_bin, serial, "ro.build.type")
        pre_incremental = getprop(adb_bin, serial, "ro.build.version.incremental")
        target_build_type = str(
            args.get("verify_build_type") if args.get("verify_build_type") is not None else "userdebug"
        ).strip()
        target_incremental = str(args.get("verify_incremental") or "").strip()

        version_prop = str(manifest["version_prop"]).strip()
        expected_version = str(manifest["version"]).strip()

        fdl1_file, fdl1_base = spec["fdl1"]  # type: ignore[misc]
        fdl2_file, fdl2_base = spec["fdl2"]  # type: ignore[misc]
        partitions = [
            {"partition": "userdata", "image": str(spec["userdata_rel"])},
            *[
                {"partition": part, "image": rel}
                for part, rel in spec["rel_parts"]  # type: ignore[attr-defined]
            ],
        ]
        if spec["has_super"]:
            partitions.append({"partition": "super", "image": str(spec["super_rel"])})

        if _bool(args.get("dry_run"), False):
            dry_super = os.path.join(fw_dir, str(spec["super_rel"])) if spec["has_super"] else ""
            cmd = build_spd_dump_args(spd_dump_bin, fw_dir, dry_super, spec=spec)
            return {
                "metrics": {
                    "dry_run": True,
                    "firmware_dir": fw_dir,
                    "fdl": {
                        "fdl1": {"file": fdl1_file, "base": fdl1_base},
                        "fdl2": {"file": fdl2_file, "base": fdl2_base},
                    },
                    "erases": list(spec["erases"]),  # type: ignore[arg-type]
                    "skipped_erases": list(spec["skipped_erases"]),  # type: ignore[arg-type]
                    "partitions": partitions,
                    "route": route,
                    "version_prop": version_prop,
                    "expected_version": expected_version,
                    "battery_level": battery_level,
                    "pre_build_type": pre_build_type,
                    "pre_incremental": pre_incremental,
                    "spd_dump_cmd_len": len(cmd),
                }
            }

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
                    "route": route,
                },
            }

        lock_timeout = int(args.get("lock_timeout_seconds", 7200))
        lock_fd = acquire_host_lock(progress, timeout_seconds=lock_timeout)
        try:
            # Single parsed spec is reused for both cache preparation and command assembly (J10).
            local_fw, cached_super = prepare_local_cache(fw_dir, cache_dir, progress, spec=spec)
            cmd = build_spd_dump_args(spd_dump_bin, local_fw, cached_super, spec=spec)
            pre_reboot = _capture_pre_reboot_evidence(adb_bin, serial)
            # Final download-port gate after cache preparation and immediately before spd_dump (J7).
            check_no_unisoc_download_port(route=route)
            reboot_anchor = time.time()
            _LAST_REBOOT_SENT_AT["value"] = None
            flash_seconds = run_spd_dump(
                cmd, adb_bin, serial, progress, log, timeout_seconds=spd_dump_timeout
            )
            last_reboot_ts = _LAST_REBOOT_SENT_AT["value"]
            reboot_sent_at = last_reboot_ts if last_reboot_ts is not None else reboot_anchor
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
        reboot_evidence = _verify_post_reboot_evidence(
            adb_bin, serial, pre_reboot, reboot_sent_at=reboot_sent_at
        )

        post_version = getprop(adb_bin, serial, version_prop)
        if post_version != expected_version:
            raise FlashRejectedError(
                f"post-flash {version_prop}={post_version!r} != manifest.version={expected_version!r}",
                route=route,
            )

        post_build_type = getprop(adb_bin, serial, "ro.build.type")
        post_incremental = getprop(adb_bin, serial, "ro.build.version.incremental")
        if target_build_type and post_build_type != target_build_type:
            raise FlashRejectedError(
                f"post-flash ro.build.type={post_build_type!r} != {target_build_type!r}",
                route=route,
            )
        if target_incremental and post_incremental != target_incremental:
            raise FlashRejectedError(
                f"post-flash incremental={post_incremental!r} != {target_incremental!r}",
                route=route,
            )

        root_uid = ""
        if _bool(args.get("verify_root"), True):
            run_adb(adb_bin, serial, ["root"], timeout=15)
            time.sleep(2.0)
            _, root_uid, _ = run_adb(adb_bin, serial, ["shell", "id"], timeout=15)
            if "uid=0(root)" not in root_uid:
                raise FlashRejectedError(f"post-flash adb root failed: {root_uid!r}", route=route)

        return {
            "metrics": {
                "firmware_dir": fw_dir,
                "fdl": {
                    "fdl1": {"file": fdl1_file, "base": fdl1_base},
                    "fdl2": {"file": fdl2_file, "base": fdl2_base},
                },
                "erases": list(spec["erases"]),  # type: ignore[arg-type]
                "skipped_erases": list(spec["skipped_erases"]),  # type: ignore[arg-type]
                "partitions": partitions,
                "route": route,
                "battery_level": battery_level,
                "pre_build_type": pre_build_type,
                "pre_incremental": pre_incremental,
                "post_build_type": post_build_type,
                "post_incremental": post_incremental,
                "version_prop": version_prop,
                "expected_version": expected_version,
                "post_version": post_version,
                "reboot_evidence": reboot_evidence,
                "root_uid": root_uid,
                "flash_duration_seconds": flash_seconds,
                "boot_duration_seconds": boot_seconds,
                "duration_seconds": round(time.time() - t0, 1),
            }
        }
    except Exception as exc:
        if route is not None and not hasattr(exc, "route"):
            try:
                setattr(exc, "route", route)
            except Exception:
                pass
        raise


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
        err_metrics: dict = {"log_tail": log[-10:]}
        route = getattr(exc, "route", None)
        if route is not None:
            err_metrics["route"] = route
        _output(False, error_message=str(exc), metrics=err_metrics)


if __name__ == "__main__":
    main()
