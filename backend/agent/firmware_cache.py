"""Agent L1 统一固件本地缓存基座（ADR-0064 D2 / B10 U2；复核 M1–M7 收口）。

暴露 ``FIRMWARE_CACHE_API_VERSION`` 与 ``ensure_firmware`` 上下文管理器。
摘要复用 U1 ``contracts.firmware_manifest``；默认许可客户端为 U3
``TransferPermitClient``。
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
import stat
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
LOCKS_DIR_NAME = ".locks"
STAGING_DIR_NAME = ".staging"
HOST_TRANSFER_LOCK_BASENAME = ".stp_firmware_host_transfer.lock"

FORBIDDEN_FSTYPES = frozenset({"nfs", "nfs4", "cifs", "smb3", "tmpfs"})
UNSEALED_COMPAT_PROFILES = frozenset({PROFILE_MTK, PROFILE_UNISOC_FASTBOOT})

CODE_PROFILE_NOT_FOUND = "firmware_profile_not_found"
CODE_UNSEALED_NOT_SUPPORTED = "firmware_unsealed_not_supported"
CODE_INSUFFICIENT_SPACE = "insufficient_local_cache_space"
CODE_BAD_CACHE_ROOT = "firmware_cache_root_rejected"
CODE_READY_MISMATCH = "firmware_cache_ready_mismatch"
CODE_DOWNGRADE_RACE = "firmware_cache_downgrade_race"
CODE_RETRY_EXHAUSTED = "firmware_cache_ensure_retry_exhausted"
CODE_COPY_IO = "firmware_cache_copy_io_error"
CODE_ENUM_FAILED = "firmware_unsealed_enumerate_failed"
CODE_PATH_ESCAPE = "firmware_cache_path_escape"

ProgressCallback = Callable[..., None]
_SAFE_LABEL_RE = re.compile(r"[^A-Za-z0-9._-]+")
_LOCK_WAIT_POLL_SECONDS = 0.25


class FirmwareCacheError(Exception):
    """固件缓存结构化失败（携带 ``code``；``retryable`` 控制外层重入）。"""

    def __init__(
        self, code: str, message: str = "", *, retryable: bool = False
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.retryable = bool(retryable)


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


def host_transfer_lock_path(
    environ: Optional[Mapping[str, str]] = None,
) -> Path:
    """主机级跨源传输锁——独立于可热切的 ``STP_FIRMWARE_CACHE_DIR``（M7）。"""
    return _install_dir(environ) / HOST_TRANSFER_LOCK_BASENAME


def _probe_fstype(path: Path) -> Optional[str]:
    """对尚不存在的路径，沿父目录探测挂载 fstype（拒绝前不得 mkdir）。"""
    cursor = path
    for _ in range(64):
        fstype = _mount_fstype_for_path(cursor)
        if fstype is not None:
            return fstype
        if cursor.parent == cursor:
            break
        if cursor.exists():
            return _mount_fstype_for_path(cursor)
        cursor = cursor.parent
    return _mount_fstype_for_path(path)


def _reject_forbidden_fstype(path: Path, *, create: bool) -> None:
    """M5：先校验 fstype；``create=False`` 时绝不创建目录。"""
    fstype = _probe_fstype(path)
    if fstype in FORBIDDEN_FSTYPES:
        raise FirmwareCacheError(
            CODE_BAD_CACHE_ROOT,
            f"cache root fstype forbidden: path={path} fstype={fstype}",
        )
    if create:
        path.mkdir(parents=True, exist_ok=True)
        if not os.access(path, os.W_OK):
            raise FirmwareCacheError(
                CODE_BAD_CACHE_ROOT, f"cache root not writable: {path}"
            )
        # 创建后再核一次（防跨挂载边界）
        fstype2 = _mount_fstype_for_path(path)
        if fstype2 in FORBIDDEN_FSTYPES:
            raise FirmwareCacheError(
                CODE_BAD_CACHE_ROOT,
                f"cache root fstype forbidden after create: path={path} fstype={fstype2}",
            )


def _st_dev(path: Path) -> Optional[int]:
    """只读探测 ``st_dev``；不得为探测而创建目录（M5）。"""
    try:
        if path.exists():
            return path.resolve(strict=False).stat().st_dev
    except OSError:
        pass
    try:
        parent = path.parent
        if parent.exists():
            return parent.resolve(strict=False).stat().st_dev
    except OSError:
        pass
    return None


def resolve_cache_roots(
    *,
    settings: Optional[Any] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> tuple[Path, Optional[Path]]:
    """返回 ``(primary, secondary|None)``。

    M5：禁用 FS 在 mkdir 前拒绝；二级 PermissionError / ``st_dev=None`` 跳过二级。
    """
    cfg = settings if settings is not None else get_firmware_cache_settings()
    env = environ if environ is not None else os.environ

    primary_raw = (cfg.stp_firmware_cache_dir or "").strip()
    primary = (
        Path(primary_raw)
        if primary_raw
        else _install_dir(env) / "firmware_cache"
    )
    _reject_forbidden_fstype(primary, create=True)
    (primary / LOCKS_DIR_NAME).mkdir(parents=True, exist_ok=True)
    (primary / STAGING_DIR_NAME).mkdir(parents=True, exist_ok=True)

    fallback_raw = (cfg.stp_firmware_cache_fallback_dir or "").strip()
    if fallback_raw:
        secondary_candidate: Optional[Path] = Path(fallback_raw)
    else:
        aee_root = get_aee_local_root()
        secondary_candidate = aee_root.parent / "stp_firmware_cache"

    if secondary_candidate is None:
        return primary, None

    try:
        _reject_forbidden_fstype(secondary_candidate, create=False)
        secondary_candidate.mkdir(parents=True, exist_ok=True)
        if not os.access(secondary_candidate, os.W_OK):
            raise PermissionError(f"not writable: {secondary_candidate}")
        fstype = _mount_fstype_for_path(secondary_candidate)
        if fstype in FORBIDDEN_FSTYPES:
            raise FirmwareCacheError(
                CODE_BAD_CACHE_ROOT,
                f"secondary fstype forbidden: {fstype}",
            )
    except FirmwareCacheError:
        # 二级禁用 FS：跳过二级，不阻断已健康的一级。
        logger.info(
            "firmware_cache_secondary_skipped reason=forbidden_fs path=%s",
            secondary_candidate,
        )
        return primary, None
    except (PermissionError, OSError) as exc:
        logger.info(
            "firmware_cache_secondary_skipped reason=unusable path=%s err=%s",
            secondary_candidate,
            exc,
        )
        return primary, None

    primary_dev = _st_dev(primary)
    secondary_dev = _st_dev(secondary_candidate)
    if secondary_dev is None or primary_dev is None:
        logger.info(
            "firmware_cache_secondary_skipped reason=unknown_st_dev path=%s",
            secondary_candidate,
        )
        return primary, None
    if primary_dev == secondary_dev:
        logger.info(
            "firmware_cache_secondary_skipped reason=same_st_dev path=%s",
            secondary_candidate,
        )
        return primary, None
    if is_ssd_fallback_root(get_aee_local_root()) and primary_dev == secondary_dev:
        return primary, None

    # M5：二级 .locks / .staging 初始化失败 → 跳过二级，不阻断一级。
    try:
        (secondary_candidate / LOCKS_DIR_NAME).mkdir(parents=True, exist_ok=True)
        (secondary_candidate / STAGING_DIR_NAME).mkdir(parents=True, exist_ok=True)
        if not os.access(secondary_candidate / LOCKS_DIR_NAME, os.W_OK):
            raise PermissionError("secondary .locks not writable")
        if not os.access(secondary_candidate / STAGING_DIR_NAME, os.W_OK):
            raise PermissionError("secondary .staging not writable")
    except (PermissionError, OSError) as exc:
        logger.info(
            "firmware_cache_secondary_skipped reason=subdir_unusable path=%s err=%s",
            secondary_candidate,
            exc,
        )
        return primary, None
    return primary, secondary_candidate


def _validate_all_manifest_paths(source_dir: Path, manifest: Mapping[str, Any]) -> None:
    """D1 #5 / M6：整份清单所有 profile 的路径/软链边界均须合法。"""
    profiles = manifest.get("profiles") or {}
    if not isinstance(profiles, Mapping):
        raise FirmwareCacheError(CODE_BAD_CACHE_ROOT, "manifest profiles missing")
    for name, profile in profiles.items():
        files = profile.get("files") if isinstance(profile, Mapping) else None
        if not isinstance(files, list):
            raise FirmwareCacheError(
                CODE_BAD_CACHE_ROOT, f"profile {name!r} files missing"
            )
        for item in files:
            rel = item.get("relpath") if isinstance(item, Mapping) else None
            if not isinstance(rel, str):
                raise FirmwareCacheError(
                    CODE_BAD_CACHE_ROOT, f"profile {name!r} has invalid relpath"
                )
            validate_relpath_within_root(str(source_dir), rel)


