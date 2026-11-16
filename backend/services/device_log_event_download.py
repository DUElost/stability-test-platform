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
    resolve_evidence_event_path,
)
from backend.models.device_log_event import DeviceLogEvent
from backend.services import issue_fact_store as facts
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

    # B8 G7 / D16：先去重分区的代表证据以 G6 已校验发布记录为准（D9 第 6–7 条）。
    # 注册表是唯一索引；不得按主文件夹推断归属。DLE.remote_path 仍供溢出补扫，
    # 只在没有已校验发布记录时使用。
    publication = facts.find_verified_publication_for_dle(
        db, plan_run_id=plan_run_id, dle_id=eid, platform=event.platform,
    )
    if publication is not None and publication.destination_rel_path:
        try:
            resolved = resolve_evidence_event_path(
                publication.destination_rel_path,
                root_plan_run_id=publication.root_plan_run_id,
                serial=event.serial,
                must_exist=True,
            )
        except ArtifactPathError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if resolved.is_dir():
            return build_directory_zip_response(
                resolved, download_name=f"dle-{event.id}",
            )
        raise HTTPException(status_code=404, detail="evidence path is not a directory")

    # B8 G7 / B2：先去重分区的 DLE 下载只提供同一实际 run、平台和 DLE 的
    # VERIFIED 发布证据。没有已校验记录时不得回退到 DLE.remote_path——
    # 那里是 HddSpill 溢出源（补扫输入），不是可交付证据。旧路径仅保留给
    # CURRENT 与无模式存量（D13 第 2 条：无记录按现行解释）。
    partition = facts.get_partition_mode(
        db, plan_run_id=plan_run_id, platform=event.platform or "",
    )
    if partition is not None and partition.mode == facts.PARTITION_MODE_DEDUP_FIRST:
        raise HTTPException(
            status_code=409,
            detail=(
                "log event download in dedup-first partition requires a "
                "verified evidence publication"
            ),
        )

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
            raw, plan_run_id=plan_run_id, must_exist=False, allow_legacy=True,
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
