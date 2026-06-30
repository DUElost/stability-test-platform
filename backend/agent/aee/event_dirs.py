"""Event directory naming and discovery (ADR-0025 Sprint 4).

Supports ISO-style (``2026-06-23_14-30-00_db.01``) and compact MTK-style
(``2026_0629_174940_206_db.74.ANR``) basenames.
"""

from __future__ import annotations

import re
from pathlib import Path

# ISO: 2026-06-23_14-30-00_*  or  2026_06_23_14_30_00_*
# Compact: 2026_0629_174940_206_*
_EVENT_DIR_BASENAME_RE = re.compile(
    r"^("
    r"\d{4}[-_]\d{2}[-_]\d{2}[_T]\d{2}[-_:]?\d{2}[-_:]?\d{2}"
    r"|\d{4}_\d{4}_\d{6}_\d{3}"
    r")_",
)


def is_event_dir_basename(name: str) -> bool:
    """Return True if ``name`` looks like a timestamp-prefixed event directory."""
    if not name or name.startswith("."):
        return False
    return bool(_EVENT_DIR_BASENAME_RE.match(name))


def event_dir_basename_from_path(path: str) -> str | None:
    """Extract event directory basename from a filesystem or device path."""
    cleaned = (path or "").strip().replace("\\", "/")
    if not cleaned:
        return None
    parts = [p for p in cleaned.split("/") if p]
    for part in reversed(parts):
        if part in ("__exp_main.txt", "main.dbg", "ZZ_INTERNAL"):
            continue
        if is_event_dir_basename(part):
            return part
    return None


def is_valid_event_dir(path: Path) -> bool:
    """Heuristic: directory contains AEE event markers."""
    return (
        (path / "ZZ_INTERNAL").is_file()
        or (path / "__exp_main.txt").is_file()
        or (path / "main.dbg").is_file()
    )


def find_event_dir_under_root(
    root: Path,
    dirname: str,
    *,
    max_depth: int = 8,
) -> Path | None:
    """Locate ``{root}/**/{dirname}`` when events live under folder/serial/."""
    if not dirname:
        return None
    direct = root / dirname
    if direct.is_dir() and is_valid_event_dir(direct):
        return direct

    base_depth = len(root.parts)
    matches: list[Path] = []
    try:
        for candidate in root.rglob(dirname):
            if not candidate.is_dir() or candidate.name != dirname:
                continue
            if len(candidate.parts) - base_depth > max_depth:
                continue
            if is_valid_event_dir(candidate):
                matches.append(candidate)
    except OSError:
        return None

    if not matches:
        return None
    return sorted(matches)[0]


__all__ = [
    "event_dir_basename_from_path",
    "find_event_dir_under_root",
    "is_event_dir_basename",
    "is_valid_event_dir",
]
