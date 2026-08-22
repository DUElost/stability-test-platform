"""Script catalog scanning helpers."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.core.legacy_aee import LEGACY_AEE_SCRIPT_NAMES
from backend.models.script import Script

logger = logging.getLogger(__name__)

_SUPPORTED_SUFFIXES = {
    ".py": "python",
    ".sh": "shell",
}

_CAPABILITIES_FILE = "capabilities.json"


@dataclass
class ScriptScanResult:
    created: int = 0
    skipped: int = 0
    deactivated: int = 0
    conflicts: List[Dict[str, str]] = field(default_factory=list)
    rebaselined: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "created": self.created,
            "skipped": self.skipped,
            "deactivated": self.deactivated,
            "conflicts": self.conflicts,
            "rebaselined": self.rebaselined,
        }


def detect_script_type(path: Path) -> Optional[str]:
    return _SUPPORTED_SUFFIXES.get(path.suffix.lower())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_DEFAULT_CATEGORY = "device"


def _iter_script_entries(root: Path) -> Iterable[Tuple[str, str, str, Path, str]]:
    """Yield (category, name, version, entry, script_type).

    Layout: ``root/<name>/v<version>/<entry>.py`` (flat, 2 levels under root).
    Category is fixed at ``device``.
    """
    for name_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        name = name_dir.name
        if name in LEGACY_AEE_SCRIPT_NAMES:
            continue
        for version_dir in sorted(p for p in name_dir.iterdir() if p.is_dir()):
            raw_version = version_dir.name
            if not raw_version.startswith("v") or len(raw_version) <= 1:
                continue
            version = raw_version[1:]
            entry, script_type = _pick_entry(version_dir)
            if entry:
                yield _DEFAULT_CATEGORY, name, version, entry, script_type


def _pick_entry(version_dir: Path) -> tuple:
    """Return (entry_path, script_type) for the first script file in *version_dir*."""
    candidates = [
        p for p in sorted(version_dir.iterdir())
        if p.is_file() and detect_script_type(p) and not p.name.startswith("_")
    ]
    if not candidates:
        return None, None
    entry = candidates[0]
    return entry, detect_script_type(entry)


def support_files_manifest(version_dir: Path, entry: Path) -> dict[str, str]:
    """Map companion script filenames → sha256 (every script file except *entry*)."""
    manifest: dict[str, str] = {}
    entry_resolved = entry.resolve()
    for path in sorted(version_dir.iterdir()):
        if not path.is_file():
            continue
        if path.resolve() == entry_resolved:
            continue
        if detect_script_type(path) is None:
            continue
        manifest[path.name] = sha256_file(path)
    return manifest


def read_capabilities(version_dir: Path) -> list[str]:
    """Read ``capabilities.json`` from a version directory (e.g. ``progress_stamps``).

    Missing or malformed metadata is treated as "no capabilities" — a version
    that declares nothing must not pass capability-gated validation. Only
    non-empty strings are kept and returned sorted for stable comparisons.
    """
    meta = version_dir / _CAPABILITIES_FILE
    if not meta.is_file():
        return []
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning(
            "script_capabilities_metadata_unreadable dir=%s",
            version_dir,
        )
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("capabilities")
    if not isinstance(raw, list):
        return []
    return sorted({
        str(cap).strip()
        for cap in raw
        if isinstance(cap, str) and cap.strip()
    })


def _is_under_root(path: str, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def _is_under_runtime_root(path: str, runtime_root: str) -> bool:
    root = runtime_root.replace("\\", "/").rstrip("/")
    target = path.replace("\\", "/").rstrip("/")
    return bool(root) and (target == root or target.startswith(f"{root}/"))


def _runtime_path(root: Path, entry: Path, runtime_root: str | None) -> str:
    if not runtime_root:
        return str(entry)

    relative_parts = entry.relative_to(root).parts
    normalized_root = runtime_root.rstrip("/\\")
    if "\\" in normalized_root or (len(normalized_root) >= 2 and normalized_root[1] == ":"):
        return str(PureWindowsPath(normalized_root, *relative_parts))
    return str(PurePosixPath(normalized_root, *relative_parts))


def scan_script_root(
    db: Session,
    root: str | Path,
    runtime_root: str | None = None,
    *,
    force_rebaseline: bool = False,
) -> ScriptScanResult:
    """Scan ``root`` and reconcile the ``script`` table.

    Normal mode implements the ADR-0020 contract: a version whose on-disk
    sha256 no longer matches the stored one is reported under ``conflicts``
    and the row is left untouched — publishing changed content requires a new
    version directory.

    ``is_active`` is scan-managed in one direction only: versions missing from
    disk are deactivated, but a row deactivated while its directory is still
    present (admin endpoint / seed migration) is never resurrected —
    re-activation is an explicit operator action.

    ``force_rebaseline=True`` is the explicit operator escape hatch for the
    case where that contract has *already* been broken upstream (e.g. a
    repo-wide mechanical rewrite edited published version directories in
    place). It re-anchors ``content_sha256``/``nfs_path`` to what is on disk
    and reports the affected versions under ``rebaselined``. This trades away
    the "a given version always means the same bytes" guarantee, so callers
    must gate it on admin auth and on there being no in-flight PlanRun.
    """
    root_path = Path(root).resolve()
    if not root_path.exists() or not root_path.is_dir():
        raise FileNotFoundError(f"script root not found: {root_path}")

    result = ScriptScanResult()
    seen_keys: set[tuple[str, str]] = set()
    now = datetime.now(timezone.utc)

    existing_rows = db.query(Script).all()
    existing_by_key = {(row.name, row.version): row for row in existing_rows}

    for category, name, version, entry, script_type in _iter_script_entries(root_path):
        key = (name, version)
        seen_keys.add(key)
        content_sha256 = sha256_file(entry)
        support_manifest = support_files_manifest(entry.parent, entry)
        capabilities = read_capabilities(entry.parent)
        existing = existing_by_key.get(key)

        if existing is None:
            db.add(Script(
                name=name,
                display_name=name,
                category=category,
                script_type=script_type,
                version=version,
                nfs_path=_runtime_path(root_path, entry, runtime_root),
                content_sha256=content_sha256,
                support_files_manifest=support_manifest,
                capabilities=capabilities,
                param_schema={},
                default_params={},
                is_active=True,
                created_at=now,
                updated_at=now,
            ))
            result.created += 1
            continue

        stored_manifest = dict(existing.support_files_manifest or {})
        stored_capabilities = list(existing.capabilities or [])
        entry_changed = existing.content_sha256 != content_sha256
        support_changed = stored_manifest != support_manifest
        capabilities_changed = stored_capabilities != capabilities
        if entry_changed or support_changed or capabilities_changed:
            if (
                not force_rebaseline
                and not entry_changed
                and not stored_manifest
                and support_manifest
            ):
                # First scan after support-manifest tracking shipped: anchor
                # companion modules without treating on-disk state as a conflict.
                existing.support_files_manifest = support_manifest
                existing.updated_at = now
                result.skipped += 1
                continue
            if (
                not force_rebaseline
                and not entry_changed
                and not support_changed
                and not stored_capabilities
                and capabilities
            ):
                # First scan after capability metadata shipped: backfill
                # silently instead of treating the new column as a conflict.
                existing.capabilities = capabilities
                existing.updated_at = now
                result.skipped += 1
                continue
            if not force_rebaseline:
                result.conflicts.append({"name": name, "version": version})
                continue
            rebaseline_entry = {
                "name": name,
                "version": version,
                "old_sha256": existing.content_sha256 or "",
                "new_sha256": content_sha256,
            }
            if capabilities_changed:
                rebaseline_entry["old_capabilities"] = stored_capabilities
                rebaseline_entry["new_capabilities"] = capabilities
            result.rebaselined.append(rebaseline_entry)
            existing.content_sha256 = content_sha256
            existing.support_files_manifest = support_manifest
            existing.capabilities = capabilities
            existing.nfs_path = _runtime_path(root_path, entry, runtime_root)
            existing.is_active = True
            existing.updated_at = now
            continue

        # A row deactivated while its directory is still on disk stays
        # deactivated — that state only ever comes from the admin deactivate
        # endpoint or a seed migration, and silently resurrecting it defeats
        # those decisions. Re-activation is an explicit operator action.
        result.skipped += 1

    for row in existing_rows:
        key = (row.name, row.version)
        if key in seen_keys:
            continue
        if not row.is_active:
            continue
        if runtime_root:
            if not _is_under_runtime_root(row.nfs_path, runtime_root):
                continue
        elif not _is_under_root(row.nfs_path, root_path):
            continue
        row.is_active = False
        row.updated_at = now
        result.deactivated += 1

    db.commit()
    try:
        from backend.services.script_catalog_version import (
            invalidate_script_catalog_version_cache,
        )

        invalidate_script_catalog_version_cache()
    except Exception:
        pass
    return result
