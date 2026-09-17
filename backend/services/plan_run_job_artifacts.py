"""PlanRun Job 产物列表（#1520 垂直切片：GET .../jobs/{id}/artifacts）。

下载仍走 ``job_artifact_download.build_artifact_download_response``（路由薄壳）。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.job import JobArtifact, JobInstance
from backend.services.plan_run_read_common import iso


def list_plan_run_job_artifacts(
    db: Session,
    run_id: int,
    job_id: int,
) -> list[dict[str, Any]]:
    job = db.get(JobInstance, job_id)
    if job is None or job.plan_run_id != run_id:
        raise HTTPException(status_code=404, detail="job not found in this plan run")

    artifacts = db.execute(
        select(JobArtifact).where(JobArtifact.job_id == job_id)
    ).scalars().all()
    return [
        {
            "id": a.id,
            "job_id": a.job_id,
            "filename": a.storage_uri.rsplit("/", 1)[-1] if a.storage_uri else None,
            "artifact_type": a.artifact_type,
            "size_bytes": a.size_bytes,
            "checksum": a.checksum,
            "created_at": iso(a.created_at),
        }
        for a in artifacts
    ]
