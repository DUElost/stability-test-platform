"""ADR-0025 归档-3 / ADR-0028 Track B: DLE-backed extract (+ merge xls copy)."""

from __future__ import annotations

import logging
import shutil
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agent.aee.event_dirs import event_dir_basename_from_path
from backend.models.plan_run_artifact import PlanRunArtifact

logger = logging.getLogger(__name__)


def parse_event_dir_names_from_xls(
    xls_path: Path,
    *,
    allowed_serials: list[str] | None = None,
) -> set[str]:
    """Read merge/scan xls Path column → event directory basenames.

    Kept for scan-scope / tooling; extract no longer uses this (#213 B2).

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


def run_extract_sync(plan_run_id: int) -> int:
    """Copy DLE REMOTE/ARCHIVED event dirs + merge xls → jira/{plan_run_id}/.

    Event discovery is **only** ``list_remote_paths_for_extract`` (#213 B1).
    Merge xls Path columns are not used to find event directories.

    Returns:
      >= 0  number of items copied
      -1    no merge artifact
      -2    NFS root not configured
    """
    # CodeQL py/path-injection (#70): all callers pass an int (FastAPI path
    # param / SAQ task), but normalize defensively before any DB query or path
    # construction so a stray string can never reach jira/ as a traversal.
    from backend.core.artifact_paths import ArtifactPathError

    try:
        plan_run_id = int(plan_run_id)
    except (TypeError, ValueError) as exc:
        raise ArtifactPathError(f"invalid plan_run_id: {plan_run_id!r}") from exc

    from backend.core.storage_root import resolve_shared_storage_root
    from backend.core.database import SessionLocal

    db = SessionLocal()
    try:
        from backend.services.device_log_event import (
            associate_unassigned_events_to_plan_run,
            list_remote_paths_for_extract,
            mark_events_archived,
        )

        associate_unassigned_events_to_plan_run(db, plan_run_id)

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

        from backend.core.artifact_paths import (
            copytree_under_root,
            path_under_root,
            resolve_extract_event_src,
        )

        remote_path_rows: list[tuple[str, Path, Path]] = []
        missing_remote_paths = 0
        for raw in list_remote_paths_for_extract(db, plan_run_id):
            try:
                located = resolve_extract_event_src(
                    raw,
                    nfs_root=nfs_root,
                    legacy_root=legacy_root,
                    plan_run_id=plan_run_id,
                )
            except ArtifactPathError:
                logger.warning(
                    "dedup_extract_skip_bad_remote plan_run=%d path=%s",
                    plan_run_id, raw,
                )
                missing_remote_paths += 1
                continue
            if located is None:
                logger.debug(
                    "dedup_extract_skip_missing_remote plan_run=%d path=%s",
                    plan_run_id, raw,
                )
                missing_remote_paths += 1
                continue
            src, devices_root = located
            remote_path_rows.append((raw, src, devices_root))

        jira_dir = path_under_root(Path(nfs_root) / "jira", str(plan_run_id))
        jira_dir.mkdir(parents=True, exist_ok=True)

        extracted = 0
        event_dirs_copied = 0
        existing_dirs = 0
        merge_xls_copied = 0
        archived_remote_paths: list[str] = []
        extracted_dest_names: set[str] = set()

        by_dest: dict[str, list[tuple[str, Path, Path]]] = defaultdict(list)
        for raw, src, devices_root in remote_path_rows:
            by_dest[src.name].append((raw, src, devices_root))

        for dest_name, rows in by_dest.items():
            raw_paths = [item[0] for item in rows]
            src, devices_root = rows[0][1], rows[0][2]
            if dest_name in extracted_dest_names:
                archived_remote_paths.extend(raw_paths)
                continue
            dest = path_under_root(jira_dir, dest_name)
            if dest.exists():
                existing_dirs += 1
                extracted_dest_names.add(dest_name)
                archived_remote_paths.extend(raw_paths)
                continue
            try:
                copytree_under_root(src, dest, root=devices_root, dest_root=jira_dir)
                extracted += 1
                event_dirs_copied += 1
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

        if archived_remote_paths:
            mark_events_archived(db, plan_run_id, archived_remote_paths)

        for row in merge_rows:
            merge_xls = Path(row.storage_uri)
            if not merge_xls.is_file():
                continue
            try:
                dest = path_under_root(jira_dir, merge_xls.name)
            except ArtifactPathError:
                logger.warning(
                    "dedup_extract_skip_unsafe_merge_name plan_run=%d name=%r",
                    plan_run_id, merge_xls.name,
                )
                continue
            if dest.exists():
                continue
            try:
                shutil.copy2(str(merge_xls), str(dest))
                extracted += 1
                merge_xls_copied += 1
            except Exception:
                logger.exception(
                    "dedup_extract_merge_xls_failed plan_run=%d path=%s",
                    plan_run_id, merge_xls,
                )

        from backend.services.plan_run_context import write_run_context_section

        write_run_context_section(db, plan_run_id, "extract", {
            # 含 missing/bad 路径：targets 是发现的总数，缺口单列。
            "targets": len(remote_path_rows) + missing_remote_paths,
            "copied": event_dirs_copied,
            "missing": missing_remote_paths,
            "existing": existing_dirs,
            "merge_xls_copied": merge_xls_copied,
            "archived": len(archived_remote_paths),
        })
        logger.info(
            "dedup_extract_done plan_run=%d extracted=%d remote_paths=%d",
            plan_run_id, extracted, len(remote_path_rows),
        )
        return extracted
    finally:
        db.close()


def collect_upload_event_dir_names(db: Session, plan_run_id: int) -> list[str]:
    """ADR-0028 方案 A：从 scan xls Path 列提取事件目录名（过滤模型——只上送 scan 引用的有效事件）。

    不再 union JobLogSignal 路径（#213 Track B 已废弃 basename union）。
    """
    from backend.services.plan_run_scan_scope import load_plan_run_device_serials

    serials = load_plan_run_device_serials(db, plan_run_id)
    names: set[str] = set()

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


__all__ = [
    "collect_upload_event_dir_names",
    "parse_event_dir_names_from_xls",
    "run_extract_sync",
]
