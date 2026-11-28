"""B11-U1/U3（#4199 v1.1 / #4174）Jira handoff A/B 段 API 的 Pydantic v2 Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class HandoffPreflightIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_run_ids: list[int] = Field(..., min_length=1, max_length=20)
    platform: str = Field(default="mtk", description="一期仅支持字面 'mtk'；非字面 'mtk' 返回 400")
    snapshot_cutoff: Optional[datetime] = Field(
        default=None,
        description="冻结快照截止时间（同时冻结 DLE 观测与分片选择；默认当前 UTC）",
    )


class HandoffBundleCreateIn(HandoffPreflightIn):
    builds: Optional[list[str]] = Field(
        default=None,
        description="可选：仅针对指定 build 列表生成，空则覆盖全部 build",
    )
    target_folder_hint: Optional[str] = Field(
        default=None,
        max_length=128,
        description="可选：仅作预览 Path 占位提示，阶段 1 不写 15.4 共享根",
    )


class HandoffRunCoverageOut(BaseModel):
    plan_run_id: int
    plan_name: str
    status: str
    partition_mode: str
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    project_key: Optional[str] = None
    jira_project_key: Optional[str] = None
    hosts_expected: list[str]
    hosts_with_reports: list[str]
    missing_hosts: list[str]
    unexpected_hosts: list[str] = Field(default_factory=list)
    mapping_status: str


class HandoffIdentityGateOut(BaseModel):
    total_org_rows: int
    unique_events: int
    keyed_rows: int
    compat_fallback_rows: int
    unbound_rows: int
    after_cutoff_skipped_rows: int
    unbound_by_reason: dict[str, int]


class HandoffUnboundRowOut(BaseModel):
    plan_run_id: int
    host_id: str
    report_file: str
    row_no: int
    serial: str
    raw_path: str
    reason: str


class HandoffBuildPreviewOut(BaseModel):
    build: str
    product: str = ""
    device_count: int
    unique_event_count: int


class HandoffGatesOut(BaseModel):
    coverage_complete: bool
    identity_degraded: bool
    blocking_issues: list[str]
    warnings: list[str]


class HandoffPreflightOut(BaseModel):
    platform: str
    snapshot_cutoff: str
    first_run_started_at: Optional[str] = None
    runs: list[HandoffRunCoverageOut]
    identity_gate: HandoffIdentityGateOut
    unbound_rows: list[HandoffUnboundRowOut] = Field(default_factory=list)
    builds: list[HandoffBuildPreviewOut]
    gates: HandoffGatesOut


class HandoffArtifactMetaOut(BaseModel):
    rel_path: str
    filename: str
    sha256: str
    size_bytes: int


class HandoffBaselineDistOut(BaseModel):
    current_only: int
    mixed: int
    baseline_only: int


class HandoffRepresentativeOut(BaseModel):
    dedup_key: str
    identity_source: str
    occurrence_key: Optional[str] = None
    dle_id: Optional[str] = None
    serial: str
    entry: str
    evidence_location: str
    evidence_tier: str


class HandoffIssueOut(BaseModel):
    issue_index: int
    build: str
    count: int
    device_count: int
    device_ids: list[str]
    source_runs: list[int]
    baseline_class: str
    clock_fallback_count: int
    preview_path: str
    representative: HandoffRepresentativeOut
    member_dedup_keys: list[str]
    form_fields: dict[str, str]


class HandoffBuildBundleOut(BaseModel):
    build: str
    build_slug: str
    product: str = ""
    device_count: int
    unique_events: int
    issue_count: int
    grouped_events: int
    filtered_events: int
    filtered_by_reason: dict[str, int]
    baseline_distribution: HandoffBaselineDistOut
    artifacts: dict[str, HandoffArtifactMetaOut]
    issues: list[HandoffIssueOut]
    filtered_rows: list[dict[str, Any]]
    events: list[dict[str, Any]]


class HandoffBundleSummaryOut(BaseModel):
    handoff_id: str
    status: str
    platform: str
    created_at: str
    created_by: str
    plan_run_ids: list[int]
    build_count: int
    issue_count_total: int
    coverage_complete: bool
    manifest_sha256: str


class HandoffBundleOut(BaseModel):
    schema_name: str = Field(alias="schema")
    schema_version: str
    handoff_id: str
    status: str
    platform: str
    created_at: str
    created_by: str
    plan_run_ids: list[int]
    snapshot_cutoff: str
    first_run_started_at: Optional[str] = None
    target_folder_hint: Optional[str] = None
    scan_tool_release: Optional[dict[str, str]] = None
    gates: HandoffGatesOut
    identity_gate: HandoffIdentityGateOut
    runs: list[dict[str, Any]]
    unbound_rows: list[dict[str, Any]]
    builds: list[HandoffBuildBundleOut]
    manifest_sha256: str

    model_config = ConfigDict(populate_by_name=True)


class HandoffExportBuildTargetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    build: str = Field(..., min_length=1, max_length=256)
    target_folder_name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="人工显式 15.4 目标目录名；禁止路径穿越与内网 IP",
    )
    merge_strategy: Literal["create_new", "merge_existing"] = Field(
        ...,
        description="字面 create_new | merge_existing；不得自动推断",
    )


class HandoffExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_sha256: str = Field(..., min_length=64, max_length=64)
    builds: list[HandoffExportBuildTargetIn] = Field(..., min_length=1)


class HandoffExportBuildResultOut(BaseModel):
    build: str
    target_folder_name: str
    merge_strategy: str
    representatives: list[dict[str, Any]]
    final_xls: dict[str, Any]
    verified_path_count: int


class HandoffExportOut(BaseModel):
    schema_name: str = Field(alias="schema")
    schema_version: str
    handoff_id: str
    status: str
    exported_at: str
    exported_by: str
    manifest_sha256: str
    export_root: str
    vendor_path_prefix: str
    builds: list[HandoffExportBuildResultOut]

    model_config = ConfigDict(populate_by_name=True)
