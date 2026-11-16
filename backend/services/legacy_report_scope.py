"""既有「按 PlanRun 产物取报表」入口的分区边界（ADR-0062 D10、D16）。

D10 第 1 条要求：运行已有分区模式记录时，既有报表入口只能选择可确认属于
「现行」分区的产物；第 2 条要求没有模式记录的存量运行沿用既有规则。

本模块只做归属校验，不改变产物、不启动工具、不写登记。调用方必须在消费
（启动厂商工具、复制文件等）之前调用，校验失败直接拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.core.dedup_platform import DEDUP_PLATFORMS
from backend.models.plan_run import PlanRun
from backend.models.plan_run_artifact import PlanRunArtifact
from backend.services import issue_fact_store as facts

_REPORT_ARTIFACT_TYPES = frozenset({"scan_result_xls", "merge_result_xls"})


@dataclass
class LegacyReportScopeError(Exception):
    """归属校验失败；由路由层映射为 HTTP 错误。"""

    status_code: int
    detail: str

    def __str__(self) -> str:
        return self.detail


def _confirmed_platform(storage_uri: str) -> str | None:
    """从产物 URI 的平台路径段确认归属；无法确认时返回 None。

    ``scan_artifact_uri_platform`` 对未知路径回落 mtk（用于期望集推导），
    这里不能沿用该兜底：混合运行中「无法确认」必须拒绝，而不是猜成 mtk。
    """
    norm = (storage_uri or "").replace("\\", "/").lower()
    for platform in DEDUP_PLATFORMS:
        if f"/{platform}/" in norm:
            return platform
    return None


def resolve_legacy_report_artifact(
    db: Session,
    *,
    artifact_id: int,
    plan_run_id: int | None = None,
) -> PlanRunArtifact:
    """校验并返回可供既有报表入口消费的产物。"""
    artifact = db.get(PlanRunArtifact, artifact_id)
    if artifact is None:
        raise LegacyReportScopeError(404, f"plan_run_artifact {artifact_id} not found")

    if plan_run_id is not None and int(artifact.plan_run_id) != int(plan_run_id):
        raise LegacyReportScopeError(
            409,
            "artifact belongs to a different plan run; "
            f"artifact_run={artifact.plan_run_id} requested_run={plan_run_id}",
        )

    run = db.get(PlanRun, artifact.plan_run_id)
    if run is None:
        raise LegacyReportScopeError(404, f"plan run {artifact.plan_run_id} not found")

    if artifact.artifact_type not in _REPORT_ARTIFACT_TYPES:
        raise LegacyReportScopeError(
            409,
            f"artifact type {artifact.artifact_type!r} is not a report",
        )

    modes = facts.list_partition_modes(db, plan_run_id=run.id)
    if not modes:
        # 无模式记录的存量运行：沿用既有产物读取规则（D10 第 2 条）。
        return artifact

    platform = _confirmed_platform(artifact.storage_uri)
    if platform is None:
        raise LegacyReportScopeError(
            409,
            "artifact partition cannot be confirmed from its storage path",
        )

    recorded = {
        row.platform.lower(): row.mode for row in modes
    }
    if recorded.get(platform, facts.PARTITION_MODE_CURRENT) != facts.PARTITION_MODE_CURRENT:
        raise LegacyReportScopeError(
            409,
            f"artifact belongs to the {platform} {recorded[platform]} partition; "
            "the legacy report entry only accepts CURRENT partitions",
        )
    return artifact


__all__ = ["LegacyReportScopeError", "resolve_legacy_report_artifact"]
