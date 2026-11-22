"""Agent L1 统一固件本地缓存基座（ADR-0064 D2 / B10 U2）。

暴露 ``FIRMWARE_CACHE_API_VERSION`` 与 ``ensure_firmware`` 上下文管理器，供刷机族
在主机锁外调用。摘要契约复用 U1 ``contracts.firmware_manifest``；默认许可客户端
为 U3 ``TransferPermitClient``。
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence

from .aee.paths import (
    _mount_fstype_for_path,
    get_aee_local_root,
    is_ssd_fallback_root,
)
from .config import BASE_DIR
from .contracts.firmware_manifest import (
    PROFILE_MTK,
    PROFILE_UNISOC_BSL,
    PROFILE_UNISOC_FASTBOOT,
    compute_content_digest,
    load_staging_manifest,
    normalize_relpath,
    validate_relpath_within_root,
)
from .settings import get_disk_archive_settings, get_firmware_cache_settings
from .system_monitor import get_disk_usage
from .transfer_permit_client import (
    TransferPermitClient,
    TransferPermitClientError,
    TransferPermitHold,
)

logger = logging.getLogger(__name__)

FIRMWARE_CACHE_API_VERSION = 1

READY_MARKER = ".stp-fw-ready.json"
ENTRY_LOCK_NAME = ".entry.lock"
HOST_TRANSFER_LOCK_NAME = ".host-transfer.lock"
STAGING_DIR_NAME = ".staging"

FORBIDDEN_FSTYPES = frozenset({"nfs", "nfs4", "cifs", "smb3", "tmpfs"})
UNSEALED_COMPAT_PROFILES = frozenset({PROFILE_MTK, PROFILE_UNISOC_FASTBOOT})

CODE_PROFILE_NOT_FOUND = "firmware_profile_not_found"
CODE_UNSEALED_NOT_SUPPORTED = "firmware_unsealed_not_supported"
CODE_INSUFFICIENT_SPACE = "insufficient_local_cache_space"
CODE_BAD_CACHE_ROOT = "firmware_cache_root_rejected"
CODE_READY_MISMATCH = "firmware_cache_ready_mismatch"
CODE_RETRY_EXHAUSTED = "firmware_cache_ensure_retry_exhausted"

ProgressCallback = Callable[..., None]
_SAFE_LABEL_RE = re.compile(r"[^A-Za-z0-9._-]+")


class FirmwareCacheError(Exception):
    """固件缓存结构化失败（携带 ``code``）。"""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


@dataclass
class FirmwareCacheResult:
    """``ensure_firmware`` 产出：本地目录 + 命中/降级元数据。"""

    local_dir: Path
    cache_key: str
    sealed: bool
    content_digest: str
    profile: str
    tier: str  # primary | secondary | nfs_direct
    hit: bool
    total_bytes: int = 0
    fallback_reason: Optional[str] = None
    permit_degraded: bool = False


def _safe_label(source_dir: Path, label: Optional[str] = None) -> str:
    raw = (label or source_dir.name or "firmware").strip() or "firmware"
    cleaned = _SAFE_LABEL_RE.sub("_", raw).strip("._-") or "firmware"
    return cleaned[:64]


def _digest16(content_digest: str) -> str:
    hexpart = content_digest
    if hexpart.startswith("sha256:"):
        hexpart = hexpart[len("sha256:") :]
    return hexpart[:16]


def _install_dir(environ: Optional[Mapping[str, str]] = None) -> Path:
    env = environ if environ is not None else os.environ
    override = (env.get("STP_AGENT_INSTALL_DIR") or "").strip()
    if override:
        return Path(override)
    return Path(BASE_DIR)


def _reject_forbidden_fstype(path: Path) -> None:
    fstype = _mount_fstype_for_path(path)
    if fstype in FORBIDDEN_FSTYPES:
        raise FirmwareCacheError(
            CODE_BAD_CACHE_ROOT,
            f"cache root fstype forbidden: path={path} fstype={fstype}",
        )


def _st_dev(path: Path) -> Optional[int]:
    try:
        return path.resolve(strict=False).stat().st_dev
    except OSError:
        try:
            parent = path.parent
            parent.mkdir(parents=True, exist_ok=True)
            return parent.resolve(strict=False).stat().st_dev
        except OSError:
            return None


def _ensure_writable_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if not os.access(path, os.W_OK):
        raise FirmwareCacheError(
            CODE_BAD_CACHE_ROOT, f"cache root not writable: {path}"
        )
    _reject_forbidden_fstype(path)


def resolve_cache_roots(
    *,
    settings: Optional[Any] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> tuple[Path, Optional[Path]]:
    """返回 ``(primary, secondary|None)``；同 ``st_dev`` / 不可写时跳过二级。"""
    cfg = settings if settings is not None else get_firmware_cache_settings()
    env = environ if environ is not None else os.environ

    primary_raw = (cfg.stp_firmware_cache_dir or "").strip()
    primary = (
        Path(primary_raw)
        if primary_raw
        else _install_dir(env) / "firmware_cache"
    )
    _ensure_writable_dir(primary)

    fallback_raw = (cfg.stp_firmware_cache_fallback_dir or "").strip()
    if fallback_raw:
        secondary_candidate: Optional[Path] = Path(fallback_raw)
    else:
        aee_root = get_aee_local_root()
        secondary_candidate = aee_root.parent / "stp_firmware_cache"

    if secondary_candidate is None:
        return primary, None

    try:
        _ensure_writable_dir(secondary_candidate)
    except FirmwareCacheError:
        logger.info(
            "firmware_cache_secondary_skipped reason=unusable path=%s",
            secondary_candidate,
        )
        return primary, None

    primary_dev = _st_dev(primary)
    secondary_dev = _st_dev(secondary_candidate)
    if (
        primary_dev is not None
        and secondary_dev is not None
        and primary_dev == secondary_dev
    ):
        logger.info(
            "firmware_cache_secondary_skipped reason=same_st_dev path=%s",
            secondary_candidate,
        )
        return primary, None
    if is_ssd_fallback_root(get_aee_local_root()):
        # 纯 SSD fallback 主机：二级默认与一级同盘概率极高；若仍同盘已在上面跳过。
        pass
    return primary, secondary_candidate


def _load_sealed_profile(
    source_dir: Path, profile: str
) -> tuple[bool, Optional[Mapping[str, Any]], Optional[str]]:
    """返回 ``(sealed, profile_obj|None, content_digest|None)``。

    已封包但缺 profile → 抛 ``firmware_profile_not_found``。
    """
    manifest_path = source_dir / "staging_manifest.json"
    if not manifest_path.is_file():
        return False, None, None
    manifest = load_staging_manifest(str(manifest_path))
    profiles = manifest.get("profiles") or {}
    if profile not in profiles:
        raise FirmwareCacheError(
            CODE_PROFILE_NOT_FOUND,
            f"sealed manifest missing profile={profile!r} under {source_dir}",
        )
    profile_obj = profiles[profile]
    for item in profile_obj["files"]:
        validate_relpath_within_root(str(source_dir), item["relpath"])
    return True, profile_obj, str(profile_obj["content_digest"])


def _unsealed_file_fingerprint(source_dir: Path) -> tuple[str, int, list[dict[str, Any]]]:
    """未封包兼容：仅用 ``(relpath, size, mtime_ns)`` 轻量指纹，不读文件内容。"""
    entries: list[list[Any]] = []
    files_meta: list[dict[str, Any]] = []
    root = source_dir.resolve(strict=False)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in (".", ".."))
        for name in sorted(filenames):
            abs_path = Path(dirpath) / name
            try:
                st = abs_path.lstat()
            except OSError:
                continue
            if not abs_path.is_file():
                continue
            rel = abs_path.relative_to(root).as_posix()
            try:
                rel = normalize_relpath(rel)
            except Exception:
                continue
            entries.append([rel, int(st.st_size), int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))])
            files_meta.append(
                {
                    "relpath": rel,
                    "size": int(st.st_size),
                    "mtime_ns": int(
                        getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
                    ),
                }
            )
    blob = json.dumps(entries, ensure_ascii=True, separators=(",", ":")).encode(
        "utf-8"
    )
    digest = "sha256:" + hashlib.sha256(blob).hexdigest()
    total = sum(int(e[1]) for e in entries)
    return digest, total, files_meta


def resolve_cache_identity(
    source_dir: Path, profile: str, *, label: Optional[str] = None
) -> tuple[str, bool, str, int, list[dict[str, Any]]]:
    """返回 ``(cache_key, sealed, content_digest, total_bytes, files_spec)``。"""
    source = Path(source_dir)
    if not source.is_dir():
        raise FirmwareCacheError(
            "firmware_source_missing", f"source_dir not a directory: {source}"
        )
    sealed, profile_obj, digest = _load_sealed_profile(source, profile)
    safe = _safe_label(source, label)
    if sealed and profile_obj is not None and digest is not None:
        files = list(profile_obj["files"])
        total = int(profile_obj["total_bytes"])
        key = f"{safe}_{profile}_{_digest16(digest)}"
        return key, True, digest, total, files

    if profile == PROFILE_UNISOC_BSL:
        raise FirmwareCacheError(
            CODE_UNSEALED_NOT_SUPPORTED,
            f"unsealed source cannot serve profile={profile!r}",
        )
    if profile not in UNSEALED_COMPAT_PROFILES:
        raise FirmwareCacheError(
            CODE_UNSEALED_NOT_SUPPORTED,
            f"unsealed compat unsupported for profile={profile!r}",
        )
    digest, total, files_meta = _unsealed_file_fingerprint(source)
    # 未封包缓存键固定不带 profile，避免同目录多 profile 重复整目录存放；
    # 展锐已封包路径靠 profile 分段隔离。
    key = f"{safe}_unsealed_{_digest16(digest)}"
    # 拷贝清单：整目录相对路径（无 sha，拷贝时现算）
    files = [{"relpath": f["relpath"], "size": f["size"]} for f in files_meta]
    return key, False, digest, total, files


def _open_lock(path: Path, *, create: bool = True) -> int:
    flags = os.O_RDWR | os.O_NOFOLLOW
    if create:
        flags |= os.O_CREAT
    try:
        return os.open(str(path), flags, 0o644)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise FirmwareCacheError(
                CODE_BAD_CACHE_ROOT, f"lock path is a symlink: {path}"
            ) from exc
        raise


def _flock(fd: int, operation: int) -> None:
    fcntl.flock(fd, operation)


def _try_flock_ex_nb(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return False
        raise


def _read_ready(entry_dir: Path) -> Optional[dict[str, Any]]:
    marker = entry_dir / READY_MARKER
    if not marker.is_file():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_ready(entry_dir: Path, payload: Mapping[str, Any]) -> None:
    marker = entry_dir / READY_MARKER
    text = json.dumps(dict(payload), ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    tmp = entry_dir / f"{READY_MARKER}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, marker)


def _touch_last_used(entry_dir: Path) -> None:
    now = time.time()
    try:
        os.utime(entry_dir, (now, now))
    except OSError:
        pass
    marker = entry_dir / READY_MARKER
    if marker.is_file():
        try:
            os.utime(marker, (now, now))
        except OSError:
            pass


def _local_files_match(
    entry_dir: Path, files: Sequence[Mapping[str, Any]], *, sealed: bool
) -> bool:
    for item in files:
        rel = item.get("relpath")
        if not isinstance(rel, str):
            return False
        abs_path = entry_dir / Path(*normalize_relpath(rel).split("/"))
        if not abs_path.is_file():
            return False
        try:
            size = abs_path.stat().st_size
        except OSError:
            return False
        if int(item.get("size", -1)) != int(size):
            return False
        expected_sha = item.get("sha256")
        if sealed and isinstance(expected_sha, str) and expected_sha:
            if _sha256_file(abs_path) != expected_sha:
                return False
    return True


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _best_effort_idle_ioprio() -> None:
    """Linux 下尽力把当前线程 I/O 优先级降到 idle；无权限/非 Linux 不阻断。"""
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        # ioprio_set(IOPRIO_WHO_PROCESS=1, 0, IOPRIO_PRIO_VALUE(IDLE=3, 0))
        IOPRIO_CLASS_IDLE = 3
        ioprio = (IOPRIO_CLASS_IDLE << 13) | 0
        libc.syscall(251, 1, 0, ioprio)  # NR_ioprio_set on x86_64
    except Exception:
        pass


def _dir_total_bytes(root: Path) -> int:
    total = 0
    if not root.is_dir():
        return 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
    return total


def _cache_usage_bytes(cache_root: Path) -> int:
    total = 0
    if not cache_root.is_dir():
        return 0
    for child in cache_root.iterdir():
        if child.name.startswith("."):
            continue
        if child.is_dir():
            total += _dir_total_bytes(child)
    return total


def _space_ok_after(
    cache_root: Path,
    *,
    additional_bytes: int,
    settings: Any,
    aee_root: Path,
) -> bool:
    usage = get_disk_usage(str(cache_root))
    avail = usage.get("avail_gb")
    total_gb = usage.get("total_gb")
    writable_percent = usage.get("writable_percent")
    if avail is None or total_gb is None:
        return False
    add_gb = max(0.0, float(additional_bytes) / (1024**3))
    avail_after = float(avail) - add_gb
    if avail_after < float(settings.stp_firmware_cache_reserve_gb):
        return False
    used_cache_gb = _cache_usage_bytes(cache_root) / (1024**3) + add_gb
    max_by_pct = float(total_gb) * float(settings.stp_firmware_cache_max_pct) / 100.0
    max_allowed = min(float(settings.stp_firmware_cache_max_gb), max_by_pct)
    if used_cache_gb > max_allowed + 1e-9:
        return False
    # 与日志盘同 st_dev：写后 writable_percent 不得超过 spill_threshold - 10%。
    cache_dev = _st_dev(cache_root)
    aee_dev = _st_dev(aee_root)
    if (
        cache_dev is not None
        and aee_dev is not None
        and cache_dev == aee_dev
        and writable_percent is not None
    ):
        spill = float(get_disk_archive_settings().stp_local_disk_spill_threshold)
        # writable_percent_after ≈ 提高 used 占比
        used_gb = float(usage.get("used_gb") or 0.0)
        writable_base = used_gb + float(avail)
        if writable_base > 0:
            writable_after = 100.0 * (used_gb + add_gb) / (writable_base)
        else:
            writable_after = float(writable_percent)
        if writable_after > spill - 10.0 + 1e-9:
            return False
    return True


def _emit_progress(cb: Optional[ProgressCallback], **kwargs: Any) -> None:
    if cb is None:
        return
    try:
        cb(**kwargs)
    except Exception:
        logger.debug("firmware_cache_progress_callback_failed", exc_info=True)


def _copy_files_streaming(
    source_dir: Path,
    dest_dir: Path,
    files: Sequence[Mapping[str, Any]],
    *,
    sealed: bool,
    progress_callback: Optional[ProgressCallback],
    total_bytes: int,
) -> list[dict[str, Any]]:
    """单遍流式拷贝并同步算 sha256；返回带 sha256 的 files 列表。"""
    _best_effort_idle_ioprio()
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_files: list[dict[str, Any]] = []
    copied = 0
    last_cb = time.monotonic()
    for item in files:
        rel = normalize_relpath(str(item["relpath"]))
        src = source_dir / Path(*rel.split("/"))
        dst = dest_dir / Path(*rel.split("/"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256()
        expected_size = int(item.get("size", -1))
        with open(src, "rb") as rf, open(dst, "wb") as wf:
            while True:
                chunk = rf.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
                wf.write(chunk)
                copied += len(chunk)
                now = time.monotonic()
                if now - last_cb >= 2.0:
                    last_cb = now
                    _emit_progress(
                        progress_callback,
                        stage="firmware-stage-copy",
                        bytes_copied=copied,
                        total_bytes=total_bytes,
                        relpath=rel,
                    )
            wf.flush()
            os.fsync(wf.fileno())
        actual_size = dst.stat().st_size
        if expected_size >= 0 and actual_size != expected_size:
            raise FirmwareCacheError(
                CODE_READY_MISMATCH,
                f"size mismatch after copy: {rel} expected={expected_size} actual={actual_size}",
            )
        sha = h.hexdigest()
        expected_sha = item.get("sha256")
        if sealed and isinstance(expected_sha, str) and expected_sha and sha != expected_sha:
            raise FirmwareCacheError(
                CODE_READY_MISMATCH,
                f"sha256 mismatch after copy: {rel}",
            )
        out_files.append({"relpath": rel, "size": actual_size, "sha256": sha})
        _emit_progress(
            progress_callback,
            stage="firmware-stage-copy",
            bytes_copied=copied,
            total_bytes=total_bytes,
            relpath=rel,
        )
    return out_files


def _entry_paths(cache_root: Path, cache_key: str) -> tuple[Path, Path]:
    entry_dir = cache_root / cache_key
    lock_path = entry_dir / ENTRY_LOCK_NAME
    return entry_dir, lock_path


def _list_entry_dirs(cache_root: Path) -> list[Path]:
    if not cache_root.is_dir():
        return []
    out: list[Path] = []
    for child in cache_root.iterdir():
        if child.name.startswith("."):
            continue
        if child.is_dir() and (child / READY_MARKER).is_file():
            out.append(child)
    return out


def _entry_last_used(entry_dir: Path) -> float:
    marker = entry_dir / READY_MARKER
    try:
        return marker.stat().st_mtime
    except OSError:
        try:
            return entry_dir.stat().st_mtime
        except OSError:
            return 0.0


def _evict_unlocked_entries(
    cache_roots: Sequence[Path],
    *,
    need_bytes: int,
    settings: Any,
    aee_root: Path,
    protect_keys: Optional[set[str]] = None,
) -> None:
    """按 last_used 从旧到新非阻塞 EX 腾退；保护键跳过。"""
    protect = protect_keys or set()
    candidates: list[tuple[float, Path]] = []
    for root in cache_roots:
        for entry in _list_entry_dirs(root):
            if entry.name in protect:
                continue
            candidates.append((_entry_last_used(entry), entry))
    candidates.sort(key=lambda x: x[0])
    for _mtime, entry in candidates:
        lock_path = entry / ENTRY_LOCK_NAME
        fd: Optional[int] = None
        try:
            if not lock_path.exists():
                # 无锁文件：仍尝试创建后 NB 获取，避免误删正在写入的目录。
                entry.mkdir(parents=True, exist_ok=True)
            fd = _open_lock(lock_path, create=True)
            if not _try_flock_ex_nb(fd):
                continue
            shutil.rmtree(entry, ignore_errors=True)
            logger.info("firmware_cache_evicted entry=%s", entry)
        except OSError:
            logger.debug("firmware_cache_evict_failed entry=%s", entry, exc_info=True)
        finally:
            if fd is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(fd)
        # 腾退一轮后若目标盘已够则提前停（对每个 root 分别判断由调用方再检）
        if need_bytes <= 0:
            break
        # 若所有 root 都已满足空间则停止
        if all(
            _space_ok_after(
                root, additional_bytes=need_bytes, settings=settings, aee_root=aee_root
            )
            for root in cache_roots
            if root.is_dir()
        ):
            break


def _sweep_staging(cache_root: Path) -> None:
    staging = cache_root / STAGING_DIR_NAME
    if not staging.is_dir():
        return
    for child in list(staging.iterdir()):
        if not child.name.endswith(f".tmp.{os.getpid()}") and ".tmp." in child.name:
            # 无锁残留：直接清扫（staging 不持 entry lock；崩溃遗留可删）
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            except OSError:
                logger.debug("firmware_cache_staging_sweep_failed path=%s", child, exc_info=True)


def _cleanup_pass(manager: "FirmwareCacheManager") -> None:
    settings = get_firmware_cache_settings()
    try:
        primary, secondary = resolve_cache_roots(settings=settings)
    except FirmwareCacheError:
        logger.warning("firmware_cache_cleanup_roots_unavailable", exc_info=True)
        return
    roots = [primary] + ([secondary] if secondary is not None else [])
    aee_root = get_aee_local_root()
    ttl_seconds = float(settings.stp_firmware_cache_ttl_days) * 86400.0
    now = time.time()

    for root in roots:
        _sweep_staging(root)
        for entry in _list_entry_dirs(root):
            age = now - _entry_last_used(entry)
            if age < ttl_seconds:
                continue
            lock_path = entry / ENTRY_LOCK_NAME
            fd: Optional[int] = None
            try:
                fd = _open_lock(lock_path, create=True)
                if not _try_flock_ex_nb(fd):
                    continue
                shutil.rmtree(entry, ignore_errors=True)
                logger.info("firmware_cache_ttl_evicted entry=%s age_s=%.0f", entry, age)
            except OSError:
                logger.debug("firmware_cache_ttl_failed entry=%s", entry, exc_info=True)
            finally:
                if fd is not None:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    except OSError:
                        pass
                    os.close(fd)

        # 水位保护性腾退
        if not _space_ok_after(
            root, additional_bytes=0, settings=settings, aee_root=aee_root
        ):
            _evict_unlocked_entries(
                [root],
                need_bytes=0,
                settings=settings,
                aee_root=aee_root,
            )


class FirmwareCacheManager:
    """进程级单例：后台 TTL/水位清理线程。"""

    _instance: Optional["FirmwareCacheManager"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._configured = False

    @classmethod
    def instance(cls) -> "FirmwareCacheManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def _reset_for_tests(cls) -> None:
        with cls._instance_lock:
            inst = cls._instance
            cls._instance = None
        if inst is not None:
            try:
                inst.stop(timeout=0.5)
            except Exception:
                pass

    def configure(self) -> "FirmwareCacheManager":
        self._configured = True
        return self

    def start(self) -> "FirmwareCacheManager":
        if not self._configured:
            self.configure()
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="firmware-cache-cleanup",
            daemon=True,
        )
        self._thread.start()
        logger.info("firmware_cache_cleanup=started")
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_evt.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None

    def run_cleanup_once(self) -> None:
        """测试/运维：同步跑一轮清理。"""
        _cleanup_pass(self)

    def _loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                _cleanup_pass(self)
            except Exception:
                logger.exception("firmware_cache_cleanup_failed")
            interval = float(
                get_firmware_cache_settings().stp_firmware_cache_cleanup_interval_seconds
            )
            self._stop_evt.wait(timeout=max(60.0, interval))


def _default_permit_client(
    *,
    progress_callback: Optional[ProgressCallback],
    allow_permit_bypass: bool,
) -> TransferPermitClient:
    settings = get_firmware_cache_settings()
    return TransferPermitClient(
        wait_timeout_seconds=settings.stp_firmware_transfer_permit_wait_timeout_seconds,
        unreachable_timeout_seconds=settings.stp_firmware_transfer_unreachable_timeout_seconds,
        allow_permit_bypass=allow_permit_bypass,
        progress_callback=progress_callback,
    )


def _verify_ready_under_sh(
    entry_dir: Path,
    *,
    content_digest: str,
    sealed: bool,
    files: Sequence[Mapping[str, Any]],
) -> bool:
    ready = _read_ready(entry_dir)
    if ready is None:
        return False
    if ready.get("content_digest") != content_digest:
        return False
    ready_files = ready.get("files")
    check_files: Sequence[Mapping[str, Any]]
    if isinstance(ready_files, list) and ready_files:
        check_files = ready_files
    else:
        check_files = files
    return _local_files_match(entry_dir, check_files, sealed=sealed)


def _populate_entry(
    *,
    cache_root: Path,
    host_lock_root: Path,
    entry_dir: Path,
    source_dir: Path,
    cache_key: str,
    profile: str,
    sealed: bool,
    content_digest: str,
    total_bytes: int,
    files: Sequence[Mapping[str, Any]],
    progress_callback: Optional[ProgressCallback],
    permit_client: Any,
    allow_permit_bypass: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """在已持 ``LOCK_EX`` 下完成跨源拷贝；返回 ``(files_with_sha, permit_degraded)``。

    ``.host-transfer.lock`` 固定落在一级缓存根（``host_lock_root``），即使条目写入二级。
    """
    staging_root = cache_root / STAGING_DIR_NAME
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = staging_root / f"{cache_key}.tmp.{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    host_lock_path = host_lock_root / HOST_TRANSFER_LOCK_NAME
    # 固定偏序：已持 entry LOCK_EX → host-transfer（一级根）→ Redis 许可
    host_fd = _open_lock(host_lock_path, create=True)
    permit_hold: Optional[TransferPermitHold] = None
    permit_degraded = False
    try:
        _emit_progress(
            progress_callback,
            stage="firmware-stage-wait",
            wait_for="host-transfer-lock",
            cache_key=cache_key,
        )
        _flock(host_fd, fcntl.LOCK_EX)
        if permit_client is not None:
            permit_hold = permit_client.acquire_permit(
                allow_permit_bypass=allow_permit_bypass
            )
            permit_degraded = bool(getattr(permit_hold, "permit_degraded", False))
        try:
            copied_files = _copy_files_streaming(
                source_dir,
                staging,
                files,
                sealed=sealed,
                progress_callback=progress_callback,
                total_bytes=total_bytes,
            )
            if sealed:
                digest = content_digest
            else:
                digest = compute_content_digest(copied_files)
            ready_payload = {
                "schema_version": 1,
                "profile": profile,
                "sealed": sealed,
                "content_digest": digest if sealed else content_digest,
                "total_bytes": total_bytes,
                "files": copied_files,
            }
            # 未封包：ready 仍记录轻量指纹 content_digest（非文件内容 digest）
            if not sealed:
                ready_payload["content_digest"] = content_digest
            _write_ready(staging, ready_payload)
            if entry_dir.exists():
                shutil.rmtree(entry_dir)
            os.replace(staging, entry_dir)
            staging = None  # replaced
            return copied_files, permit_degraded
        finally:
            if permit_hold is not None:
                permit_hold.release()
    finally:
        try:
            fcntl.flock(host_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(host_fd)
        if staging is not None and Path(staging).exists():
            shutil.rmtree(staging, ignore_errors=True)


def _release_entry_lock(fd: Optional[int]) -> None:
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


def _try_hit_locked(
    *,
    root: Path,
    tier_name: str,
    cache_key: str,
    content_digest: str,
    sealed: bool,
    files: Sequence[Mapping[str, Any]],
    profile: str,
    total_bytes: int,
    progress_callback: Optional[ProgressCallback],
    attempt: int,
) -> tuple[Optional[FirmwareCacheResult], Optional[int]]:
    """尝试 ``LOCK_SH`` 命中；成功返回 ``(result, fd)``（调用方负责释放）。"""
    entry_dir, lock_path = _entry_paths(root, cache_key)
    if not (entry_dir / READY_MARKER).is_file():
        return None, None
    entry_dir.mkdir(parents=True, exist_ok=True)
    fd = _open_lock(lock_path, create=True)
    try:
        _emit_progress(
            progress_callback,
            stage="firmware-stage-wait",
            wait_for="entry-lock-sh",
            cache_key=cache_key,
            attempt=attempt,
        )
        _flock(fd, fcntl.LOCK_SH)
        if not _verify_ready_under_sh(
            entry_dir,
            content_digest=content_digest,
            sealed=sealed,
            files=files,
        ):
            _release_entry_lock(fd)
            return None, None
        _touch_last_used(entry_dir)
        return (
            FirmwareCacheResult(
                local_dir=entry_dir,
                cache_key=cache_key,
                sealed=sealed,
                content_digest=content_digest,
                profile=profile,
                tier=tier_name,
                hit=True,
                total_bytes=total_bytes,
            ),
            fd,
        )
    except Exception:
        _release_entry_lock(fd)
        raise


def _populate_locked(
    *,
    root: Path,
    primary: Path,
    tier_name: str,
    source: Path,
    cache_key: str,
    profile: str,
    sealed: bool,
    content_digest: str,
    total_bytes: int,
    files: Sequence[Mapping[str, Any]],
    progress_callback: Optional[ProgressCallback],
    client: Any,
    allow_permit_bypass: bool,
    attempt: int,
) -> tuple[Optional[FirmwareCacheResult], Optional[int]]:
    """持 ``LOCK_EX`` 备货后降级 ``LOCK_SH``；成功返回 ``(result, fd)``。"""
    entry_dir, lock_path = _entry_paths(root, cache_key)
    entry_dir.mkdir(parents=True, exist_ok=True)
    fd = _open_lock(lock_path, create=True)
    try:
        _emit_progress(
            progress_callback,
            stage="firmware-stage-wait",
            wait_for="entry-lock-ex",
            cache_key=cache_key,
            attempt=attempt,
        )
        _flock(fd, fcntl.LOCK_EX)
        if _verify_ready_under_sh(
            entry_dir,
            content_digest=content_digest,
            sealed=sealed,
            files=files,
        ):
            _flock(fd, fcntl.LOCK_SH)
            if _verify_ready_under_sh(
                entry_dir,
                content_digest=content_digest,
                sealed=sealed,
                files=files,
            ):
                _touch_last_used(entry_dir)
                return (
                    FirmwareCacheResult(
                        local_dir=entry_dir,
                        cache_key=cache_key,
                        sealed=sealed,
                        content_digest=content_digest,
                        profile=profile,
                        tier=tier_name,
                        hit=True,
                        total_bytes=total_bytes,
                    ),
                    fd,
                )
            _flock(fd, fcntl.LOCK_EX)

        copied_files, permit_degraded = _populate_entry(
            cache_root=root,
            host_lock_root=primary,
            entry_dir=entry_dir,
            source_dir=source,
            cache_key=cache_key,
            profile=profile,
            sealed=sealed,
            content_digest=content_digest,
            total_bytes=total_bytes,
            files=files,
            progress_callback=progress_callback,
            permit_client=client,
            allow_permit_bypass=allow_permit_bypass,
        )
        _flock(fd, fcntl.LOCK_SH)
        if not _verify_ready_under_sh(
            entry_dir,
            content_digest=content_digest,
            sealed=sealed,
            files=copied_files,
        ):
            raise FirmwareCacheError(
                CODE_READY_MISMATCH,
                f"ready marker missing after downgrade key={cache_key}",
            )
        _touch_last_used(entry_dir)
        return (
            FirmwareCacheResult(
                local_dir=entry_dir,
                cache_key=cache_key,
                sealed=sealed,
                content_digest=content_digest,
                profile=profile,
                tier=tier_name,
                hit=False,
                total_bytes=total_bytes,
                permit_degraded=permit_degraded,
            ),
            fd,
        )
    except Exception:
        _release_entry_lock(fd)
        raise


@contextmanager
def ensure_firmware(
    source_dir: str | Path,
    profile: str,
    *,
    permit_client: Any = None,
    progress_callback: Optional[ProgressCallback] = None,
    allow_nfs_direct_fallback: bool = False,
    allow_permit_bypass: bool = False,
    label: Optional[str] = None,
    settings: Optional[Any] = None,
    max_retries: int = 3,
) -> Iterator[FirmwareCacheResult]:
    """准备本地固件缓存并在上下文内持有条目 ``LOCK_SH``（NFS 降级除外）。"""
    source = Path(source_dir)
    cfg = settings if settings is not None else get_firmware_cache_settings()
    cache_key, sealed, content_digest, total_bytes, files = resolve_cache_identity(
        source, profile, label=label
    )
    primary, secondary = resolve_cache_roots(settings=cfg)
    roots: list[Path] = [primary] + ([secondary] if secondary is not None else [])
    aee_root = get_aee_local_root()

    client = permit_client
    if client is None:
        client = _default_permit_client(
            progress_callback=progress_callback,
            allow_permit_bypass=allow_permit_bypass,
        )

    last_error: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        held: Optional[tuple[FirmwareCacheResult, int]] = None

        for tier_name, root in (("primary", primary), ("secondary", secondary)):
            if root is None:
                continue
            try:
                result, fd = _try_hit_locked(
                    root=root,
                    tier_name=tier_name,
                    cache_key=cache_key,
                    content_digest=content_digest,
                    sealed=sealed,
                    files=files,
                    profile=profile,
                    total_bytes=total_bytes,
                    progress_callback=progress_callback,
                    attempt=attempt,
                )
            except Exception as exc:
                last_error = exc
                continue
            if result is not None and fd is not None:
                held = (result, fd)
                break

        if held is None:
            for tier_name, root in (("primary", primary), ("secondary", secondary)):
                if root is None:
                    continue
                if not _space_ok_after(
                    root,
                    additional_bytes=total_bytes,
                    settings=cfg,
                    aee_root=aee_root,
                ):
                    _evict_unlocked_entries(
                        roots,
                        need_bytes=total_bytes,
                        settings=cfg,
                        aee_root=aee_root,
                        protect_keys={cache_key},
                    )
                if not _space_ok_after(
                    root,
                    additional_bytes=total_bytes,
                    settings=cfg,
                    aee_root=aee_root,
                ):
                    continue
                try:
                    result, fd = _populate_locked(
                        root=root,
                        primary=primary,
                        tier_name=tier_name,
                        source=source,
                        cache_key=cache_key,
                        profile=profile,
                        sealed=sealed,
                        content_digest=content_digest,
                        total_bytes=total_bytes,
                        files=files,
                        progress_callback=progress_callback,
                        client=client,
                        allow_permit_bypass=allow_permit_bypass,
                        attempt=attempt,
                    )
                except (FirmwareCacheError, TransferPermitClientError):
                    raise
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "firmware_cache_populate_failed tier=%s attempt=%s err=%s",
                        tier_name,
                        attempt,
                        exc,
                        exc_info=True,
                    )
                    continue
                if result is not None and fd is not None:
                    held = (result, fd)
                    break

        if held is None:
            continue

        result, fd = held
        try:
            yield result
            return
        finally:
            _release_entry_lock(fd)

    if allow_nfs_direct_fallback:
        yield FirmwareCacheResult(
            local_dir=source,
            cache_key=cache_key,
            sealed=sealed,
            content_digest=content_digest,
            profile=profile,
            tier="nfs_direct",
            hit=False,
            total_bytes=total_bytes,
            fallback_reason=CODE_INSUFFICIENT_SPACE,
        )
        return

    if last_error is not None and not isinstance(last_error, FirmwareCacheError):
        raise FirmwareCacheError(
            CODE_RETRY_EXHAUSTED, f"ensure failed after retries: {last_error}"
        ) from last_error
    raise FirmwareCacheError(
        CODE_INSUFFICIENT_SPACE,
        f"no local cache root can host key={cache_key} size={total_bytes}",
    )
