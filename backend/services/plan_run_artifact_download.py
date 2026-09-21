"""PlanRunArtifact 下载面（ADR-0033 Phase A3）。

与 JobArtifact 下载对偶：UI 从 ``DedupReportCard`` 打开 scan/merge xls，
不再要求用户抄 ``storage_uri`` 去 NFS。路径解析复用
``job_artifact_download._artifact_download_target``（同一套 file:// / http /
越权守卫）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from backend.models.plan_run_artifact import PlanRunArtifact
from backend.services.job_artifact_download import _artifact_download_target


def build_plan_run_artifact_download_response(
    db: Session,
    *,
    plan_run_id: int,
    artifact_id: int,
) -> FileResponse | RedirectResponse:
    """校验 PlanRunArtifact 归属后产出下载响应。"""
    artifact = db.get(PlanRunArtifact, artifact_id)
    if artifact is None or artifact.plan_run_id != plan_run_id:
        raise HTTPException(
            status_code=404,
            detail="artifact not found for this plan run",
        )

    target = _artifact_download_target(artifact.storage_uri)
    if target["kind"] == "redirect":
        return RedirectResponse(url=target["url"], status_code=307)

    local_path = Path(target["path"])
    media_type = None
    if local_path.suffix.lower() in {".xls", ".xlsx"}:
        media_type = "application/vnd.ms-excel"
    elif local_path.suffixes[-2:] == [".tar", ".gz"]:
        media_type = "application/gzip"
    return FileResponse(
        path=str(local_path),
        filename=local_path.name,
        media_type=media_type,
    )
