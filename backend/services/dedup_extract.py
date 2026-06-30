"""ADR-0025 归档-3: event directory discovery and selective extract."""

from __future__ import annotations

import logging
import shutil
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


def parse_event_dir_names_from_xls(xls_path: Path) -> set[str]:
    """Read merge/scan xls Path column → event directory basenames."""
    names: set[str] = set()
    if not xls_path.is_file():
        return names
    try:
        import xlrd
    except ImportError:
        logger.warning("dedup_xls_parse_skip_no_xlrd path=%s", xls_path)
        return names

    try:
        book = xlrd.open_workbook(str(xls_path))
        sheet = book.sheet_by_index(0)
        if sheet.nrows < 2 or sheet.ncols < 1:
            return names
        headers = [
            str(sheet.cell_value(0, col)).strip() for col in range(sheet.ncols)
        ]
        path_col = next(
            (idx for idx, header in enumerate(headers) if header.lower() == "path"),
            None,
        )
        if path_col is None:
            return names
        for row in range(1, sheet.nrows):
            raw = sheet.cell_value(row, path_col)
            if raw is None or str(raw).strip() == "":
                continue
            name = event_dir_basename_from_path(str(raw))
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
    names = collect_event_dir_names_from_log_signals(db, plan_run_id)

    scan_rows = db.execute(
        select(PlanRunArtifact).where(
            PlanRunArtifact.plan_run_id == plan_run_id,
            PlanRunArtifact.artifact_type == "scan_result_xls",
        )
    ).scalars().all()
    for row in scan_rows:
        if not row.storage_uri:
            continue
        names |= parse_event_dir_names_from_xls(Path(row.storage_uri))

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
    import os

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

        nfs_root = os.getenv(
            "STP_AEE_NFS_ROOT", os.getenv("STP_WATCHER_NFS_BASE_DIR", "")
        ).strip()
        if not nfs_root:
            logger.warning("dedup_extract_skip_no_nfs plan_run=%d", plan_run_id)
            return -2

        target_names = collect_extract_event_dir_names(db, plan_run_id)
        devices_dir = Path(nfs_root) / "devices" / str(plan_run_id)
        jira_dir = Path(nfs_root) / "jira" / str(plan_run_id)
        jira_dir.mkdir(parents=True, exist_ok=True)

        extracted = 0
        for name in sorted(target_names):
            src = devices_dir / name
            if not src.is_dir():
                logger.debug(
                    "dedup_extract_skip_missing plan_run=%d name=%s", plan_run_id, name,
                )
                continue
            dest = jira_dir / name
            if dest.exists():
                continue
            try:
                shutil.copytree(str(src), str(dest))
                extracted += 1
            except Exception:
                logger.exception(
                    "dedup_extract_event_dir_failed plan_run=%d dir=%s",
                    plan_run_id, src,
                )

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
