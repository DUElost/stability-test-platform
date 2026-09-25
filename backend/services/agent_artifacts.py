"""Agent JobArtifact 摄取（#1520 垂直切片：agent_api /artifacts）。

``POST /jobs/{job_id}/artifacts``：路径校验 → 类型白名单 → upload lease fencing →
PG ON CONFLICT 幂等入库。

路由退化为 ``ok(await ingest_agent_artifact(...))``。
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.artifact_paths import (
    ArtifactPathError,
    resolve_local_artifact_path,
)
from backend.models.job import JobArtifact, JobInstance
from backend.services.agent_log_signals import require_job_bound_upload_lease
from backend.services.errors import BadRequest, Conflict, NotFound

logger = logging.getLogger(__name__)


# 首期只接受 watcher LogPuller 产出的 crash 实文件 + 可选 bugreport。
# 故意不放开 ANR / MOBILELOG：
#   - ANR / MOBILELOG 在 JobLogSignal 里已经有 path_on_device / first_lines 元数据
#   - 文件本身体量大、价值低，不值得入 JobArtifact 展示/下载通道
_ARTIFACT_TYPE_WHITELIST: set[str] = {"aee_crash", "vendor_aee_crash", "bugreport"}


class ArtifactIn(BaseModel):
    """Agent watcher 上送的单个产物。

    幂等键：(job_id, storage_uri)
    首期边界：artifact_type 必须在 _ARTIFACT_TYPE_WHITELIST 内。
    与 JobLogSignal 解耦：log_signal.artifact_uri 保留为权威指针；
        本端点只负责展示/下载入口的后端持久化。
    """
    storage_uri:           str                       # NFS 路径（已由 Agent LogPuller 落盘）
    artifact_type:         str                       # 白名单
    fencing_token:         str
    agent_instance_id:     str
    host_id:               str
    device_serial:         str
    size_bytes:            Optional[int] = None
    checksum:              Optional[str] = None      # sha256 hex，可选
    source_category:       Optional[str] = None      # AEE | VENDOR_AEE | BUGREPORT（溯源）
    source_path_on_device: Optional[str] = None      # 设备侧原路径（溯源）


class ArtifactOut(BaseModel):
    artifact_id: int
    created:     bool   # True=首次插入；False=幂等命中（已存在同 storage_uri）


async def ingest_agent_artifact(
    db: AsyncSession,
    job_id: int,
    payload: ArtifactIn,
) -> ArtifactOut:
    """独立端点：接收 Agent watcher LogPuller 产出的 artifact。

    不复用 /complete：避免把 watcher 异步产物与 Job 终态绑死（Job 在 artifact 上送
    之前/之后终态都合法）。
    幂等：PostgreSQL `ON CONFLICT (job_id, storage_uri) DO NOTHING` —— 重复 POST
    不重复入库，返回已存在的 artifact_id + created=False。
    """
    if not payload.storage_uri:
        raise BadRequest("storage_uri is required")
    try:
        resolve_local_artifact_path(payload.storage_uri, must_exist=False)
    except ArtifactPathError as exc:
        raise BadRequest({
            "code": "INVALID_ARTIFACT_PATH",
            "message": (
                "artifact path is invalid or outside the allowed root "
                f"(STP_AEE_NFS_ROOT): {exc}"
            ),
        }) from exc

    if payload.artifact_type not in _ARTIFACT_TYPE_WHITELIST:
        raise BadRequest(
            f"artifact_type must be one of {sorted(_ARTIFACT_TYPE_WHITELIST)}; "
            f"got {payload.artifact_type!r}",
        )

    if payload.size_bytes is not None and payload.size_bytes < 0:
        raise BadRequest("size_bytes must be >= 0")

    job = await db.get(JobInstance, job_id)
    if job is None:
        raise NotFound("job not found")
    await require_job_bound_upload_lease(
        db,
        job,
        fencing_token=payload.fencing_token,
        agent_instance_id=payload.agent_instance_id,
        host_id=payload.host_id,
        device_serial=payload.device_serial,
    )

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = (
        pg_insert(JobArtifact)
        .values(
            job_id=job_id,
            storage_uri=payload.storage_uri,
            artifact_type=payload.artifact_type,
            size_bytes=payload.size_bytes,
            checksum=payload.checksum,
            source_category=payload.source_category,
            source_path_on_device=payload.source_path_on_device,
        )
        .on_conflict_do_nothing(index_elements=["job_id", "storage_uri"])
        .returning(JobArtifact.id)
    )
    res = await db.execute(stmt)
    row = res.first()

    if row is not None:
        # 首次插入
        await db.commit()
        return ArtifactOut(artifact_id=row.id, created=True)

    # 幂等命中 —— 查询已存在的 artifact_id
    existing = await db.execute(
        select(JobArtifact.id)
        .where(
            JobArtifact.job_id == job_id,
            JobArtifact.storage_uri == payload.storage_uri,
        )
    )
    existing_id = existing.scalar_one_or_none()
    if existing_id is None:
        # 极端并发：ON CONFLICT 未返回 id 且 SELECT 也查不到 → 让客户端重试
        logger.warning(
            "artifact_ingest_race job_id=%d storage_uri=%s",
            job_id, payload.storage_uri,
        )
        raise Conflict("artifact ingest race, please retry")
    await db.commit()
    return ArtifactOut(artifact_id=existing_id, created=False)




# 路由 / 既有测试用的端点别名。
ingest_artifact = ingest_agent_artifact
