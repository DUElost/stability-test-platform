"""B8 G7：交付物定位（D9 第 2、8 条；D16 下载行）。

本模块只做**定位与校验**，不构造 HTTP 响应：C2 合约禁止 services 直接依赖
fastapi，下载响应由路由层组装（与 artifact_zip / *_download 的历史例外不同，
那是已登记的违规基线，只许删不许加）。

两类产物：

- 统计导出（每个交付版本一份，D9 第 8 条）：_meta/{run}/_delivery/{platform}/v{n}.json；
- 代表证据（G6 已校验的发布记录）：evidence/<主文件夹>/<SN>/<事件目录>/。

两者都只按登记定位：交付范围以 delivery_version 与 evidence_publication 为准，
不得从主文件夹或 DLE.remote_path 反推。越根、跨 run、未校验记录一律拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.artifact_paths import (
    ArtifactPathError,
    resolve_evidence_event_path,
    resolve_local_artifact_path,
)
from backend.core.storage_root import resolve_shared_storage_root
from backend.models.issue_fact import DeliveryVersion, EvidencePublication
from backend.services import issue_fact_store as facts
from backend.services.shadow_delivery import delivery_export_path


@dataclass(frozen=True)
class DeliveryDownloadError(Exception):
    """定位失败；路由按 status_code 翻译成 HTTP 响应。"""

    status_code: int
    detail: str

    def __str__(self) -> str:
        return self.detail


def resolve_delivery_export(
    db: Session,
    *,
    plan_run_id: int,
    platform: str,
    version_no: int,
) -> Path:
    """已登记交付版本的统计导出文件；未登记 / 文件缺失一律拒绝。"""
    version = db.execute(
        select(DeliveryVersion).where(
            DeliveryVersion.plan_run_id == plan_run_id,
            func.lower(DeliveryVersion.platform) == platform.lower(),
            DeliveryVersion.version_no == version_no,
        )
    ).scalar_one_or_none()
    if version is None:
        raise DeliveryDownloadError(
            404, "delivery version not found for this plan run",
        )
    root = resolve_shared_storage_root()
    if not root:
        raise DeliveryDownloadError(503, "NFS root not configured")
    path = delivery_export_path(root, plan_run_id, platform.lower(), version_no)
    try:
        return resolve_local_artifact_path(str(path), must_exist=True)
    except ArtifactPathError as exc:
        raise DeliveryDownloadError(404, str(exc)) from exc


def resolve_evidence_directory(
    db: Session,
    *,
    plan_run_id: int,
    publication_id: int,
) -> Path:
    """一条已校验代表证据的事件目录。

    记录必须属于该 PlanRun 且处于 VERIFIED；目的相对路径的链根前缀必须与
    记录的 root_plan_run_id 一致（跨根/跨链拒绝由契约层给出）。
    """
    publication = db.get(EvidencePublication, publication_id)
    if publication is None or publication.plan_run_id != plan_run_id:
        raise DeliveryDownloadError(404, "evidence publication not found")
    if publication.state != facts.PUBLICATION_STATE_VERIFIED:
        raise DeliveryDownloadError(
            409,
            f"evidence publication state {publication.state} is not downloadable",
        )
    if not publication.destination_rel_path:
        raise DeliveryDownloadError(404, "evidence path is not recorded")
    try:
        resolved = resolve_evidence_event_path(
            publication.destination_rel_path,
            root_plan_run_id=publication.root_plan_run_id,
            must_exist=True,
        )
    except ArtifactPathError as exc:
        raise DeliveryDownloadError(400, str(exc)) from exc
    if not resolved.is_dir():
        raise DeliveryDownloadError(404, "evidence path is not a directory")
    return resolved


__all__ = [
    "DeliveryDownloadError",
    "resolve_delivery_export",
    "resolve_evidence_directory",
]
