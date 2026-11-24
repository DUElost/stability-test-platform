"""PlanRun Job 产物列表（#1520 垂直切片：GET .../jobs/{id}/artifacts）。

下载仍走 ``job_artifact_download.build_artifact_download_response``（路由薄壳）。
"""

from __future__ import annotations

import re

from backend.services.errors import NotFound
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.schemas.plan_run import PlanRunJobArtifactOut
from backend.models.job import JobArtifact, JobInstance
from backend.services.plan_run_read_common import iso

_LEADING_TS_PREFIX_RE = re.compile(r"^(?:\d+_)+")
_AEE_ARTIFACT_TYPES = frozenset({"aee_crash", "vendor_aee_crash"})
_NON_ISSUE_AEE_EXACT_NAMES = frozenset(
    {
        "aee.lck",
        "db_history",
        "file_tree.txt",
        "swt_jbt_traces",
        "temp",
    }
)
_NON_ISSUE_AEE_SUFFIXES = (".lck", ".lock", ".tmp", ".temp")


def is_non_issue_aee_artifact_filename(filename: str | None) -> bool:
    """判断 AEE 目录下伴生的锁/索引/树形元数据文件（并非实际崩溃日志）。"""
    if not filename:
        return False
    base = filename.rsplit("/", 1)[-1].strip()
    if not base:
        return False
    normalized = _LEADING_TS_PREFIX_RE.sub("", base).lower()
    if not normalized:
        return False
    if normalized in _NON_ISSUE_AEE_EXACT_NAMES:
        return True
    if normalized.endswith(_NON_ISSUE_AEE_SUFFIXES):
        return True
    if normalized.startswith(("db_history.", "swt_jbt_traces")):
        return True
    return False


def list_plan_run_job_artifacts(
    db: Session,
    run_id: int,
    job_id: int,
) -> list[PlanRunJobArtifactOut]:
    job = db.get(JobInstance, job_id)
    if job is None or job.plan_run_id != run_id:
        raise NotFound("job not found in this plan run")

    artifacts = db.execute(
        select(JobArtifact).where(JobArtifact.job_id == job_id)
    ).scalars().all()
    results: list[PlanRunJobArtifactOut] = []
    for a in artifacts:
        filename = a.storage_uri.rsplit("/", 1)[-1] if a.storage_uri else None
        if a.artifact_type in _AEE_ARTIFACT_TYPES and is_non_issue_aee_artifact_filename(
            filename
        ):
            continue
        results.append(
            PlanRunJobArtifactOut(
                id=a.id,
                job_id=a.job_id,
                filename=filename,
                artifact_type=a.artifact_type,
                size_bytes=a.size_bytes,
                checksum=a.checksum,
                created_at=iso(a.created_at),
            )
        )
    return results
