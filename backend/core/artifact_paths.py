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


def _coerce_local_path(raw: str) -> Path:
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
