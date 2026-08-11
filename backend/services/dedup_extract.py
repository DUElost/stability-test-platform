"""ADR-0025 归档-3: event directory discovery and selective extract."""

from __future__ import annotations

import logging
import shutil
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.agent.aee.event_dirs import (
    event_dir_basename_from_path,
    is_event_dir_basename,
)
from backend.models.job import JobInstance, JobLogSignal
from backend.models.plan_run_artifact import PlanRunArtifact

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _storage_roots_for_extract(primary: str, legacy: str) -> list[str]:
    roots = [primary]
    if legacy and legacy != primary:
        roots.append(legacy)
    return roots


def parse_event_dir_names_from_xls(
    xls_path: Path,
    *,
    allowed_serials: list[str] | None = None,
) -> set[str]:
    """Read merge/scan xls Path column → event directory basenames.

    ``allowed_serials=None`` keeps every row. A provided list (including empty)
    keeps only matching PlanRun devices; empty means no xls rows.
    """
    names: set[str] = set()
    if not xls_path.is_file():
        return names
    try:
        import xlrd
    except ImportError:
        logger.warning("dedup_xls_parse_skip_no_xlrd path=%s", xls_path)
        return names

    try:
        from backend.services.plan_run_scan_scope import xls_row_matches_serials

        book = xlrd.open_workbook(str(xls_path))
        sheet = book.sheet_by_index(0)
        if sheet.nrows < 2 or sheet.ncols < 1:
            return names
        headers = [
            str(sheet.cell_value(0, col)).strip() for col in range(sheet.ncols)
        ]
        lower = [h.lower() for h in headers]
        path_col = next(
            (idx for idx, header in enumerate(lower) if header == "path"),
            None,
        )
        detail_col = next(
            (idx for idx, header in enumerate(lower) if header == "detail"),
            None,
        )
        if path_col is None:
            return names
        if allowed_serials is None:
            serials: list[str] | None = None
        else:
            serials = [s for s in allowed_serials if s]
            if not serials:
                return names
        for row in range(1, sheet.nrows):
            raw = sheet.cell_value(row, path_col)
            if raw is None or str(raw).strip() == "":
                continue
            path = str(raw)
            detail = ""
            if detail_col is not None:
                detail = str(sheet.cell_value(row, detail_col) or "")
            if serials is not None and not xls_row_matches_serials(path, detail, serials):
                continue
            name = event_dir_basename_from_path(path)
            if name:
                names.add(name)
    except Exception:
        logger.exception("dedup_xls_parse_failed path=%s", xls_path)
    return names


def collect_event_dir_names_from_log_signals(db: Session, plan_run_id: int) -> set[str]:
    """Collect event basenames from JobLogSignal nfs_path / artifact_uri."""
    job_ids = db.execute(
        select(JobInstance.id).where(JobInstance.plan_run_id == plan_run_id)
    ).scalars().all()
    if not job_ids:
        return set()

    names: set[str] = set()
    signals = db.execute(
        select(JobLogSignal).where(JobLogSignal.job_id.in_(job_ids))
    ).scalars().all()
    for signal in signals:
        for raw in (signal.artifact_uri, signal.path_on_device):
            if not raw:
                continue
            name = event_dir_basename_from_path(str(raw))
            if name:
                names.add(name)
        extra = signal.extra if isinstance(signal.extra, dict) else {}
        nfs_path = extra.get("nfs_path")
        if nfs_path:
            name = event_dir_basename_from_path(str(nfs_path))
            if name:
                names.add(name)
    return names


def collect_upload_event_dir_names(db: Session, plan_run_id: int) -> list[str]:
    """ADR-0025: union JobLogSignal paths + scan xls Path rows for upload."""
    from backend.services.plan_run_scan_scope import load_plan_run_device_serials

    names = collect_event_dir_names_from_log_signals(db, plan_run_id)
    serials = load_plan_run_device_serials(db, plan_run_id)

    scan_rows = db.execute(
        select(PlanRunArtifact).where(
            PlanRunArtifact.plan_run_id == plan_run_id,
            PlanRunArtifact.artifact_type == "scan_result_xls",
        )
    ).scalars().all()
    for row in scan_rows:
        if not row.storage_uri:
            continue
        names |= parse_event_dir_names_from_xls(
            Path(row.storage_uri), allowed_serials=serials,
        )

    return sorted(names)


def collect_extract_event_dir_names(db: Session, plan_run_id: int) -> set[str]:
    """ADR-0025 归档-3: event dirs referenced by merge Result xls only."""
    names: set[str] = set()
    merge_rows = db.execute(
        select(PlanRunArtifact).where(
            PlanRunArtifact.plan_run_id == plan_run_id,
            PlanRunArtifact.artifact_type == "merge_result_xls",
        )
    ).scalars().all()
    for row in merge_rows:
        if not row.storage_uri:
            continue
        names |= parse_event_dir_names_from_xls(Path(row.storage_uri))
    return names