def _load_sealed_profile(
    source_dir: Path, profile: str
) -> tuple[bool, Optional[Mapping[str, Any]], Optional[str]]:
    """返回 ``(sealed, profile_obj|None, content_digest|None)``。"""
    manifest_path = source_dir / "staging_manifest.json"
    if not manifest_path.is_file():
        return False, None, None
    manifest = load_staging_manifest(str(manifest_path))
    _validate_all_manifest_paths(source_dir, manifest)
    profiles = manifest.get("profiles") or {}
    if profile not in profiles:
        raise FirmwareCacheError(
            CODE_PROFILE_NOT_FOUND,
            f"sealed manifest missing profile={profile!r} under {source_dir}",
        )
    profile_obj = profiles[profile]
    return True, profile_obj, str(profile_obj["content_digest"])


def _unsealed_file_fingerprint(
    source_dir: Path,
) -> tuple[str, int, list[dict[str, Any]]]:
    """未封包兼容：显式递归 ``os.scandir``，不吞任何 ``OSError``（#4086 v1.6）。

    目录打开 / 条目类型判定 / ``lstat`` / 根内文件软链 ``stat`` 任一失败立即
    ``firmware_unsealed_enumerate_failed``；根内指向普通文件的软链按目标
    size/mtime 纳入指纹（ADR-0064 D1 #6），越界/悬空/目录软链 fail-closed。
    """
    entries: list[list[Any]] = []
    files_meta: list[dict[str, Any]] = []
    root = source_dir.resolve(strict=False)
    root_real = os.path.realpath(str(source_dir))

    def _enum_failed(message: str, exc: Optional[BaseException] = None) -> None:
        err = FirmwareCacheError(CODE_ENUM_FAILED, message)
        if exc is not None:
            raise err from exc
        raise err

    def _append_file(abs_path: Path, st: os.stat_result) -> None:
        rel = abs_path.relative_to(root).as_posix()
        rel = normalize_relpath(rel)
        mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
        entries.append([rel, int(st.st_size), mtime_ns])
        files_meta.append(
            {"relpath": rel, "size": int(st.st_size), "mtime_ns": mtime_ns}
        )

    def _walk(current: Path) -> None:
        try:
            scandir_iter = os.scandir(current)
        except OSError as exc:
            _enum_failed(
                f"scandir failed during unsealed enumerate: {current}: {exc}",
                exc,
            )
            return  # pragma: no cover — _enum_failed always raises
        assert scandir_iter is not None
        with scandir_iter as it:
            children: list[Any] = []
            try:
                for entry in it:
                    children.append(entry)
            except OSError as exc:
                _enum_failed(
                    f"scandir iteration failed during unsealed enumerate: "
                    f"{current}: {exc}",
                    exc,
                )
            children.sort(key=lambda e: e.name)
            for entry in children:
                try:
                    is_sym = entry.is_symlink()
                except OSError as exc:
                    _enum_failed(
                        f"is_symlink failed during unsealed enumerate: "
                        f"{entry.path}: {exc}",
                        exc,
                    )
                    continue  # pragma: no cover
                if is_sym:
                    full_path = entry.path
                    abs_path = Path(full_path)
                    try:
                        real_target = os.path.realpath(full_path)
                    except OSError as exc:
                        _enum_failed(
                            f"realpath failed during unsealed enumerate: "
                            f"{full_path}: {exc}",
                            exc,
                        )
                        continue  # pragma: no cover
                    if real_target != root_real and not real_target.startswith(
                        root_real + os.sep
                    ):
                        _enum_failed(
                            f"symlink escapes source_dir during unsealed "
                            f"enumerate: {full_path} -> {real_target}"
                        )
                    try:
                        st_target = os.stat(full_path)
                    except OSError as exc:
                        _enum_failed(
                            f"stat through symlink failed during unsealed "
                            f"enumerate: {full_path}: {exc}",
                            exc,
                        )
                        continue  # pragma: no cover
                    if not stat.S_ISREG(st_target.st_mode):
                        _enum_failed(
                            f"symlink target is not a regular file during "
                            f"unsealed enumerate: {full_path} -> {real_target}"
                        )
                    _append_file(abs_path, st_target)
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError as exc:
                    _enum_failed(
                        f"is_dir failed during unsealed enumerate: "
                        f"{entry.path}: {exc}",
                        exc,
                    )
                    continue  # pragma: no cover
                if is_dir:
                    _walk(Path(entry.path))
                    continue
                abs_path = Path(entry.path)
                try:
                    st = os.lstat(entry.path)
                except OSError as exc:
                    _enum_failed(
                        f"lstat failed during unsealed enumerate: "
                        f"{abs_path}: {exc}",
                        exc,
                    )
                    continue  # pragma: no cover
                if not stat.S_ISREG(st.st_mode):
                    continue
                _append_file(abs_path, st)

    _walk(root)
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
    key = f"{safe}_unsealed_{_digest16(digest)}"
    files = [{"relpath": f["relpath"], "size": f["size"]} for f in files_meta]
    return key, False, digest, total, files


