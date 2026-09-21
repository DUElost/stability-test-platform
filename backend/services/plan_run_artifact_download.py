"""PlanRunArtifact 下载面（ADR-0033 Phase A3）。

与 JobArtifact 下载对偶：UI 从 ``DedupReportCard`` 打开 scan/merge xls，
以及 extract 后的 ``extract_bundle``（``jira/{plan_run_id}/`` 目录 zip）。
路径解析复用 ``job_artifact_download._artifact_download_target``（file:// /
http / 越权守卫）；目录类型走 ``artifact_zip``。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from backend.core.artifact_paths import (
    ArtifactPathError,
    resolve_local_artifact_path,
)
from backend.models.plan_run_artifact import PlanRunArtifact
from backend.services.artifact_zip import build_directory_zip_response
from backend.services.job_artifact_download import _artifact_download_target

ARTIFACT_TYPE_EXTRACT_BUNDLE = "extract_bundle"


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

    # extract_bundle is a directory under jira/{plan_run_id}/ — zip it.
    if artifact.artifact_type == ARTIFACT_TYPE_EXTRACT_BUNDLE:
        return _download_extract_bundle(artifact)

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


def _download_extract_bundle(artifact: PlanRunArtifact) -> FileResponse | RedirectResponse:
    parsed = urlparse(artifact.storage_uri or "")
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"}:
        return RedirectResponse(url=artifact.storage_uri, status_code=307)
    try:
        local_path = resolve_local_artifact_path(
            artifact.storage_uri, must_exist=False,
        )
    except ArtifactPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if local_path.is_symlink() or not local_path.exists():
        raise HTTPException(status_code=404, detail=f"extract bundle not found: {local_path}")
    if local_path.is_file():
        return FileResponse(
            path=str(local_path),
            filename=local_path.name,
            media_type="application/zip" if local_path.suffix.lower() == ".zip" else None,
        )
    if local_path.is_dir():
        return build_directory_zip_response(
            local_path,
            download_name=f"extract-bundle-{artifact.plan_run_id}",
        )
    raise HTTPException(status_code=404, detail="extract bundle not found")
