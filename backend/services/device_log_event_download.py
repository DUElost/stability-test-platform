"""DeviceLogEvent 目录/文件下载（ADR-0033 Phase A3 / #3013）。

PlanRun 详情 ``LogEventsCard`` 需要打开 DLE ``remote_path`` 树而不抄 NFS 路径。
事件目录 zip；单文件直接 FileResponse。路径守卫复用
``resolve_device_event_remote_path``（devices/ + plan_run 作用域）。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.core.artifact_paths import (
    ArtifactPathError,
    resolve_device_event_remote_path,
)
from backend.models.device_log_event import DeviceLogEvent
from backend.services.artifact_zip import build_directory_zip_response

# States that still have on-disk content under central storage.
_DOWNLOADABLE_STATES = frozenset({"REMOTE", "ARCHIVED"})


def build_device_log_event_download_response(
    db: Session,
    *,
    plan_run_id: int,
    event_id: UUID | str,
) -> FileResponse:
    """Validate DLE ownership + path scope, then file or zip response."""
    try:
        eid = event_id if isinstance(event_id, UUID) else UUID(str(event_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="log event not found") from exc

    event = db.get(DeviceLogEvent, eid)
    if event is None or event.plan_run_id != plan_run_id:
        raise HTTPException(status_code=404, detail="log event not found for this plan run")

    if event.state == "PRUNED":
        raise HTTPException(
            status_code=409,
            detail="log event pruned; on-disk archive no longer available",
        )
    if event.state not in _DOWNLOADABLE_STATES:
        raise HTTPException(
            status_code=409,
            detail=f"log event state {event.state} is not downloadable from central storage",
        )

    raw = (event.remote_path or "").strip()
    if not raw:
        raise HTTPException(
            status_code=404,
            detail="log event has no remote_path on central storage",
        )

    try:
        # must_exist=False: DLE trees are directories; resolve_local_artifact_path
        # with must_exist=True requires is_file().
        resolved = resolve_device_event_remote_path(
            raw, plan_run_id=plan_run_id, must_exist=False,
        )
    except ArtifactPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if resolved.is_symlink():
        raise HTTPException(status_code=400, detail="symlink not allowed")
    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"log event path not found: {resolved}")

    if resolved.is_file():
        return FileResponse(path=str(resolved), filename=resolved.name)

    if resolved.is_dir():
        return build_directory_zip_response(
            resolved,
            download_name=f"dle-{event.id}",
        )

    raise HTTPException(status_code=404, detail="log event path not found")