def _entry_lock_path(cache_root: Path, cache_key: str) -> Path:
    """M1：稳定锁身份，不随 entry 目录 rmtree/os.replace 更换 inode。"""
    return cache_root / LOCKS_DIR_NAME / f"{cache_key}.lock"


def _entry_dir(cache_root: Path, cache_key: str) -> Path:
    return cache_root / cache_key


def _open_lock(path: Path, *, create: bool = True) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _try_flock(fd: int, operation: int) -> bool:
    try:
        fcntl.flock(fd, operation | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return False
        raise


def _flock_wait(
    fd: int,
    operation: int,
    *,
    progress_callback: Optional[ProgressCallback],
    wait_for: str,
    poll_seconds: float = _LOCK_WAIT_POLL_SECONDS,
) -> None:
    """M7：阻塞等待期间周期打 ``firmware-stage-wait`` 戳。"""
    while True:
        if _try_flock(fd, operation):
            return
        _emit_progress(
            progress_callback,
            stage="firmware-stage-wait",
            wait_for=wait_for,
        )
        time.sleep(poll_seconds)


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


def _managed_root_resolved(cache_root: Path) -> Path:
    return cache_root.resolve(strict=False)


def _literal_managed_child(
    target: Path, cache_root: Path, *, expected_key: str
) -> Path:
    """M3：字面路径必须是持锁 ``expected_key`` 的直接条目或对应 staging 子项。"""
    root_abs = Path(os.path.abspath(str(cache_root)))
    target_abs = Path(os.path.abspath(str(target)))
    try:
        rel = target_abs.relative_to(root_abs)
    except ValueError as exc:
        raise FirmwareCacheError(
            CODE_PATH_ESCAPE,
            f"delete target not under cache root literal: {target}",
        ) from exc
    parts = rel.parts
    if len(parts) == 1 and parts[0] == expected_key:
        return target_abs
    if (
        len(parts) == 2
        and parts[0] == STAGING_DIR_NAME
        and parts[1].startswith(f"{expected_key}.tmp.")
    ):
        return target_abs
    raise FirmwareCacheError(
        CODE_PATH_ESCAPE,
        f"delete target {target} is not locked child for key={expected_key!r}",
    )


def _safe_rmtree(
    target: Path, cache_root: Path, *, expected_key: str
) -> None:
    """M3：拒绝软链目标、根目录本身、``.staging`` 祖先别名与跨条目解析。

    只删持锁 ``expected_key`` 的严格子项：条目目录，或
    ``realpath(cache_root)/.staging/<key>.tmp.*`` 直接子项。
    """
    if target.is_symlink() or os.path.islink(target):
        raise FirmwareCacheError(
            CODE_PATH_ESCAPE,
            f"refusing to delete symlink target itself: {target}",
        )
    literal = _literal_managed_child(target, cache_root, expected_key=expected_key)
    root_real = Path(os.path.realpath(str(cache_root)))
    root_abs = Path(os.path.abspath(str(cache_root)))
    try:
        rel = Path(os.path.abspath(str(target))).relative_to(root_abs)
    except ValueError as exc:
        raise FirmwareCacheError(
            CODE_PATH_ESCAPE,
            f"delete target not under cache root literal: {target}",
        ) from exc
    is_staging = (
        len(rel.parts) == 2
        and rel.parts[0] == STAGING_DIR_NAME
        and rel.parts[1].startswith(f"{expected_key}.tmp.")
    )
    try:
        resolved = Path(os.path.realpath(str(literal)))
    except OSError as exc:
        raise FirmwareCacheError(
            CODE_PATH_ESCAPE, f"cannot resolve delete target: {target}"
        ) from exc
    root_s = str(root_real)
    resolved_s = str(resolved)
    if resolved_s == root_s or not resolved_s.startswith(root_s + os.sep):
        raise FirmwareCacheError(
            CODE_PATH_ESCAPE,
            f"delete target escapes or equals cache root: {resolved}",
        )

    if is_staging:
        staging_dir = cache_root / STAGING_DIR_NAME
        if staging_dir.is_symlink() or os.path.islink(staging_dir):
            raise FirmwareCacheError(
                CODE_PATH_ESCAPE,
                f"refusing delete: .staging ancestor is symlink: {staging_dir}",
            )
        staging_real = Path(os.path.realpath(str(staging_dir)))
        expected_staging_real = root_real / STAGING_DIR_NAME
        if staging_real != expected_staging_real:
            raise FirmwareCacheError(
                CODE_PATH_ESCAPE,
                f".staging realpath mismatch: {staging_real} != "
                f"{expected_staging_real}",
            )
        if resolved.parent != staging_real:
            raise FirmwareCacheError(
                CODE_PATH_ESCAPE,
                f"resolved staging target not a direct child of "
                f"{expected_staging_real}: {resolved}",
            )
        if not resolved.name.startswith(f"{expected_key}.tmp."):
            raise FirmwareCacheError(
                CODE_PATH_ESCAPE,
                f"resolved staging name mismatch key={expected_key!r}: {resolved}",
            )
    else:
        expected_entry = root_real / expected_key
        if resolved != expected_entry:
            raise FirmwareCacheError(
                CODE_PATH_ESCAPE,
                f"resolved delete target not the locked entry for "
                f"key={expected_key!r}: {resolved}",
            )

    if resolved.is_dir() and not resolved.is_symlink():
        shutil.rmtree(resolved)
    elif resolved.exists() or resolved.is_symlink():
        # 末级软链已在入口拒绝；此处仅处理普通文件
        resolved.unlink()


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


def _local_matches_authoritative_files(
    entry_dir: Path, files: Sequence[Mapping[str, Any]]
) -> bool:
    """M6：按权威清单核对本地必需文件存在、尺寸与 SHA-256（有 sha 时必校）。

    只读本地缓存目录；不打开源目录大镜像。拦截「尺寸未变但内容损坏」的脏命中。
    """
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
        if isinstance(expected_sha, str) and expected_sha:
            try:
                if _sha256_file(abs_path) != expected_sha:
                    return False
            except OSError:
                return False
    return True


def _verify_ready_under_sh(
    entry_dir: Path,
    *,
    content_digest: str,
    authoritative_files: Sequence[Mapping[str, Any]],
) -> bool:
    ready = _read_ready(entry_dir)
    if ready is None:
        return False
    if ready.get("content_digest") != content_digest:
        return False
    return _local_matches_authoritative_files(entry_dir, authoritative_files)


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
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        IOPRIO_CLASS_IDLE = 3
        ioprio = (IOPRIO_CLASS_IDLE << 13) | 0
        libc.syscall(251, 1, 0, ioprio)
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
    cache_dev = _st_dev(cache_root)
    aee_dev = _st_dev(aee_root)
    if (
        cache_dev is not None
        and aee_dev is not None
        and cache_dev == aee_dev
        and writable_percent is not None
    ):
        spill = float(get_disk_archive_settings().stp_local_disk_spill_threshold)
        used_gb = float(usage.get("used_gb") or 0.0)
        writable_base = used_gb + float(avail)
        if writable_base > 0:
            writable_after = 100.0 * (used_gb + add_gb) / writable_base
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
    _best_effort_idle_ioprio()
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_files: list[dict[str, Any]] = []
    copied = 0
    last_cb = time.monotonic()
    try:
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
                    f"size mismatch after copy: {rel}",
                )
            sha = h.hexdigest()
            expected_sha = item.get("sha256")
            if (
                sealed
                and isinstance(expected_sha, str)
                and expected_sha
                and sha != expected_sha
            ):
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
    except FirmwareCacheError:
        raise
    except OSError as exc:
        raise FirmwareCacheError(
            CODE_COPY_IO, f"copy I/O failed: {exc}"
        ) from exc
    return out_files


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


