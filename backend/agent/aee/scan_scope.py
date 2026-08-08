"""PlanRun-scoped AEE scan roots: only `{folder}/{serial}/` for this run.

ScanAeeTne walks `-d` with `os.walk(followlinks=False)`, so the scoped tree
must be real directories (hardlink files), not symlinks.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Iterable, Sequence

logger = logging.getLogger(__name__)


def normalize_str_list(value: object) -> list[str]:
    """Coerce SocketIO / argv payload into a de-duplicated str list."""
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.replace(";", ",").replace(":", ",").split(",")
        items = [p.strip() for p in parts if p.strip()]
    elif isinstance(value, (list, tuple, set)):
        items = [str(v).strip() for v in value if str(v).strip()]
    else:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def folder_matches_run_date_stamp(folder_name: str, stamp: str) -> bool:
    """True if MonkeyAEEinfo folder name belongs to MMDD stamp (e.g. 0808)."""
    if not folder_name or not stamp:
        return False
    return f"_{stamp}_" in folder_name or folder_name.endswith(f"_{stamp}")


def iter_serial_scan_dirs(
    hdd_root: Path,
    serials: Sequence[str],
    stamps: Sequence[str] = (),
) -> list[Path]:
    """Existing `{folder}/{serial}` dirs matching serials (and optional MMDD)."""
    root = Path(hdd_root)
    serial_set = [s for s in serials if s]
    stamp_set = [s for s in stamps if s]
    if not root.is_dir() or not serial_set:
        return []

    matched: list[Path] = []
    try:
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            if stamp_set and not any(
                folder_matches_run_date_stamp(folder.name, stamp)
                for stamp in stamp_set
            ):
                continue
            for serial in serial_set:
                serial_dir = folder / serial
                if serial_dir.is_dir():
                    matched.append(serial_dir)
    except OSError:
        logger.warning("iter_serial_scan_dirs_failed root=%s", root, exc_info=True)
        return []
    return matched


def _link_or_copy_file(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _hardlink_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for entry in src.iterdir():
        dest_entry = dst / entry.name
        if entry.is_dir() and not entry.is_symlink():
            _hardlink_tree(entry, dest_entry)
        elif entry.is_file() and not entry.is_symlink():
            _link_or_copy_file(entry, dest_entry)


def build_scoped_scan_root(
    hdd_root: Path,
    dest_root: Path,
    serials: Sequence[str],
    stamps: Sequence[str] = (),
) -> Path:
    """Populate ``dest_root`` with hardlinked `{folder}/{serial}` trees.

    Returns ``dest_root`` (created even when nothing matched, so scan still
    produces an empty org xls instead of falling back to host-wide HDD).
    """
    dest = Path(dest_root)
    dest.mkdir(parents=True, exist_ok=True)
    dirs = iter_serial_scan_dirs(hdd_root, serials, stamps)
    for serial_dir in dirs:
        rel = serial_dir.relative_to(Path(hdd_root))
        _hardlink_tree(serial_dir, dest / rel)
    logger.info(
        "scoped_scan_root dest=%s serials=%s stamps=%s dirs=%d",
        dest, list(serials), list(stamps), len(dirs),
    )
    return dest


def path_has_serial(path: str, serials: Iterable[str]) -> bool:
    """True if ``path`` contains any serial as a full path component."""
    if not path:
        return False
    parts = {p for p in path.replace("\\", "/").split("/") if p}
    return any(serial in parts for serial in serials if serial)