def run_extract_sync(plan_run_id: int) -> int:
    """Copy merge-referenced event dirs + merge xls → jira/{plan_run_id}/.

    Returns:
      >= 0  number of items copied
      -1    no merge artifact
      -2    NFS root not configured
    """
    from backend.core.storage_root import resolve_shared_storage_root
    from backend.core.database import SessionLocal

    db = SessionLocal()
    try:
        merge_rows = db.execute(
            select(PlanRunArtifact).where(
                PlanRunArtifact.plan_run_id == plan_run_id,
                PlanRunArtifact.artifact_type == "merge_result_xls",
            )
        ).scalars().all()
        if not merge_rows:
            logger.warning("dedup_extract_skip_no_merge plan_run=%d", plan_run_id)
            return -1

        nfs_root = resolve_shared_storage_root()
        if not nfs_root:
            logger.warning("dedup_extract_skip_no_nfs plan_run=%d", plan_run_id)
            return -2

        from backend.core.storage_root import resolve_legacy_shared_storage_root

        legacy_root = resolve_legacy_shared_storage_root()

        target_names = collect_extract_event_dir_names(db, plan_run_id)
        from backend.services.device_log_event import (
            continuous_event_upload_enabled,
            list_remote_paths_for_extract,
            mark_events_archived,
        )

        remote_path_rows: list[tuple[str, Path, Path]] = []
        if continuous_event_upload_enabled():
            from backend.core.artifact_paths import (
                ArtifactPathError,
                resolve_extract_event_src,
            )

            for raw in list_remote_paths_for_extract(db, plan_run_id):
                located = resolve_extract_event_src(
                    raw,
                    nfs_root=nfs_root,
                    legacy_root=legacy_root,
                    plan_run_id=plan_run_id,
                )
                if located is None:
                    logger.debug(
                        "dedup_extract_skip_missing_remote plan_run=%d path=%s",
                        plan_run_id, raw,
                    )
                    continue
                src, devices_root = located
                remote_path_rows.append((raw, src, devices_root))

        jira_dir = Path(nfs_root) / "jira" / str(plan_run_id)
        jira_dir.mkdir(parents=True, exist_ok=True)

        extracted = 0
        archived_remote_paths: list[str] = []
        extracted_dest_names: set[str] = set()
        from backend.core.artifact_paths import ArtifactPathError, copytree_under_root

        by_dest: dict[str, list[tuple[str, Path, Path]]] = defaultdict(list)
        for raw, src, devices_root in remote_path_rows:
            by_dest[src.name].append((raw, src, devices_root))

        for dest_name, rows in by_dest.items():
            raw_paths = [item[0] for item in rows]
            src, devices_root = rows[0][1], rows[0][2]
            if dest_name in extracted_dest_names:
                archived_remote_paths.extend(raw_paths)
                continue
            dest = jira_dir / dest_name
            if dest.exists():
                extracted_dest_names.add(dest_name)
                archived_remote_paths.extend(raw_paths)
                continue
            try:
                copytree_under_root(src, dest, root=devices_root, dest_root=jira_dir)
                extracted += 1
                extracted_dest_names.add(dest_name)
                archived_remote_paths.extend(raw_paths)
            except ArtifactPathError:
                logger.warning(
                    "dedup_extract_skip_unsafe_remote plan_run=%d paths=%s",
                    plan_run_id, raw_paths,
                )
            except Exception:
                logger.exception(
                    "dedup_extract_remote_dir_failed plan_run=%d dir=%s",
                    plan_run_id, src,
                )

        from backend.core.artifact_paths import resolve_extract_event_src

        for name in sorted(target_names):
            if name in extracted_dest_names:
                continue
            if not name or ".." in name or name.startswith(("/", "\\")):
                logger.warning(
                    "dedup_extract_skip_unsafe_name plan_run=%d name=%r",
                    plan_run_id, name,
                )
                continue
            located = resolve_extract_event_src(
                name,
                nfs_root=nfs_root,
                legacy_root=legacy_root,
                plan_run_id=plan_run_id,
            )
            if located is None:
                logger.debug(
                    "dedup_extract_skip_missing plan_run=%d name=%s", plan_run_id, name,
                )
                continue
            src, devices_root = located
            dest = jira_dir / name
            if dest.exists():
                extracted_dest_names.add(name)
                continue
            try:
                copytree_under_root(src, dest, root=devices_root, dest_root=jira_dir)
                extracted += 1
                extracted_dest_names.add(name)
            except ArtifactPathError:
                logger.warning(
                    "dedup_extract_skip_outside_devices plan_run=%d path=%s",
                    plan_run_id, src,
                )
            except Exception:
                logger.exception(
                    "dedup_extract_event_dir_failed plan_run=%d dir=%s",
                    plan_run_id, src,
                )

        if archived_remote_paths:
            mark_events_archived(db, plan_run_id, archived_remote_paths)

        for row in merge_rows:
            merge_xls = Path(row.storage_uri)
            if not merge_xls.is_file():
                continue
            dest = jira_dir / merge_xls.name
            if dest.exists():
                continue
            try:
                shutil.copy2(str(merge_xls), str(dest))
                extracted += 1
            except Exception:
                logger.exception(
                    "dedup_extract_merge_xls_failed plan_run=%d path=%s",
                    plan_run_id, merge_xls,
                )

        logger.info(
            "dedup_extract_done plan_run=%d extracted=%d targets=%d",
            plan_run_id, extracted, len(target_names),
        )
        return extracted
    finally:
        db.close()


__all__ = [
    "collect_extract_event_dir_names",
    "collect_upload_event_dir_names",
    "is_event_dir_basename",
    "parse_event_dir_names_from_xls",
    "run_extract_sync",
]