def _evict_one_unlocked_entry(
    cache_root: Path,
    *,
    protect_keys: Optional[set[str]] = None,
) -> bool:
    """M4：仅淘汰 ``cache_root`` 内最旧未加锁条目；删除失败不计成功。"""
    protect = protect_keys or set()
    candidates: list[tuple[float, Path]] = []
    for entry in _list_entry_dirs(cache_root):
        if entry.name in protect:
            continue
        candidates.append((_entry_last_used(entry), entry))
    candidates.sort(key=lambda x: x[0])
    for _mtime, entry in candidates:
        lock_path = _entry_lock_path(cache_root, entry.name)
        fd: Optional[int] = None
        try:
            fd = _open_lock(lock_path, create=True)
            if not _try_flock(fd, fcntl.LOCK_EX):
                continue
            _safe_rmtree(entry, cache_root, expected_key=entry.name)
            logger.info("firmware_cache_evicted entry=%s", entry)
            return True
        except (OSError, FirmwareCacheError):
            logger.debug(
                "firmware_cache_evict_failed entry=%s", entry, exc_info=True
            )
            continue
        finally:
            _release_entry_lock(fd)
    return False


def _evict_until_space_ok(
    cache_root: Path,
    *,
    additional_bytes: int,
    settings: Any,
    aee_root: Path,
    protect_keys: Optional[set[str]] = None,
) -> None:
    """M4：循环逐项腾退至安全水位或无可腾退。"""
    while not _space_ok_after(
        cache_root,
        additional_bytes=additional_bytes,
        settings=settings,
        aee_root=aee_root,
    ):
        if not _evict_one_unlocked_entry(cache_root, protect_keys=protect_keys):
            break


