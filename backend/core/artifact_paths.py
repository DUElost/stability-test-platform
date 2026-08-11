"""Helpers for validating local artifact storage paths."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

_WINDOWS_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


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


def copytree_under_root(src: Path, dest: Path, *, root: Path) -> None:
    """Copy a directory tree only when *src* resolves under *root*."""
    import shutil

    root_resolved = root.resolve(strict=False)
    try:
        resolved = src.resolve(strict=False)
    except OSError as exc:
        raise ArtifactPathOutsideRootError(str(src)) from exc
    if not resolved.is_relative_to(root_resolved):
        raise ArtifactPathOutsideRootError(f"{resolved} is not under {root_resolved}")
    _reject_nested_symlinks(resolved)
    shutil.copytree(str(resolved), str(dest))


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
