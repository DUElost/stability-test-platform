"""Helpers for validating local artifact storage paths."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from urllib.parse import unquote, urlparse

_WINDOWS_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
_SAFE_PATH_COMPONENT_RE = re.compile(r"^[^/\\\0]+$")
_DIR_OPEN_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)


class ArtifactPathError(ValueError):
    """Base error for invalid artifact storage paths."""


class ArtifactPathSchemeError(ArtifactPathError):
    """Raised when storage_uri uses an unsupported scheme."""


class ArtifactPathOutsideRootError(ArtifactPathError):
    """Raised when a local artifact path escapes the shared storage root."""


class ArtifactPathNotFoundError(ArtifactPathError):
    """Raised when a required artifact file does not exist."""


def get_stp_nfs_root() -> Path:
    """Deprecated alias: shared storage root (STP_AEE_NFS_ROOT)."""
    from backend.core.storage_root import resolve_shared_storage_root

    raw = resolve_shared_storage_root()
    if not raw:
        raise ArtifactPathError("STP_AEE_NFS_ROOT is not configured")
    return Path(raw).resolve(strict=False)


def get_local_artifact_roots() -> tuple[Path, ...]:
    from backend.core.storage_root import resolve_shared_storage_root

    raw = resolve_shared_storage_root()
    if not raw:
        return ()
    return (Path(raw).resolve(strict=False),)


def coerce_local_artifact_path(storage_uri: str) -> Path:
    raw = (storage_uri or "").strip()
    if not raw:
        raise ArtifactPathError("storage_uri is required")
    return _coerce_local_path(raw).resolve(strict=False)


def resolve_local_artifact_path(storage_uri: str, *, must_exist: bool = False) -> Path:
    resolved_path = coerce_local_artifact_path(storage_uri)
    allowed_roots = get_local_artifact_roots()
    if not allowed_roots:
        raise ArtifactPathError("STP_AEE_NFS_ROOT is not configured")
    if not any(resolved_path.is_relative_to(root) for root in allowed_roots):
        raise ArtifactPathOutsideRootError(
            "artifact path must stay under STP_AEE_NFS_ROOT: "
            f"{', '.join(str(root) for root in allowed_roots)}"
        )

    if must_exist and (not resolved_path.exists() or not resolved_path.is_file()):
        raise ArtifactPathNotFoundError(f"artifact file not found: {resolved_path}")
    return resolved_path


def resolve_device_event_remote_path(
    storage_uri: str,
    *,
    plan_run_id: int | None = None,
    event_id: str | None = None,
    must_exist: bool = False,
) -> Path:
    """Validate a DeviceLogEvent ``remote_path`` under ``devices/`` scope."""
    resolved_path = resolve_local_artifact_path(storage_uri, must_exist=must_exist)
    devices_root = (get_stp_nfs_root() / "devices").resolve(strict=False)
    if not resolved_path.is_relative_to(devices_root):
        raise ArtifactPathOutsideRootError(
            "device log remote path must stay under devices/: "
            f"{resolved_path}"
        )
    if plan_run_id is not None:
        scope = (devices_root / str(plan_run_id)).resolve(strict=False)
        if not resolved_path.is_relative_to(scope):
            raise ArtifactPathOutsideRootError(
                f"device log remote path must stay under {scope}: {resolved_path}"
            )
    elif event_id:
        scope = (devices_root / "unassigned" / event_id).resolve(strict=False)
        if not resolved_path.is_relative_to(scope):
            raise ArtifactPathOutsideRootError(
                f"device log remote path must stay under {scope}: {resolved_path}"
            )
    return resolved_path


def _reject_path_traversal(raw: str) -> None:
    if "\0" in raw or ".." in Path(raw).parts:
        raise ArtifactPathError("path traversal not allowed")


def _validate_path_component(part: str) -> None:
    if not part or part in (".", "..") or not _SAFE_PATH_COMPONENT_RE.match(part):
        raise ArtifactPathOutsideRootError(f"unsafe path component: {part!r}")


def path_under_root(root: Path, *parts: str) -> Path:
    """Build a path under *root* after validating each component."""
    for part in parts:
        _validate_path_component(part)
    dest = root.joinpath(*parts)
    root_resolved = root.resolve(strict=False)
    dest_resolved = dest.resolve(strict=False)
    if not dest_resolved.is_relative_to(root_resolved):
        raise ArtifactPathOutsideRootError(f"{dest} is not under {root}")
    return dest


def copytree_validated_event_dir(src: Path, dest: Path, *, plan_run_id: int) -> None:
    """Copy a device event directory after scope validation (ADR-0028)."""
    import shutil

    resolve_device_event_remote_path(str(src), plan_run_id=plan_run_id, must_exist=False)
    shutil.copytree(str(src), str(dest))


def _reject_nested_symlinks(src: Path) -> None:
    """Reject any symlink under *src* (event dirs must be plain directory trees)."""
    for entry in src.rglob("*"):
        if entry.is_symlink():
            raise ArtifactPathOutsideRootError(f"symlink not allowed under event dir: {entry}")


def _open_dir_nofollow(path: Path, *, contained_in: Path) -> int:
    if path.is_symlink():
        raise ArtifactPathOutsideRootError(f"symlink not allowed: {path}")
    resolved = path.resolve(strict=False)
    container = contained_in.resolve(strict=False)
    if not resolved.is_relative_to(container):
        raise ArtifactPathOutsideRootError(f"{resolved} is not under {container}")
    try:
        return os.open(path, _DIR_OPEN_FLAGS)
    except OSError as exc:
        raise ArtifactPathOutsideRootError(str(path)) from exc


def _open_dir_at(parent_fd: int, rel: Path) -> int:
    if not rel.parts:
        return os.dup(parent_fd)
    fd = os.dup(parent_fd)
    for part in rel.parts:
        _validate_path_component(part)
        try:
            next_fd = os.open(part, _DIR_OPEN_FLAGS, dir_fd=fd)
        except OSError as exc:
            os.close(fd)
            raise ArtifactPathOutsideRootError(str(rel)) from exc
        os.close(fd)
        fd = next_fd
    return fd


def _write_all(fd: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            raise OSError("failed to write artifact data")
        remaining = remaining[written:]


def _copy_file_at(src_fd: int, name: str, dest_fd: int) -> None:
    rfd = os.open(name, _FILE_OPEN_FLAGS, dir_fd=src_fd)
    try:
        st = os.fstat(rfd)
        if not stat.S_ISREG(st.st_mode):
            raise ArtifactPathOutsideRootError(f"non-regular file: {name}")
        wfd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            st.st_mode & 0o777,
            dir_fd=dest_fd,
        )
        try:
            while True:
                chunk = os.read(rfd, 1024 * 1024)
                if not chunk:
                    break
                _write_all(wfd, chunk)
        finally:
            os.close(wfd)
    finally:
        os.close(rfd)


def _open_new_dir_at(parent_fd: int, rel: Path) -> int:
    """Create and open a new leaf directory under *parent_fd* using O_NOFOLLOW."""
    if not rel.parts:
        raise ArtifactPathOutsideRootError("empty destination path")
    fd = os.dup(parent_fd)
    for part in rel.parts[:-1]:
        _validate_path_component(part)
        try:
            next_fd = os.open(part, _DIR_OPEN_FLAGS, dir_fd=fd)
        except OSError as exc:
            os.close(fd)
            raise ArtifactPathOutsideRootError(str(rel)) from exc
        os.close(fd)
        fd = next_fd
    leaf = rel.parts[-1]
    _validate_path_component(leaf)
    try:
        os.mkdir(leaf, mode=0o755, dir_fd=fd)
    except FileExistsError as exc:
        os.close(fd)
        raise FileExistsError(f"destination already exists: {leaf}") from exc
    try:
        leaf_fd = os.open(leaf, _DIR_OPEN_FLAGS, dir_fd=fd)
    except OSError as exc:
        os.close(fd)
        raise ArtifactPathOutsideRootError(str(rel)) from exc
    os.close(fd)
    return leaf_fd


def _copytree_fd_at(src_fd: int, dest_fd: int, dest_path: Path) -> None:
    for name in os.listdir(src_fd):
        _validate_path_component(name)
        try:
            entry_mode = os.lstat(name, dir_fd=src_fd)
        except OSError as exc:
            raise ArtifactPathOutsideRootError(f"{dest_path / name}") from exc
        if stat.S_ISLNK(entry_mode.st_mode):
            raise ArtifactPathOutsideRootError(
                f"symlink not allowed under event dir: {dest_path / name}"
            )
        if stat.S_ISDIR(entry_mode.st_mode):
            os.mkdir(name, mode=entry_mode.st_mode & 0o777, dir_fd=dest_fd)
            child_src_fd = os.open(name, _DIR_OPEN_FLAGS, dir_fd=src_fd)
            child_dest_fd = os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0), dir_fd=dest_fd)
            try:
                _copytree_fd_at(child_src_fd, child_dest_fd, dest_path / name)
            finally:
                os.close(child_src_fd)
                os.close(child_dest_fd)
        elif stat.S_ISREG(entry_mode.st_mode):
            _copy_file_at(src_fd, name, dest_fd)
        else:
            raise ArtifactPathOutsideRootError(
                f"unsupported file type under event dir: {dest_path / name}"
            )


def _copytree_fd_to_dest(src_fd: int, dest_root_fd: int, dest_rel: Path) -> None:
    dest_fd = _open_new_dir_at(dest_root_fd, dest_rel)
    try:
        _copytree_fd_at(src_fd, dest_fd, Path(*dest_rel.parts))
    finally:
        os.close(dest_fd)


def copytree_under_root(
    src: Path,
    dest: Path,
    *,
    root: Path,
    dest_root: Path,
) -> None:
    """Copy a directory tree when *src* and *dest* stay under their roots."""
    root_resolved = root.resolve(strict=False)
    try:
        resolved = src.resolve(strict=False)
    except OSError as exc:
        raise ArtifactPathOutsideRootError(str(src)) from exc
    if not resolved.is_relative_to(root_resolved):
        raise ArtifactPathOutsideRootError(f"{resolved} is not under {root_resolved}")
    try:
        dest_rel = dest.relative_to(dest_root)
    except ValueError as exc:
        raise ArtifactPathOutsideRootError(
            f"{dest} is not under {dest_root}"
        ) from exc
    if dest_rel.is_absolute() or ".." in dest_rel.parts:
        raise ArtifactPathOutsideRootError(f"{dest} is not under {dest_root}")
    for part in dest_rel.parts:
        _validate_path_component(part)

    src_rel = resolved.relative_to(root_resolved)
    for part in src_rel.parts:
        _validate_path_component(part)
    root_fd = _open_dir_nofollow(root_resolved, contained_in=root_resolved)
    dest_root_fd = _open_dir_nofollow(dest_root, contained_in=dest_root)
    try:
        src_fd = _open_dir_at(root_fd, src_rel)
        try:
            _copytree_fd_to_dest(src_fd, dest_root_fd, dest_rel)
        finally:
            os.close(src_fd)
    finally:
        os.close(root_fd)
        os.close(dest_root_fd)


def _plan_run_devices_scope(storage_root: str, plan_run_id: int) -> Path | None:
    """Canonical ``devices/{plan_run_id}/`` scope; reject plan_run symlink escape."""
    storage = Path(storage_root).resolve(strict=False)
    devices_parent = storage / "devices"
    if not devices_parent.is_dir():
        return None
    devices_parent_real = devices_parent.resolve(strict=False)
    plan_run_entry = devices_parent / str(int(plan_run_id))
    if plan_run_entry.is_symlink():
        return None
    plan_run_scope = (devices_parent_real / str(int(plan_run_id))).resolve(strict=False)
    if plan_run_scope.parent != devices_parent_real:
        return None
    if plan_run_scope.name != str(int(plan_run_id)):
        return None
    if not plan_run_scope.is_relative_to(devices_parent_real):
        return None
    return plan_run_scope


def resolve_extract_event_src(
    raw: str,
    *,
    nfs_root: str,
    legacy_root: str,
    plan_run_id: int,
) -> tuple[Path, Path] | None:
    """Locate an event directory on primary or legacy storage (D8).

    Returns ``(src, devices_scope_root)`` when a validated directory exists.
    """
    from backend.core.storage_root import resolve_legacy_shared_storage_root

    _reject_path_traversal(raw)
    rel_name = Path(raw).name
    if not rel_name or rel_name in (".", ".."):
        return None

    legacy = legacy_root or resolve_legacy_shared_storage_root()
    roots = [nfs_root]
    if legacy and legacy != nfs_root:
        roots.append(legacy)

    for root in roots:
        if not root:
            continue
        plan_run_scope = _plan_run_devices_scope(root, plan_run_id)
        if plan_run_scope is None:
            continue
        candidate_entry = plan_run_scope / rel_name
        if candidate_entry.is_symlink():
            continue
        if not candidate_entry.is_dir():
            continue
        try:
            candidate_real = candidate_entry.resolve(strict=False)
        except OSError:
            continue
        if candidate_real.name != rel_name:
            continue
        if not candidate_real.is_relative_to(plan_run_scope):
            continue
        try:
            _reject_nested_symlinks(candidate_entry)
        except ArtifactPathOutsideRootError:
            continue
        return candidate_real, plan_run_scope
    return None


def _coerce_local_path(raw: str) -> Path:
    _reject_path_traversal(raw)
    if _WINDOWS_DRIVE_PATH_RE.match(raw) or raw.startswith("\\\\"):
        return Path(raw)

    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    if scheme == "":
        return Path(raw)
    if scheme != "file":
        raise ArtifactPathSchemeError(
            f"unsupported artifact scheme: {scheme or 'empty'}"
        )
    if parsed.netloc and parsed.path:
        return Path(f"//{parsed.netloc}{unquote(parsed.path)}")
    if parsed.netloc and not parsed.path:
        return Path(unquote(parsed.netloc))
    return Path(unquote(parsed.path))