def _sweep_staging(cache_root: Path) -> None:
    """M3：清扫无锁残留 staging；活跃写者持锁则跳过；越界拒绝。"""
    staging = cache_root / STAGING_DIR_NAME
    if not staging.is_dir():
        return
    for child in list(staging.iterdir()):
        name = child.name
        if ".tmp." not in name:
            continue
        key = name.rsplit(".tmp.", 1)[0]
        lock_path = _entry_lock_path(cache_root, key)
        fd: Optional[int] = None
        try:
            fd = _open_lock(lock_path, create=True)
            if not _try_flock(fd, fcntl.LOCK_EX):
                continue
            _safe_rmtree(child, cache_root, expected_key=key)
        except FirmwareCacheError as exc:
            if exc.code == CODE_PATH_ESCAPE:
                logger.warning(
                    "firmware_cache_staging_escape_blocked path=%s", child
                )
            else:
                logger.debug(
                    "firmware_cache_staging_sweep_failed path=%s",
                    child,
                    exc_info=True,
                )
        except OSError:
            logger.debug(
                "firmware_cache_staging_sweep_failed path=%s", child, exc_info=True
            )
        finally:
            _release_entry_lock(fd)


def _cleanup_pass(_manager: "FirmwareCacheManager") -> None:
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
            lock_path = _entry_lock_path(root, entry.name)
            fd: Optional[int] = None
            try:
                fd = _open_lock(lock_path, create=True)
                if not _try_flock(fd, fcntl.LOCK_EX):
                    continue
                # M1：锁内重新读取 last_used，避免锁前旧 mtime 误删
                age = now - _entry_last_used(entry)
                if age < ttl_seconds:
                    continue
                _safe_rmtree(entry, root, expected_key=entry.name)
                logger.info(
                    "firmware_cache_ttl_evicted entry=%s age_s=%.0f", entry, age
                )
            except (OSError, FirmwareCacheError):
                logger.debug(
                    "firmware_cache_ttl_failed entry=%s", entry, exc_info=True
                )
            finally:
                _release_entry_lock(fd)

        if not _space_ok_after(
            root, additional_bytes=0, settings=settings, aee_root=aee_root
        ):
            _evict_until_space_ok(
                root,
                additional_bytes=0,
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


def _populate_entry(
    *,
    cache_root: Path,
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
    settings: Any,
    aee_root: Path,
) -> tuple[list[dict[str, Any]], bool]:
    """已持条目 ``LOCK_EX``：串行区水位复核 → host lock → 许可 → 拷贝。"""
    staging_root = cache_root / STAGING_DIR_NAME
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = staging_root / f"{cache_key}.tmp.{os.getpid()}"
    if staging.exists():
        _safe_rmtree(staging, cache_root, expected_key=cache_key)
    staging.mkdir(parents=True, exist_ok=True)

    host_lock = host_transfer_lock_path()
    host_fd = _open_lock(host_lock, create=True)
    permit_hold: Optional[TransferPermitHold] = None
    permit_degraded = False
    staging_final: Optional[Path] = staging
    try:
        _flock_wait(
            host_fd,
            fcntl.LOCK_EX,
            progress_callback=progress_callback,
            wait_for="host-transfer-lock",
        )
        # M4：进入串行区后以最新水位复核，仅腾退本盘
        _evict_until_space_ok(
            cache_root,
            additional_bytes=total_bytes,
            settings=settings,
            aee_root=aee_root,
            protect_keys={cache_key},
        )
        if not _space_ok_after(
            cache_root,
            additional_bytes=total_bytes,
            settings=settings,
            aee_root=aee_root,
        ):
            raise FirmwareCacheError(
                CODE_INSUFFICIENT_SPACE,
                f"insufficient space in serial zone root={cache_root}",
            )

        if permit_client is not None:
            permit_hold = permit_client.acquire_permit(
                allow_permit_bypass=allow_permit_bypass
            )
            permit_degraded = bool(getattr(permit_hold, "permit_degraded", False))
        try:
            # M4：许可返回后、真正写入 .staging 前再以最新水位过门（排队期间可能跌破）
            _evict_until_space_ok(
                cache_root,
                additional_bytes=total_bytes,
                settings=settings,
                aee_root=aee_root,
                protect_keys={cache_key},
            )
            if not _space_ok_after(
                cache_root,
                additional_bytes=total_bytes,
                settings=settings,
                aee_root=aee_root,
            ):
                raise FirmwareCacheError(
                    CODE_INSUFFICIENT_SPACE,
                    f"insufficient space after permit before write root={cache_root}",
                )
            copied_files = _copy_files_streaming(
                source_dir,
                staging,
                files,
                sealed=sealed,
                progress_callback=progress_callback,
                total_bytes=total_bytes,
            )
            ready_payload = {
                "schema_version": 1,
                "profile": profile,
                "sealed": sealed,
                "content_digest": content_digest,
                "total_bytes": total_bytes,
                "files": copied_files,
            }
            try:
                _write_ready(staging, ready_payload)
                if entry_dir.exists():
                    _safe_rmtree(entry_dir, cache_root, expected_key=cache_key)
                os.replace(staging, entry_dir)
            except OSError as exc:
                raise FirmwareCacheError(
                    CODE_COPY_IO,
                    f"publish/ready I/O failed root={cache_root}: {exc}",
                ) from exc
            staging_final = None
            return copied_files, permit_degraded
        finally:
            if permit_hold is not None:
                permit_hold.release()
    finally:
        try:
            fcntl.flock(host_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(host_fd)
        except OSError:
            pass
        if staging_final is not None and Path(staging_final).exists():
            try:
                _safe_rmtree(
                    Path(staging_final), cache_root, expected_key=cache_key
                )
            except FirmwareCacheError:
                pass


def _try_hit_locked(
    *,
    root: Path,
    tier_name: str,
    cache_key: str,
    content_digest: str,
    authoritative_files: Sequence[Mapping[str, Any]],
    profile: str,
    sealed: bool,
    total_bytes: int,
    progress_callback: Optional[ProgressCallback],
    attempt: int,
) -> tuple[Optional[FirmwareCacheResult], Optional[int]]:
    entry_dir = _entry_dir(root, cache_key)
    if not (entry_dir / READY_MARKER).is_file():
        return None, None
    lock_path = _entry_lock_path(root, cache_key)
    fd = _open_lock(lock_path, create=True)
    try:
        _emit_progress(
            progress_callback,
            stage="firmware-stage-wait",
            wait_for="entry-lock-sh",
            cache_key=cache_key,
            attempt=attempt,
        )
        _flock_wait(
            fd,
            fcntl.LOCK_SH,
            progress_callback=progress_callback,
            wait_for="entry-lock-sh",
        )
        if not _verify_ready_under_sh(
            entry_dir,
            content_digest=content_digest,
            authoritative_files=authoritative_files,
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
    except BaseException:
        _release_entry_lock(fd)
        raise


def _populate_locked(
    *,
    root: Path,
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
    settings: Any,
    aee_root: Path,
) -> tuple[Optional[FirmwareCacheResult], Optional[int]]:
    entry_dir = _entry_dir(root, cache_key)
    lock_path = _entry_lock_path(root, cache_key)
    fd = _open_lock(lock_path, create=True)
    try:
        _emit_progress(
            progress_callback,
            stage="firmware-stage-wait",
            wait_for="entry-lock-ex",
            cache_key=cache_key,
            attempt=attempt,
        )
        _flock_wait(
            fd,
            fcntl.LOCK_EX,
            progress_callback=progress_callback,
            wait_for="entry-lock-ex",
        )
        # 双检：等待期间可能已写好
        if _verify_ready_under_sh(
            entry_dir,
            content_digest=content_digest,
            authoritative_files=files,
        ):
            # M7：降级取 LOCK_SH 走非阻塞轮询 + 周期打戳
            _flock_wait(
                fd,
                fcntl.LOCK_SH,
                progress_callback=progress_callback,
                wait_for="entry-lock-sh-downgrade",
            )
            if _verify_ready_under_sh(
                entry_dir,
                content_digest=content_digest,
                authoritative_files=files,
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
            # 降级窗口失配 → 可恢复重入
            raise FirmwareCacheError(
                CODE_DOWNGRADE_RACE,
                f"ready mismatch after SH downgrade key={cache_key}",
                retryable=True,
            )

        _evict_until_space_ok(
            root,
            additional_bytes=total_bytes,
            settings=settings,
            aee_root=aee_root,
            protect_keys={cache_key},
        )
        if not _space_ok_after(
            root,
            additional_bytes=total_bytes,
            settings=settings,
            aee_root=aee_root,
        ):
            raise FirmwareCacheError(
                CODE_INSUFFICIENT_SPACE,
                f"no space on {tier_name} for key={cache_key}",
            )

        copied_files, permit_degraded = _populate_entry(
            cache_root=root,
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
            settings=settings,
            aee_root=aee_root,
        )
        # M2：LOCK_EX → LOCK_SH 降级后持锁复核；失配 → 可恢复重入
        # M7：降级取 LOCK_SH 走非阻塞轮询 + 周期打戳
        _flock_wait(
            fd,
            fcntl.LOCK_SH,
            progress_callback=progress_callback,
            wait_for="entry-lock-sh-downgrade",
        )
        if not _verify_ready_under_sh(
            entry_dir,
            content_digest=content_digest,
            authoritative_files=copied_files if not sealed else files,
        ):
            raise FirmwareCacheError(
                CODE_DOWNGRADE_RACE,
                f"ready marker missing after cold-pull downgrade key={cache_key}",
                retryable=True,
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
    except BaseException:
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
    # 已封包：权威清单即 profile files；命中完整性按此核对（M6）
    authoritative_files = files
    primary, secondary = resolve_cache_roots(settings=cfg)
    aee_root = get_aee_local_root()

    client = permit_client
    if client is None:
        client = _default_permit_client(
            progress_callback=progress_callback,
            allow_permit_bypass=allow_permit_bypass,
        )

    last_error: Optional[Exception] = None
    saw_insufficient = False
    # M6-2：任一候选盘非空间类失败后，禁止误归因 insufficient → NFS 直读
    saw_non_space_failure = False
    held_fd: Optional[int] = None
    try:
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
                        authoritative_files=authoritative_files,
                        profile=profile,
                        sealed=sealed,
                        total_bytes=total_bytes,
                        progress_callback=progress_callback,
                        attempt=attempt,
                    )
                except BaseException as exc:
                    if isinstance(exc, Exception):
                        last_error = exc
                        continue
                    raise
                if result is not None and fd is not None:
                    held = (result, fd)
                    break

            if held is None:
                for tier_name, root in (
                    ("primary", primary),
                    ("secondary", secondary),
                ):
                    if root is None:
                        continue
                    _evict_until_space_ok(
                        root,
                        additional_bytes=total_bytes,
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
                        saw_insufficient = True
                        continue
                    try:
                        result, fd = _populate_locked(
                            root=root,
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
                            settings=cfg,
                            aee_root=aee_root,
                        )
                    except FirmwareCacheError as exc:
                        if exc.retryable:
                            # M6：降级复核等非空间可恢复错误不得被主盘空间不足掩盖
                            saw_non_space_failure = True
                            last_error = exc
                            break  # 重入外层 attempt
                        if exc.code == CODE_INSUFFICIENT_SPACE:
                            saw_insufficient = True
                            last_error = exc
                            continue
                        if exc.code == CODE_COPY_IO:
                            # M6-2：I/O 错误直接失败，不降级 NFS
                            raise
                        saw_non_space_failure = True
                        raise
                    except TransferPermitClientError:
                        raise
                    except Exception as exc:
                        saw_non_space_failure = True
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
                else:
                    # 内层 for 正常结束（无 break）
                    pass

            if held is None:
                continue

            result, held_fd = held
            try:
                yield result
                return
            finally:
                _release_entry_lock(held_fd)
                held_fd = None

        # M6：仅「全部可用候选盘均因空间不足被拒」可走 NFS 降级；
        # 降级复核耗尽 / I/O 等非空间错误优先按原语义抛出。
        if isinstance(last_error, FirmwareCacheError) and last_error.code in (
            CODE_COPY_IO,
            CODE_DOWNGRADE_RACE,
        ):
            raise last_error
        if saw_non_space_failure:
            if isinstance(last_error, FirmwareCacheError):
                raise last_error
            if last_error is not None:
                raise FirmwareCacheError(
                    CODE_COPY_IO,
                    f"non-space failure during populate: {last_error}",
                ) from last_error
        space_exhausted = (
            saw_insufficient
            and not saw_non_space_failure
            and (
                last_error is None
                or (
                    isinstance(last_error, FirmwareCacheError)
                    and last_error.code == CODE_INSUFFICIENT_SPACE
                )
            )
        )
        if allow_nfs_direct_fallback and space_exhausted:
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

        if isinstance(last_error, FirmwareCacheError):
            raise last_error
        if last_error is not None:
            raise FirmwareCacheError(
                CODE_RETRY_EXHAUSTED, f"ensure failed after retries: {last_error}"
            ) from last_error
        raise FirmwareCacheError(
            CODE_INSUFFICIENT_SPACE,
            f"no local cache root can host key={cache_key} size={total_bytes}",
        )
    except BaseException:
        _release_entry_lock(held_fd)
        raise
