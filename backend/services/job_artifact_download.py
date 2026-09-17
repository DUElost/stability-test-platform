"""JobArtifact 下载面：两条路由入口共用的一份实现（#2420 第 4 项）。

同一资源此前有两条各自独立实现的下载路由：

- ``GET /plan-runs/{run_id}/jobs/{job_id}/artifacts/{artifact_id}/download``
  ——UI 权威入口（前端 ``planRuns.ts`` 唯一消费者），带 run↔job 配对校验，
  且对 ``run_log_bundle`` 返回 409 指引（方案 C：运行日志不再上送中心存储）；
- ``GET /runs/{run_id}/artifacts/{artifact_id}/download``（run_id 位是 **JobInstance.id**）
  ——job 域入口，脚本按 ``/results``/报告里已有的 job id 直接下载。

两份拷贝已经出现**行为漂移**：job 域入口没有 ``run_log_bundle`` 409 守卫，
对早已离开中心盘的历史 bundle 会走到路径解析、以 400/404 失败收场，而不是
像权威入口那样明确指引实时日志。本模块把校验顺序、409 守卫、redirect 与
FileResponse 形状收敛为唯一实现，两条路由都只做「解析参数 → 调本函数」。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from backend.core.artifact_paths import (
    ArtifactPathError,
    ArtifactPathNotFoundError,
    resolve_local_artifact_path,
)
from backend.models.job import JobArtifact, JobInstance


def _artifact_download_target(storage_uri: str) -> dict[str, str]:
    parsed = urlparse(storage_uri)
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"}:
        return {"kind": "redirect", "url": storage_uri}
    try:
        local_path = resolve_local_artifact_path(storage_uri, must_exist=True)
    except ArtifactPathNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ArtifactPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"kind": "local", "path": str(local_path)}


def build_artifact_download_response(
    db: Session,
    *,
    job_id: int,
    artifact_id: int,
    plan_run_id: Optional[int] = None,
) -> FileResponse | RedirectResponse:
    """校验归属并产出下载响应；两条路由共用（判据两侧同形，#2420 第 4 项）。

    ``plan_run_id`` 给出时先校验 job↔plan_run 配对（UI 权威入口的语义），
    不给时保持 job 域口径（脚本入口：只有 job id）。
    """
    job = db.get(JobInstance, job_id)
    if job is None or (plan_run_id is not None and job.plan_run_id != plan_run_id):
        raise HTTPException(
            status_code=404,
            detail=(
                "job not found in this plan run"
                if plan_run_id is not None
                else "artifact not found"
            ),
        )

    artifact = db.get(JobArtifact, artifact_id)
    if artifact is None or artifact.job_id != job_id:
        raise HTTPException(status_code=404, detail="artifact not found for this job")

    # 方案 C: run_log_bundle 运行日志不再上送中心存储。
    # 已有历史注册数据仍返回 409（历史产物不再可代理下载）。
    if artifact.artifact_type == "run_log_bundle":
        raise HTTPException(
            status_code=409,
            detail=(
                "run_log_bundle: run logs are no longer archived to NFS. "
                "Use live console / GET /api/v1/logs/query during execution, "
                "or POST /api/v1/agent/logs (SSH) for post-mortem files on the agent host."
            ),
        )

    target = _artifact_download_target(artifact.storage_uri)
    if target["kind"] == "redirect":
        return RedirectResponse(url=target["url"], status_code=307)

    local_path = Path(target["path"])
    media_type = "application/gzip" if local_path.suffixes[-2:] == [".tar", ".gz"] else None
    return FileResponse(path=str(local_path), filename=local_path.name, media_type=media_type)
