#!/usr/bin/env python3
"""Flash one Unisoc (SPRD) device via BSL/FDL2 (spd_dump) using Scheme 2 silent direct boot.

Implements Scheme 2 flashing inside the STP agent script contract:
1. Resolve firmware target (explicit firmware_dir, family+version, or family+latest.json)
   and bind device identity (ro.product.model / device / name) against manifest.json.
2. Load strict flash specification from source images/plan.json (FDL1/FDL2 files & load
   bases, whitelist-filtered EraseFlash2 list, UserData, writable partitions).
3. Enforce pre-flash battery gate (battery_min_level=5, skip_battery=false) and
   target USB port resolution; `dry_run=true` also runs the USB bus 1782:4d00 gate.
4. Ensure Agent L1 firmware cache (profile=unisoc_bsl) outside the host flash lock,
   holding LOCK_SH for the flash lifetime; unsealed targets fail-closed with
   firmware_unsealed_not_supported before any host lock or device state change.
   Local images live at <cache>/images; trimmed super at images/bsl/super_trimmed.img.
5. Acquire host-wide serialization lock (/tmp/stp-flash-firmware.lock, shared with
   flash_firmware and flash_firmware_unisoc), emitting stage=lock-wait PROGRESS stamps
   every 5s so queued devices are not killed by step stall detection.
6. Inside the host lock, verify no residual Unisoc download port (1782:4d00) exists on
   the USB bus, switch device to BSL mode via `adb reboot autodloader`, and execute
   `spd_dump` under a bounded total duration timeout, terminating and reaping the child
   on any failure.
7. Verify positive reboot evidence and read back manifest.version_prop == manifest.version
   after cold boot, plus optional build_type / incremental / root checks.

v1.0.9（2026-11-24 / #3995）：主机锁交接双保险防 `1782:4d00` sysfs 尾影竞态——
`run_spd_dump` 正常返回后释放 `/tmp/stp-flash-firmware.lock` 前等待自身下载口从
sysfs 摘除（上限 10s）；锁内 `check_no_unisoc_download_port` 增加短宽限轮询
（默认 5s，`download_port_clear_wait_seconds` 可配），持续存在仍 fail-closed。

v1.0.8（2026-11-24 / #4066）：移除正常刷机路径锁外 `check_no_unisoc_download_port`
（仅在持主机锁后与 `dry_run` 预检执行），避免同主机 `N > effective_slots` 或跨
PlanRun 重叠排队设备误判同伴正在使用的 `1782:4d00`；`run_spd_dump` 提前启动
stdout/stderr 读取线程，确保 `spd_dump` 早退输出完整进入 `metrics.log_tail`。

v1.0.7（2026-11-23 / #4086 U4 / ADR-0064 D4）：退役 /var/tmp/stp_flash 私有缓存与
主机侧裁剪/锁内拷贝；统一接入 agent.firmware_cache；启动前 fail-closed 校验
FIRMWARE_CACHE_API_VERSION==1。

STP agent script contract:
    env  STP_DEVICE_SERIAL          target serial (required)
         STP_ADB_PATH               default "adb"
         STP_SPD_DUMP_DIR           registered tool package root (requires_tools spd_dump);
                                    must contain an executable spd_dump; no fallback since 1.0.4
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
import math
import os
import re
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

_PROGRESS_PREFIX = "PROGRESS "
_HOST_LOCK_PATH = "/tmp/stp-flash-firmware.lock"
_DEFAULT_NFS_ROOT = "/mnt/stp-aee"
_SYSFS_USB_BASE = "/sys/bus/usb/devices"
_UNISOC_DOWNLOAD_VID = "1782"
_UNISOC_DOWNLOAD_PID = "4d00"
_DEFAULT_BATTERY_MIN_LEVEL = 5
_DEFAULT_SPD_DUMP_TIMEOUT_SECONDS = 3600
_DEFAULT_DOWNLOAD_PORT_CLEAR_WAIT_SECONDS = 5.0
_POST_FLASH_PORT_CLEAR_WAIT_SECONDS = 10.0
_DOWNLOAD_PORT_POLL_INTERVAL = 0.25

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
_USB_PORT_RE = re.compile(r"^[1-9][0-9]*-[1-9][0-9]*(?:\.[1-9][0-9]*){0,6}\Z")
_SPD_OPENED_PORT_RE = re.compile(r"Opened\s+1782:4d00\s+on\s+usb-port\s+([^\s]+)")


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


_AGENT_FW_CACHE_UNAVAILABLE = "agent_firmware_cache_unavailable"


def _load_firmware_cache_api():
    """① 零设备访问启动自检：导入 L1 并校验 API 版本（ADR-0064 D2/D4）。"""
    try:
        from agent.firmware_cache import (  # type: ignore
            FIRMWARE_CACHE_API_VERSION,
            FirmwareCacheError,
            ensure_firmware,
        )
    except Exception:
        try:
            from backend.agent.firmware_cache import (  # type: ignore
                FIRMWARE_CACHE_API_VERSION,
                FirmwareCacheError,
                ensure_firmware,
            )
        except Exception as exc:  # noqa: BLE001 - fail-closed structured error
            return None, None, (
                f"{_AGENT_FW_CACHE_UNAVAILABLE}: import failed ({type(exc).__name__})"
            )
    if FIRMWARE_CACHE_API_VERSION != 1:
        return None, None, (
            f"{_AGENT_FW_CACHE_UNAVAILABLE}: API version "
            f"{FIRMWARE_CACHE_API_VERSION!r} != 1"
        )
    return ensure_firmware, FirmwareCacheError, None


def _firmware_cache_metrics(fw_cache, elapsed_seconds: float) -> dict:
    return {
        "hit": bool(getattr(fw_cache, "hit", False)),
        "elapsed_seconds": round(float(elapsed_seconds), 3),
        "tier": getattr(fw_cache, "tier", None),
        "sealed": bool(getattr(fw_cache, "sealed", False)),
        "cache_key": getattr(fw_cache, "cache_key", None),
        "fallback_reason": getattr(fw_cache, "fallback_reason", None),
        "permit_degraded": bool(getattr(fw_cache, "permit_degraded", False)),
    }


_LAST_REBOOT_SENT_AT: Dict[str, Optional[float]] = {"value": None}


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


def resolve_spd_dump(route: Optional[dict] = None) -> str:
    """L2 (#4006 v1.3): spd_dump 只来自 STP_SPD_DUMP_DIR（注册工具包根）。

    1.0.4 删除 STP_SPD_DUMP_PATH / PATH / 固定路径三种回退；拒绝带路由。
    """
    tool_dir = _env("STP_SPD_DUMP_DIR", "").strip()
    if not tool_dir:
        raise FlashRejectedError(
            "STP_SPD_DUMP_DIR is not set: spd_dump must come from the registered tool "
            "package (requires_tools spd_dump); STP_SPD_DUMP_PATH / PATH / fixed-path "
            "fallbacks were removed in 1.0.4",
            route=route,
        )
    candidate = os.path.join(tool_dir, "spd_dump")
    if not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
        raise FlashRejectedError(
            f"spd_dump not found or not executable under STP_SPD_DUMP_DIR: {candidate!r} "
            "(the tool package root must contain an executable spd_dump)",
            route=route,
        )
    return candidate


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


def check_no_unisoc_download_port(
    base: Optional[str] = None,
    route: Optional[dict] = None,
    *,
    wait_seconds: float = 0.0,
    poll_interval: Optional[float] = None,
) -> None:
    usb_base = _SYSFS_USB_BASE if base is None else base
    interval = _DOWNLOAD_PORT_POLL_INTERVAL if poll_interval is None else poll_interval
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    while True:
        try:
            hits = find_unisoc_download_ports(usb_base)
        except OSError as exc:
            raise FlashRejectedError(
                f"USB bus directory unreadable at {usb_base}: {exc} "
                f"(expected readable sysfs directory with no {_UNISOC_DOWNLOAD_VID}:{_UNISOC_DOWNLOAD_PID})",
                route=route,
            ) from exc
        if not hits:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FlashRejectedError(
                f"Unisoc download port {_UNISOC_DOWNLOAD_VID}:{_UNISOC_DOWNLOAD_PID} already present on USB bus "
                f"(ports={hits}, expected none)",
                route=route,
            )
        time.sleep(min(interval, remaining))


def _wait_download_port_clear(
    usb_port: Optional[str],
    log: list,
    *,
    timeout_seconds: float = _POST_FLASH_PORT_CLEAR_WAIT_SECONDS,
    poll_interval: Optional[float] = None,
    base: Optional[str] = None,
) -> None:
    """Wait briefly inside host lock after `spd_dump reset` for own 1782:4d00 to leave sysfs."""
    usb_base = _SYSFS_USB_BASE if base is None else base
    interval = _DOWNLOAD_PORT_POLL_INTERVAL if poll_interval is None else poll_interval
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        try:
            hits = find_unisoc_download_ports(usb_base)
        except OSError:
            return
        still_present = (usb_port in hits) if usb_port is not None else bool(hits)
        if not still_present:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            log.append(
                f"post-flash 1782:4d00 still visible after {timeout_seconds:.1f}s "
                f"(target={usb_port!r}, hits={hits!r})"
            )
            return
        time.sleep(min(interval, remaining))


def find_usb_ports_for_serial(serial: str, base: Optional[str] = None) -> List[str]:
    """Find sysfs USB port names whose serial attribute matches ``serial``."""
    if not serial:
        return []
    usb_base = _SYSFS_USB_BASE if base is None else base
    names = sorted(os.listdir(usb_base))
    hits: List[str] = []
    for name in names:
        if ":" in name or "-" not in name:
            continue
        dev_serial = _sysfs_read(os.path.join(usb_base, name, "serial"))
        if dev_serial == serial:
            hits.append(name)
    return hits


def resolve_device_usb_port(
    serial: str,
    *,
    require_port_binding: bool = True,
    base: Optional[str] = None,
    route: Optional[dict] = None,
) -> Optional[str]:
    """Resolve and validate the target physical USB port path for ``serial``."""
    usb_base = _SYSFS_USB_BASE if base is None else base
    if require_port_binding and not sys.platform.startswith("linux"):
        raise FlashRejectedError(
            f"require_port_binding=true requires Linux sysfs USB topology (got {sys.platform!r})",
            route=route,
        )
    try:
        hits = find_usb_ports_for_serial(serial, base=usb_base)
    except OSError as exc:
        if not require_port_binding:
            return None
        raise FlashRejectedError(
            f"USB bus directory unreadable at {usb_base}: {exc} "
            f"(cannot resolve target USB port for serial={serial!r})",
            route=route,
        ) from exc

    if len(hits) > 1:
        raise FlashRejectedError(
            f"ambiguous sysfs USB ports {hits} matched serial={serial!r}",
            route=route,
        )
    if not hits:
        if require_port_binding:
            raise FlashRejectedError(
                f"cannot resolve target USB port from sysfs for serial={serial!r}; "
                "refusing unbound spd_dump",
                route=route,
            )
        return None
    port = hits[0]
    if not _USB_PORT_RE.match(port):
        raise FlashRejectedError(
            f"invalid sysfs USB port {port!r} matched serial={serial!r}",
            route=route,
        )
    return port



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
    *,
    usb_port: Optional[str] = None,
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

    cmd: List[str] = [spd_dump_bin]
    if usb_port is not None:
        clean_port = str(usb_port).strip()
        if not _USB_PORT_RE.match(clean_port):
            raise ValueError(f"invalid usb_port {usb_port!r}")
        cmd.extend(["--usb-port", clean_port])
    cmd.extend([
        "--verbose", "1",
        "--wait", "60",
        "keep_charge", "1",
        "fdl", fdl1, fdl1_base,
        "fdl", fdl2, fdl2_base,
        "disable_transcode",
        "timeout", "15000",
        "blk_size", "0x8000",
        "keep_charge", "1",
    ])
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
    expected_usb_port: Optional[str] = None,
) -> float:
    """Launch spd_dump, reboot device to autodloader, stream PROGRESS stamps, and enforce total timeout."""
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError(f"spd_dump_timeout_seconds must be a finite positive number, got {timeout_seconds!r}")
    # J8 (#4006 v1.2): stage duration must use the host monotonic clock so a wall-clock
    # rollback inside the stage cannot extend the deadline.
    t0 = time.monotonic()
    deadline = t0 + float(timeout_seconds)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    tail: List[str] = []
    opened_ports: List[str] = []
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
                pm = _SPD_OPENED_PORT_RE.search(line)
                if pm:
                    opened_ports.append(pm.group(1))
                    progress.stamp(stage="bsl-flash", detail=line[:120], elapsed_s=round(time.monotonic() - t0, 1))
                    continue
                m = _LOAD_PART_RE.search(line)
                if m:
                    progress.stamp(stage="bsl-flash", partition=m.group(1), elapsed_s=round(time.monotonic() - t0, 1))
                elif any(tag in line for tag in ("BSL_REP_VER", "FDL2", "erase_partition", "RESET")):
                    progress.stamp(stage="bsl-flash", detail=line[:120], elapsed_s=round(time.monotonic() - t0, 1))
        except (OSError, ValueError):
            pass

    def _flush_early_tail() -> str:
        if reader is not None:
            reader.join(timeout=2)
        if tail:
            log.extend(tail[-15:])
            return f": {' | '.join(tail[-5:])}"
        return ""

    try:
        # #4066: start stdout/stderr reader before startup wait so an early-exiting
        # spd_dump (e.g. missing shared library) is captured into metrics.log_tail.
        reader = threading.Thread(target=_read_stdout, daemon=True)
        reader.start()

        sleep_s = min(1.5, max(0.0, deadline - time.monotonic()))
        if sleep_s > 0:
            try:
                early_rc = proc.wait(timeout=sleep_s)
                detail = _flush_early_tail()
                raise RuntimeError(
                    f"spd_dump exited prematurely (rc={early_rc}) before adb reboot autodloader{detail}"
                )
            except subprocess.TimeoutExpired:
                pass
        early_rc = proc.poll()
        if early_rc is not None:
            detail = _flush_early_tail()
            raise RuntimeError(
                f"spd_dump exited prematurely (rc={early_rc}) before adb reboot autodloader{detail}"
            )
        remaining_before_adb = deadline - time.monotonic()
        if remaining_before_adb <= 0:
            raise RuntimeError(f"spd_dump timed out after {timeout_seconds}s before adb reboot autodloader")

        progress.stamp(stage="autodloader")
        # J8 (#4006 v1.2): the poll below is the last step before sending the reboot —
        # no I/O (including PROGRESS output) or waiting may happen between this check and
        # the run_adb call, so a spd_dump that exits during the stamp cannot receive a
        # reboot into download mode without a flasher waiting.
        final_rc = proc.poll()
        if final_rc is not None:
            detail = _flush_early_tail()
            raise RuntimeError(
                f"spd_dump exited before adb reboot autodloader was sent (rc={final_rc}){detail}"
            )
        _LAST_REBOOT_SENT_AT["value"] = time.monotonic()
        rc_adb, out_adb, err_adb = run_adb(
            adb_bin, serial, ["reboot", "autodloader"], timeout=min(15.0, remaining_before_adb)
        )
        log.append(f"adb reboot autodloader -> rc={rc_adb} {out_adb} {err_adb}".strip())
        if rc_adb != 0:
            raise RuntimeError(f"adb reboot autodloader failed (rc={rc_adb}): {err_adb or out_adb}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"spd_dump timed out after {timeout_seconds}s")

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"spd_dump timed out after {timeout_seconds}s")
            rc = proc.poll()
            if rc is not None:
                break
            try:
                rc = proc.wait(timeout=min(remaining, 0.2))
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"spd_dump timed out after {timeout_seconds}s")
                break
            except subprocess.TimeoutExpired:
                continue

        reader.join(timeout=2)
        if time.monotonic() >= deadline:
            raise RuntimeError(f"spd_dump timed out after {timeout_seconds}s")
        elapsed = round(time.monotonic() - t0, 1)
        log.extend(tail[-15:])
        if rc != 0:
            raise RuntimeError(f"spd_dump exited with rc={rc} after {elapsed}s: {' | '.join(tail[-5:])}")
        if expected_usb_port is not None:
            if not opened_ports:
                raise RuntimeError(
                    f"spd_dump exited 0 without USB port binding echo (expected usb-port {expected_usb_port!r})"
                )
            if any(p != expected_usb_port for p in opened_ports):
                raise RuntimeError(
                    f"spd_dump bound unexpected USB port {opened_ports!r} != expected {expected_usb_port!r}"
                )
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


def _read_uptime_with_window(adb_bin: str, serial: str) -> Tuple[Optional[float], float, float]:
    """Read /proc/uptime once, returning (u, T0, T1) per J9 (#4006 v1.2).

    T0 is taken from the host monotonic clock immediately before the read command is
    issued and T1 immediately after it returns. The device samples u at some moment
    inside [T0, T1], so its boot moment lies within [T0 - u, T1 - u]. Read failure,
    timeout, empty or non-finite output yields u=None (no evidence).
    """
    t0 = time.monotonic()
    rc, out, _ = run_adb(adb_bin, serial, ["shell", "cat", "/proc/uptime"], timeout=10)
    t1 = time.monotonic()
    if rc != 0 or not out.strip():
        return None, t0, t1
    try:
        val = float(out.strip().split()[0])
    except (ValueError, IndexError):
        return None, t0, t1
    if not math.isfinite(val) or val < 0:
        return None, t0, t1
    return val, t0, t1


def _capture_pre_reboot_evidence(adb_bin: str, serial: str) -> dict:
    """Capture pre-flash reboot evidence (J9 #4006 v1.2).

    Only /proc/sys/kernel/random/boot_id is read here. Volatile properties are neither
    evidence nor veto, and uptime evidence only needs one successful post-flash read.
    """
    return {"pre_boot_id": _read_boot_id(adb_bin, serial)}


def _verify_post_reboot_evidence(
    adb_bin: str,
    serial: str,
    pre: dict,
    reboot_sent_at: Optional[float] = None,
) -> dict:
    """Verify reboot evidence per J9 (#4006 v1.2).

    R is the host monotonic moment taken before `adb reboot autodloader` was sent.
    /proc/uptime is read once after the flash: T0 before, T1 after (monotonic), u is
    the read value, so the device boot moment lies in [T0-u, T1-u].

    Positive evidence (only): boot_id read successfully before and after and different;
    or T0-u later than R. Veto: boot_id read successfully both times and identical;
    or T1-u not later than R. Any veto, or no positive evidence at all, is a failure.
    When T0-u is not later than R but T1-u is, uptime provides no evidence (adb return
    latency cannot make an unrebooted device pass).
    """
    pre_boot_id = pre.get("pre_boot_id") or ""
    post_boot_id = _read_boot_id(adb_bin, serial)
    post_uptime, uptime_t0, uptime_t1 = _read_uptime_with_window(adb_bin, serial)

    boot_id_both_read = bool(pre_boot_id) and bool(post_boot_id)
    boot_id_same = boot_id_both_read and pre_boot_id == post_boot_id
    boot_id_changed = boot_id_both_read and pre_boot_id != post_boot_id

    uptime_positive = False
    uptime_veto = False
    if post_uptime is not None and reboot_sent_at is not None:
        if uptime_t0 - post_uptime > reboot_sent_at:
            uptime_positive = True
        if uptime_t1 - post_uptime <= reboot_sent_at:
            uptime_veto = True

    if boot_id_same:
        raise RuntimeError(f"device did not reboot: boot_id unchanged ({pre_boot_id})")
    if uptime_veto:
        raise RuntimeError(
            "device did not reboot: uptime readback contradicts a reboot after the command "
            f"(boot moment no later than {uptime_t1 - post_uptime:.3f} on the monotonic clock, "
            f"reboot sent at {reboot_sent_at:.3f}, uptime={post_uptime})"
        )
    if not (boot_id_changed or uptime_positive):
        raise RuntimeError(
            "no positive evidence that device rebooted "
            f"(boot_id={pre_boot_id!r}->{post_boot_id!r}, uptime={post_uptime!r}, "
            f"uptime_window=[{uptime_t0:.3f}, {uptime_t1:.3f}], reboot_sent_at={reboot_sent_at})"
        )

    return {
        "boot_id_changed": boot_id_changed,
        "uptime_positive": uptime_positive,
        "pre_boot_id": pre_boot_id,
        "post_boot_id": post_boot_id,
        "post_uptime": post_uptime,
        "uptime_read_window": {"t0": uptime_t0, "t1": uptime_t1},
        "boot_moment_bounds": (
            [uptime_t0 - post_uptime, uptime_t1 - post_uptime]
            if post_uptime is not None
            else None
        ),
        "reboot_sent_at": reboot_sent_at,
    }


def run(args: dict, serial: str, progress: Progress, log: list) -> dict:
    t0 = time.time()
    # ① 零设备访问启动自检（任何 getprop / 主机锁之前）
    ensure_firmware, FirmwareCacheError, api_err = _load_firmware_cache_api()
    if api_err is not None:
        raise FlashRejectedError(_AGENT_FW_CACHE_UNAVAILABLE)

    adb_bin = resolve_adb()

    route: Optional[dict] = None
    try:
        version_dir, fw_dir, manifest, route = resolve_firmware_target(args, adb_bin, serial)
        # L2 (#4006 v1.3): 工具路径解析放在路由建立之后——找不到工具的拒绝结果必须带路由。
        spd_dump_bin = resolve_spd_dump(route)
        progress.stamp(stage="preflight", serial=serial, spd_dump=spd_dump_bin)
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

        is_dry_run = _bool(args.get("dry_run"), False)
        if is_dry_run:
            # #4066: dry_run does not acquire the host lock; normal flashing enforces
            # the 1782:4d00 gate inside /tmp/stp-flash-firmware.lock so queued siblings
            # do not fail on a peer legitimately flashing on the same host.
            check_no_unisoc_download_port(route=route)

        raw_require_port = args.get("require_port_binding")
        if raw_require_port is None or str(raw_require_port).strip() == "":
            raw_require_port = _env("STP_FLASH_REQUIRE_PORT_BINDING", "")
        require_port_binding = _bool(raw_require_port, True)
        usb_port = resolve_device_usb_port(
            serial,
            require_port_binding=require_port_binding,
            route=route,
        )

        spd_dump_timeout = _parse_positive_finite_float(
            args.get("spd_dump_timeout_seconds"),
            "spd_dump_timeout_seconds",
            float(_DEFAULT_SPD_DUMP_TIMEOUT_SECONDS),
            route=route,
        )

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
            partitions.append({"partition": "super", "image": "bsl/super_trimmed.img"})

        if is_dry_run:
            dry_super = (
                os.path.join(fw_dir, "bsl", "super_trimmed.img")
                if spec["has_super"]
                else ""
            )
            cmd = build_spd_dump_args(spd_dump_bin, fw_dir, dry_super, spec=spec, usb_port=usb_port)
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
                    "usb_port": usb_port,
                    "require_port_binding": require_port_binding,
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

        allow_nfs = _bool(args.get("allow_nfs_direct_fallback"), False)
        allow_bypass = _bool(args.get("allow_permit_bypass"), False)
        lock_timeout = int(args.get("lock_timeout_seconds", 7200))
        cache_t0 = time.time()
        try:
            # ③ 锁外缓存准备与封包校验；未封包 BSL 在此 fail-closed
            with ensure_firmware(
                version_dir,
                profile="unisoc_bsl",
                progress_callback=progress.stamp,
                allow_nfs_direct_fallback=allow_nfs,
                allow_permit_bypass=allow_bypass,
                label=str(route.get("version") or "") or None,
            ) as fw_cache:
                fc_metrics = _firmware_cache_metrics(fw_cache, time.time() - cache_t0)
                local_images = os.path.join(str(fw_cache.local_dir), "images")
                cached_super = ""
                if spec["has_super"]:
                    cached_super = os.path.join(local_images, "bsl", "super_trimmed.img")
                    if not os.path.isfile(cached_super):
                        raise FlashRejectedError(
                            f"sealed BSL cache missing trimmed super: {cached_super}",
                            route=route,
                        )

                # ④ 持 LOCK_SH 后再取主机刷机排他锁
                lock_fd = acquire_host_lock(progress, timeout_seconds=lock_timeout)
                try:
                    live_usb_port = resolve_device_usb_port(
                        serial,
                        require_port_binding=require_port_binding,
                        route=route,
                    )
                    if live_usb_port != usb_port:
                        raise FlashRejectedError(
                            f"target USB port changed while waiting for host lock: "
                            f"{usb_port!r} -> {live_usb_port!r}",
                            route=route,
                        )
                    cmd = build_spd_dump_args(
                        spd_dump_bin, local_images, cached_super, spec=spec, usb_port=usb_port
                    )
                    pre_reboot = _capture_pre_reboot_evidence(adb_bin, serial)
                    raw_clear_wait = args.get("download_port_clear_wait_seconds")
                    if raw_clear_wait is None or str(raw_clear_wait).strip() == "":
                        clear_wait = _DEFAULT_DOWNLOAD_PORT_CLEAR_WAIT_SECONDS
                    else:
                        clear_wait = max(0.0, float(raw_clear_wait))
                    check_no_unisoc_download_port(route=route, wait_seconds=clear_wait)
                    reboot_anchor = time.monotonic()
                    _LAST_REBOOT_SENT_AT["value"] = None
                    flash_seconds = (
                        run_spd_dump(
                            cmd,
                            adb_bin,
                            serial,
                            progress,
                            log,
                            timeout_seconds=spd_dump_timeout,
                            expected_usb_port=usb_port,
                        )
                        if usb_port is not None
                        else run_spd_dump(
                            cmd,
                            adb_bin,
                            serial,
                            progress,
                            log,
                            timeout_seconds=spd_dump_timeout,
                        )
                    )
                    last_reboot_ts = _LAST_REBOOT_SENT_AT["value"]
                    reboot_sent_at = (
                        last_reboot_ts if last_reboot_ts is not None else reboot_anchor
                    )
                    _wait_download_port_clear(usb_port, log)
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
                        f"post-flash {version_prop}={post_version!r} != "
                        f"manifest.version={expected_version!r}",
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
                        raise FlashRejectedError(
                            f"post-flash adb root failed: {root_uid!r}", route=route
                        )

                return {
                    "metrics": {
                        "firmware_dir": local_images,
                        "source_firmware_dir": fw_dir,
                        "fdl": {
                            "fdl1": {"file": fdl1_file, "base": fdl1_base},
                            "fdl2": {"file": fdl2_file, "base": fdl2_base},
                        },
                        "erases": list(spec["erases"]),  # type: ignore[arg-type]
                        "skipped_erases": list(spec["skipped_erases"]),  # type: ignore[arg-type]
                        "partitions": partitions,
                        "route": route,
                        "usb_port": usb_port,
                        "require_port_binding": require_port_binding,
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
                        "firmware_cache": fc_metrics,
                    }
                }
        except FirmwareCacheError as exc:
            code = getattr(exc, "code", None) or str(exc)
            raise FlashRejectedError(code, route=route) from exc
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
                # v1.0.7：私有 /var/tmp/stp_flash 已退役；保留 CLI 兼容但忽略。
                i += 2
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
        msg = str(exc)
        if _AGENT_FW_CACHE_UNAVAILABLE in msg:
            msg = _AGENT_FW_CACHE_UNAVAILABLE
        _output(False, error_message=msg, metrics=err_metrics)


if __name__ == "__main__":
    main()
