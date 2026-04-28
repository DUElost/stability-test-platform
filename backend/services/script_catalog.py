"""Script catalog scanning helpers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.models.script import Script

_SUPPORTED_SUFFIXES = {
    ".py": "python",
    ".sh": "shell",
    ".bat": "bat",
    ".cmd": "bat",
}


@dataclass
class ScriptScanResult:
    created: int = 0
    skipped: int = 0
    deactivated: int = 0
    conflicts: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "created": self.created,
            "skipped": self.skipped,
            "deactivated": self.deactivated,
            "conflicts": self.conflicts,
        }


def detect_script_type(path: Path) -> Optional[str]:
    return _SUPPORTED_SUFFIXES.get(path.suffix.lower())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_script_entries(root: Path) -> Iterable[Tuple[str, str, str, Path, str]]:
    for category_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        category = category_dir.name
        for name_dir in sorted(p for p in category_dir.iterdir() if p.is_dir()):
            name = name_dir.name
            for version_dir in sorted(p for p in name_dir.iterdir() if p.is_dir()):
                raw_version = version_dir.name
                if not raw_version.startswith("v") or len(raw_version) <= 1:
                    continue
                version = raw_version[1:]
                candidates = [
                    p for p in sorted(version_dir.iterdir())
                    if p.is_file() and detect_script_type(p) and not p.name.startswith("_")
                ]
                if not candidates:
                    continue
                entry = candidates[0]
                script_type = detect_script_type(entry)
                if script_type:
                    yield category, name, version, entry, script_type


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


def scan_script_root(db: Session, root: str | Path, runtime_root: str | None = None) -> ScriptScanResult:
    root_path = Path(root).resolve()
    if not root_path.exists() or not root_path.is_dir():
        raise FileNotFoundError(f"script root not found: {root_path}")

    result = ScriptScanResult()
    seen_keys: set[tuple[str, str]] = set()
    now = datetime.utcnow()

    existing_rows = db.query(Script).all()
    existing_by_key = {(row.name, row.version): row for row in existing_rows}

    for category, name, version, entry, script_type in _iter_script_entries(root_path):
        key = (name, version)
        seen_keys.add(key)
        content_sha256 = sha256_file(entry)
        existing = existing_by_key.get(key)

        if existing is None:
            db.add(Script(
                name=name,
                display_name=name,
                category=category,
                script_type=script_type,
                version=version,
                nfs_path=_runtime_path(root_path, entry, runtime_root),
                entry_point="",
                content_sha256=content_sha256,
                param_schema={},
                is_active=True,
                created_at=now,
                updated_at=now,
            ))
            result.created += 1
            continue

        if existing.content_sha256 != content_sha256:
            result.conflicts.append({"name": name, "version": version})
            continue

        if not existing.is_active:
            existing.is_active = True
            existing.updated_at = now
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
    return result
