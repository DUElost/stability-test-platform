"""去重（scan / merge / extract）状态响应 schema。

与 `routes/dedup.py` 的 scan 端点同域。`DedupStatusOut` 是
``GET /plan-runs/{id}/dedup/status`` 的**单一声明**——前端 `DedupStatusPayload` 与它双向
对拍（`tests/test_api_response_shape_contract.py` 轴线 C）。此前该端点返回手搓 dict
（``response_model=ApiResponse[dict]``），形状没有机器可读的权威声明，只能靠
"后端 `ok({...})` / 前端匿名内联类型"两处各写一遍。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class DedupArtifactOut(BaseModel):
    """去重产物行（scan/merge 落 ``PlanRunArtifact``）。"""

    id: int
    host_id: Optional[str] = None
    storage_uri: str
    artifact_type: str
    size_bytes: Optional[int] = None
    created_at: Optional[str] = None


class DedupScanArchiveOut(BaseModel):
    """本轮 scan 完备性快照（``PlanRun.run_context['archive']``）。

    写入方：``services/dedup_scan.record_scan_archive_state``（#118）。它回答「几台 Agent
    交了东西」，与该服务 ``ScanCompleteness`` 的「(host, 平台) 对是否交齐」是两个口径。

    ``extra="allow"`` **不是**可有可无：该段是自由 JSONB，Pydantic 默认会**丢弃**未声明键，
    那会把"新增字段"变成静默丢失（比不建模更糟）。这里只固定已知键，未知键原样透传。
    """

    model_config = ConfigDict(extra="allow")

    hosts_triggered: int = 0
    scan_artifacts_registered: int = 0
    hosts_with_artifacts: int = 0
    hosts_not_acked: int = 0


class DedupStatusOut(BaseModel):
    """``GET /plan-runs/{id}/dedup/status`` 响应（scan/merge 产物 + 完备性 + 失败位）。

    ``scan_failed`` 来源是 ``PlanRun.result_summary``，``archive`` 来源是 ``run_context``，
    两者与 ``artifacts`` 表各自独立——所以这里三个字段都可能单独缺失。
    """

    plan_run_id: int
    artifacts: list[DedupArtifactOut]
    archive: Optional[DedupScanArchiveOut] = None
    scan_failed: bool = False


__all__ = [
    "DedupArtifactOut",
    "DedupScanArchiveOut",
    "DedupStatusOut",
]
