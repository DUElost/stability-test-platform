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


class DedupSkippedHostOut(BaseModel):
    """本轮 scan 未纳入的主机（``classify_recycle_targets`` 的 skipped 条目）。

    退役未获准计入 ``skipped_retired``，其余非 ONLINE 计入 ``skipped_offline``——
    两侧都不虚报完整。
    """

    host_id: str
    status: str


class DedupScanTriggerOut(BaseModel):
    """``POST /plan-runs/{id}/dedup/scan`` 响应（触发结果 + 如实报告的跳过位）。"""

    plan_run_id: int
    enqueued: str
    is_final: bool
    triggered_hosts: list[str]
    skipped_offline: list[DedupSkippedHostOut]
    skipped_retired: list[DedupSkippedHostOut]


class DedupMergeTriggerOut(BaseModel):
    """``POST /plan-runs/{id}/dedup/merge`` 响应。

    ``scan_round_id`` / ``round_started_at`` 来自 ``resolve_manual_merge_round``
    （不给无约束的历史）；两者可同时为 None。
    """

    status: str
    plan_run_id: int
    scan_round_id: Optional[str] = None
    round_started_at: Optional[str] = None


class DedupExtractOut(BaseModel):
    """``POST /plan-runs/{id}/dedup/extract`` 响应（归档-3 提单目录）。"""

    plan_run_id: int
    jira_dir: str
    extracted_count: int


class DedupAgentConfigReloadOut(BaseModel):
    """``POST /plan-runs/hosts/{host_id}/reload-config`` 响应（下发即返回，不等 ack）。"""

    host_id: str
    command: str
    status: str


class JiraRunStartOut(BaseModel):
    """``POST /jira/runs`` 响应（控制台 run 句柄 + 本次实际输入）。

    ``jira_project_key`` 仅在 ``source=plan_run`` 且该 PlanRun 能解析出项目键时有值。
    """

    console_run_id: str
    room: str
    vendor: str
    stage: str
    source: str
    jira_project_key: Optional[str] = None


class JiraRunCancelOut(BaseModel):
    """``POST /jira/runs/{console_run_id}/cancel`` 响应。

    ``canceled`` 表示是否**发起**了取消：跨实例转发失败时 fail-closed 为 False，
    不假装成功。
    """

    console_run_id: str
    canceled: bool


__all__ = [
    "DedupAgentConfigReloadOut",
    "DedupArtifactOut",
    "DedupExtractOut",
    "DedupMergeTriggerOut",
    "DedupScanArchiveOut",
    "DedupScanTriggerOut",
    "DedupSkippedHostOut",
    "DedupStatusOut",
    "JiraRunCancelOut",
    "JiraRunStartOut",
]
