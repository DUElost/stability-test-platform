"""PlanRun-scoped AEE scan roots: only `{folder}/{serial}/` for this run.

ScanAeeTne walks `-d` with `os.walk(followlinks=False)`, so the scoped tree
must be real directories (hardlink files), not symlinks.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from datetime import datetime
from typing import Any, Iterable, Sequence

try:
    import xlrd
except ImportError:
    xlrd = None

from .device_log_event_client import DeviceLogEventClient, DeviceLogEventListError
from ..contracts.occurrence_key import compute_occurrence_key
from ..contracts.evidence import event_evidence
from ..contracts.scan_device_id import (
    UNKNOWN_DEVICE_ID_VALUES as UNKNOWN_DEVICE_ID_VALUES,
    normalize_device_id,
)

logger = logging.getLogger(__name__)

class ScanReportMappingError(ValueError):
    """A scan report rejected by ``report_mapping`` with an operator category."""

    def __init__(self, reason: str, *, row: int | None, detail: str) -> None:
        self.reason = reason
        self.row = row
        super().__init__(detail)


def is_safe_path_token(value: str) -> bool:
    """Reject empty / absolute / traversal tokens used as serial or MMDD."""
    if not value or value.strip() != value:
        return False
    if value in (".", "..") or ".." in value:
        return False
    if "/" in value or "\\" in value or os.sep in value:
        return False
    return not os.path.isabs(value)


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
        if item not in seen and is_safe_path_token(item):
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
    try:
        root_resolved = root.resolve()
    except OSError:
        return []
    serial_set = [s for s in serials if is_safe_path_token(s)]
    stamp_set = [s for s in stamps if is_safe_path_token(s)]
    if root.is_symlink() or not root.is_dir() or not serial_set:
        return []

    matched: list[Path] = []
    try:
        for folder in sorted(root.iterdir()):
            if folder.is_symlink() or not folder.is_dir() or folder.name.startswith("."):
                continue
            if stamp_set and not any(
                folder_matches_run_date_stamp(folder.name, stamp)
                for stamp in stamp_set
            ):
                continue
            for serial in serial_set:
                serial_dir = folder / serial
                if serial_dir.is_symlink() or not serial_dir.is_dir():
                    continue
                try:
                    resolved = serial_dir.resolve()
                    resolved.relative_to(root_resolved)
                except (OSError, ValueError):
                    continue
                if resolved.parent.name != folder.name or resolved.name != serial:
                    continue
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


def metadata_occurrence_key(metadata: Any, *, platform: str, serial: str) -> str | None:
    """Only collector-provided device originals can prove a watcher/scan identity."""
    def original(name: str) -> str:
        value = getattr(metadata, name, None)
        return value if isinstance(value, str) else ""

    return compute_occurrence_key(
        platform=platform, serial=serial, db_path=original("db_path"),
        aee_ts=original("device_timestamp_raw"), raw_event_type=original("raw_event_type"),
    )


def load_scan_events(host_id: str, plan_run_id: int) -> list[dict] | None:
    """Read only this host/run; None denotes unavailable, [] denotes a valid empty result."""
    client = DeviceLogEventClient.from_env(
        api_url=os.getenv("API_URL", "http://127.0.0.1:8000"), agent_secret=os.getenv("AGENT_SECRET", ""),
        host_id=host_id,
    )
    try:
        if client is None:
            raise DeviceLogEventListError("client not configured")
        events = client.list_events(state="", limit=None, plan_run_id=plan_run_id, raise_on_error=True)
    except DeviceLogEventListError as exc:
        logger.warning("scan_mapping_events_unavailable host=%s plan_run=%d reason=%s", host_id, plan_run_id, exc)
        return None
    return [ev for ev in events if ev.get("plan_run_id") == plan_run_id and ev.get("host_id") == host_id]


def scan_sources(hdd: Path, scan_root: Path, events: Sequence[dict]) -> list[dict]:
    sources = []
    for ev in events:
        if not ev.get("local_path"):
            continue
        local = Path(ev["local_path"])
        try:
            rel = local.resolve().relative_to(hdd.resolve())
        except (OSError, ValueError):
            continue
        staged = scan_root / rel
        if not staged.is_dir():
            continue
        sources.append({**ev, "source": "local", "scan_path": str(staged)})
    return sources


def stage_spill_inputs(
    hdd: Path, scan_root: Path, nfs_root: Path, plan_run_id: int,
    serials: Sequence[str], spill_inputs: Sequence[dict], events: Sequence[dict],
) -> list[dict]:
    """Materialize verified copies without replacing a simultaneous local source.

    The DLE supplies the original local layout and serial; the payload supplies
    its verified remote location and occurrence key. Neither is inferred from xls.
    """
    by_id = {str(ev["id"]): ev for ev in events}
    sources = []
    temporary_bytes = 0
    for item in spill_inputs:
        dle_id = str(item["dle_id"])
        if not is_safe_path_token(dle_id):
            raise ValueError("unsafe spill dle_id")
        ev = by_id.get(dle_id)
        if ev is None or ev.get("serial") not in serials or ev.get("platform") != "MTK":
            raise ValueError(f"spill observation unavailable or outside partition: {dle_id}")
        remote = Path(item["remote_path"])
        expected = nfs_root.resolve() / "devices" / str(plan_run_id) / dle_id
        if not remote.resolve().is_relative_to(expected) or not remote.is_dir():
            raise ValueError(f"spill copy outside partition or missing: {dle_id}")
        if str(remote) != ev.get("remote_path"):
            raise ValueError(f"spill location differs from DLE: {dle_id}")
        if item.get("occurrence_key") != ev.get("occurrence_key"):
            raise ValueError(f"spill identity differs from DLE: {dle_id}")
        rel = Path(ev["local_path"]).resolve().relative_to(hdd.resolve())
        if len(rel.parts) < 3 or rel.parts[1] != ev["serial"]:
            raise ValueError(f"spill local layout unavailable: {dle_id}")
        dest = scan_root / "spill_inputs" / dle_id / rel
        _hardlink_tree(remote, dest)
        temporary_bytes += sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())
        sources.append({**ev, "source": "spill", "scan_path": str(dest)})
    logger.info("scan_spill_staging events=%d temporary_bytes=%d root=%s",
                len(sources), temporary_bytes, scan_root)
    return sources


def report_mapping(
    xls_path: str, *, serials: Sequence[str], sources: Sequence[dict],
    scan_root: str, plan_run_id: int, host_id: str, platform: str,
    fact_round_id: int | None = None, require_serial_evidence: bool = False,
    events_unavailable: bool = False,
) -> dict:
    """Validate every data row and map only exact, unambiguous source paths.

    Row numbers are one-based spreadsheet positions (the header is row 1).
    An unmapped row retains its original Path and explicit null identity fields.
    """
    if xlrd is None:
        raise RuntimeError("scan report validation requires Agent dependency xlrd==2.0.2")
    source_roots = [
        (src, Path(src["scan_path"]).resolve(),
         Path(src["local_path"]).resolve() if src["source"] == "local" else None)
        for src in sources
    ]
    root = Path(scan_root).resolve()
    allowed_serials = set(serials)
    book = xlrd.open_workbook(xls_path, on_demand=True)
    rows = []
    try:
        for sheet in book.sheets():
            if not sheet.nrows:
                continue
            columns = {str(sheet.cell_value(0, c)).strip().lower(): c
                       for c in range(sheet.ncols)}
            if "path" not in columns:
                raise ScanReportMappingError(
                    "missing_path", row=1, detail="scan report lacks Path column",
                )
            for index in range(1, sheet.nrows):
                raw = str(sheet.cell_value(index, columns["path"]))
                path = Path(raw.replace("\\", "/")).resolve()
                matches = [src for src, staged, local in source_roots
                           if path.is_relative_to(staged) or (local is not None and path.is_relative_to(local))]
                # Only equal, nonempty occurrence keys prove duplicate observations.
                match = matches[0] if len(matches) == 1 else None
                duplicate_ids = []
                duplicate_ambiguous = False
                if platform.lower() == "mtk" and len(matches) > 1:
                    keys = {src.get("occurrence_key") for src in matches}
                    if len(keys) == 1 and all(src.get("occurrence_key") for src in matches):
                        def observation_order(src):
                            observed = src["detected_at"]
                            if isinstance(observed, str):
                                observed = datetime.fromisoformat(observed)
                            return observed, str(src["id"])
                        ordered = sorted(matches, key=observation_order)
                        match = ordered[0]
                        duplicate_ids = [str(src["id"]) for src in ordered[1:]]
                    else:
                        duplicate_ambiguous = True
                row_serials = set()
                if len(matches) == 1:
                    row_serials.add(matches[0]["serial"])
                for name in ("serial", "device_id"):
                    if name in columns:
                        value = normalize_device_id(sheet.cell_value(index, columns[name]))
                        if value:
                            row_serials.add(value)
                if "detail" in columns:
                    detail = str(sheet.cell_value(index, columns["detail"]))
                    for value in re.findall(r"Device_id:\s*([^\s,;]+)", detail):
                        value = normalize_device_id(value)
                        if value:
                            row_serials.add(value)
                try:
                    rel = path.relative_to(root)
                    parts = rel.parts[2:] if rel.parts[:1] == ("spill_inputs",) else rel.parts
                    if len(parts) >= 3:
                        row_serials.add(parts[1])
                except ValueError:
                    # External paths are not eligible for identity inference.
                    pass
                if not row_serials:
                    raise ScanReportMappingError(
                        "unprovable", row=index + 1,
                        detail=f"scan report serial evidence unprovable at row {index + 1}",
                    )
                if not row_serials.issubset(allowed_serials):
                    raise ScanReportMappingError(
                        "outside_run", row=index + 1,
                        detail=f"scan report serial outside run at row {index + 1}",
                    )
                extra = {}
                if platform.lower() == "mtk":
                    if match:
                        extra["evidence"] = event_evidence(match["scan_path"])
                        extra["duplicate_dle_ids"] = duplicate_ids
                    else:
                        extra["unmapped_reason"] = "ambiguous_duplicate" if duplicate_ambiguous else "no_source"
                rows.append({**extra,
                    "sheet": sheet.name, "row": index + 1, "path": raw,
                    "dle_id": str(match["id"]) if match else None,
                    "occurrence_key": match.get("occurrence_key") if match else None,
                    "source": match["source"] if match else None,
                    "mapped": match is not None,
                })
        if require_serial_evidence and not rows:
            raise ScanReportMappingError(
                "no_rows", row=0,
                detail="timed-out report has no run serial evidence",
            )
    finally:
        book.release_resources()
    result = {"schema_version": 1, "plan_run_id": plan_run_id, "host_id": host_id,
              "platform": platform, "rows": rows}
    if fact_round_id is not None:
        result["fact_round_id"] = fact_round_id
    if events_unavailable:
        result["events_unavailable"] = True
    return result
