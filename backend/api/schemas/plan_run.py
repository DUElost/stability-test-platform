"""PlanRun / Job / Watcher 相关的响应与请求 schema。

从 backend/api/routes/plan_runs.py 抽出 —— 该文件曾有 3151 行,其中 25 个
BaseModel 内联其中,而 backend/api/schemas/ 这个专门的包基本闲置。

注:类名与字段一律未改动,OpenAPI components 名称因此保持不变。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class StepTraceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    step_id: str
    stage: str
    event_type: str
    status: str
    output: Optional[str] = None
    error_message: Optional[str] = None
    original_ts: str
    created_at: str


class JobInstanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plan_run_id: Optional[int] = None
    plan_id: Optional[int] = None
    device_id: int
    device_serial: Optional[str] = None
    host_id: Optional[str] = None
    status: str
    status_reason: Optional[str] = None
    # ADR-0026 §3: sub-state for recycler clocks / permit+barrier observability.
    execution_state: Optional[str] = None
    last_execution_heartbeat_at: Optional[str] = None
    last_progress_at: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    created_at: Optional[str] = None
    step_traces: list[StepTraceOut] = []


class PlanRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plan_id: int
    status: str
    failure_threshold: float
    run_type: str
    triggered_by: Optional[str] = None
    started_at: str
    ended_at: Optional[str] = None
    result_summary: Optional[dict] = None
    # ADR-0021: dispatch gate progress lives under run_context.precheck.
    run_context: Optional[dict] = None
    plan_snapshot: Optional[dict] = None
    parent_plan_run_id: Optional[int] = None
    root_plan_run_id: Optional[int] = None
    chain_index: int = 0
    next_plan_triggered: bool = False
    plan_name: Optional[str] = None
    capabilities: Optional[dict] = None
    jobs: list[JobInstanceOut] = []
    # ── ADR-0026: admission-queue observability (NULL for legacy runs) ──
    queue_reason: Optional[str] = None
    enqueued_at: Optional[str] = None
    next_admission_at: Optional[str] = None
    priority: int = 0


class PlanRunAbortIn(BaseModel):
    reason: Optional[str] = None


class JobManualActionIn(BaseModel):
    reason: Optional[str] = None


class JobManualActionOut(BaseModel):
    job_id: int
    plan_run_id: int
    action: str          # 'manual_retry' | 'manual_exit'
    status: str          # job status after the action
    manual_action: Optional[str] = None
    next_retry_at: Optional[str] = None
    current_failure_streak: int = 0


class ChainNodeOut(BaseModel):
    plan_id: int
    plan_name: Optional[str] = None
    plan_run_id: Optional[int] = None
    status: str                        # PlanRun.status 或 'pending'(尚未触发)
    chain_index: int
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    failure_threshold: float
    pass_rate: Optional[float] = None  # 来自 PlanRun.result_summary
    is_current: bool = False
    is_blocked: bool = False
    block_reason: Optional[str] = None


class PlanChainOut(BaseModel):
    plan_run_id: int
    root_plan_run_id: int
    nodes: list[ChainNodeOut]


class StageStepOut(BaseModel):
    step_key: str
    script_name: str
    stage: str
    sort_order: int
    device_total: int                  # = PlanRun jobs 总数
    device_succeeded: int              # event_type=COMPLETED + status=COMPLETED
    device_failed: int                 # event_type=FAILED or status=FAILED
    device_skipped: int = 0            # v3: event_type=COMPLETED + status=SKIPPED
    device_running: int                # = max(0, total - succeeded - failed - skipped)


class StageOut(BaseModel):
    stage: str                         # init / patrol / teardown
    status: str                        # pending / running / completed / failed / skipped
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    device_total: int
    device_succeeded: int = 0
    device_failed: int = 0
    device_skipped: int = 0            # v3: summed from steps
    # patrol 专属(从 JobInstance.patrol_*_cycle_count 聚合)
    patrol_cycle_index: Optional[int] = None
    patrol_active_devices: Optional[int] = None
    patrol_interval_seconds: Optional[int] = None
    steps: list[StageStepOut] = []


class PlanRunTimelineOut(BaseModel):
    plan_run_id: int
    current_stage: str                 # init / patrol / teardown / done / pending
    stages: list[StageOut]
    aborted_job_count: int = 0         # v3: ABORTED jobs 计数 (顶层 banner 用)
    triggered_at: str
    triggered_by: Optional[str] = None
    run_type: str
    plan_name: Optional[str] = None


class EventOut(BaseModel):
    ts: str
    stage: str                         # init/patrol/teardown/system/trigger
    severity: str                      # ok/info/warn/err
    category: str                      # step / log_signal / audit / system / trigger
    title: str
    description: str = ""
    job_id: Optional[int] = None
    device_id: Optional[int] = None
    device_serial: Optional[str] = None
    ref: Optional[dict] = None         # {type, id} — 用于跳转 step_trace / log_signal


class PlanRunEventsOut(BaseModel):
    plan_run_id: int
    events: list[EventOut]
    total: int                         # 当前过滤条件下的总数(facets 后)
    facets: dict                       # {by_stage: {...}, by_severity: {...}}


class DeviceMatrixItem(BaseModel):
    device_id: int
    device_serial: Optional[str] = None
    device_model: Optional[str] = None
    host_id: Optional[str] = None
    job_id: int
    job_status: str
    ui_status: str                     # completed/running/failed/aborted/risk/backoff/pending/unknown
    current_stage: str
    current_step: Optional[str] = None
    patrol_cycle_count: int = 0
    patrol_success_cycle_count: int = 0
    patrol_failed_cycle_count: int = 0
    current_failure_streak: int = 0
    next_retry_at: Optional[str] = None
    manual_action: Optional[str] = None
    log_signal_count: int = 0
    last_heartbeat_at: Optional[str] = None
    started_at: Optional[str] = None
    created_at: Optional[str] = None
    ended_at: Optional[str] = None
    status_reason: Optional[str] = None   # ADR-0021: pending_timeout / agent never claimed / etc.
    grace_remaining_seconds: Optional[int] = None
    pending_claim_remaining_seconds: Optional[int] = None
    pending_claim_deadline_at: Optional[str] = None
    heartbeat_deadline_at: Optional[str] = None
    is_stuck: bool = False
    busy_reason: Optional[str] = None
    busy_lease_job_id: Optional[int] = None
    device_link_status: str = "unknown"   # online | offline | adb_error | host_offline | unknown
    job_exec_status: str = "unknown"      # running | backoff | pending | completed | failed | aborted | unknown
    adb_state: Optional[str] = None
    adb_connected: Optional[bool] = None
    capabilities: Optional[dict] = None


class PlanRunDevicesOut(BaseModel):
    plan_run_id: int
    total: int
    # Job 执行投影(与设备连接正交):{all/completed/running/failed/unknown/backoff/pending/aborted: int}
    by_status: dict
    # 设备 ADB / Host 可达性:{all/online/offline/adb_error/host_offline/unknown: int}
    by_link_status: dict = {}
    by_host: dict                      # {host_id: int}
    devices: list[DeviceMatrixItem]


class WatcherCategoryOut(BaseModel):
    category: str
    count: int
    affected_device_count: int
    trend_change: int                  # 当前窗口 - 上一窗口同长度
    latest_device_serial: Optional[str] = None
    latest_detected_at: Optional[str] = None


class PackageStatOut(BaseModel):
    package_name: str                 # 空/缺失统一归 "unknown"
    crash_count: int                  # category=AEE 且 event_type=CRASH(按 nfs_path 去重)
    vendor_crash_count: int           # category=VENDOR_AEE 同条件
    anr_count: int                    # category=ANR OR extra.event_type='ANR'
    latest_detected_at: Optional[str] = None


class AeeBreakdownOut(BaseModel):
    crash_count: int                  # COUNT(DISTINCT extra->>'nfs_path') under AEE+CRASH
    vendor_crash_count: int           # 同上,VENDOR_AEE+CRASH(与 crash_count 互斥)
    anr_count: int                    # COUNT(DISTINCT extra->>'nfs_path') under ANR
    packages: list[str]               # distinct package_name(已合并 unknown 桶)
    by_package: list[PackageStatOut]  # 按 crash + vendor_crash + anr 总数降序


class PackageSubtypeCountOut(BaseModel):
    subtype: str
    count: int


class SubtypeDistributionOut(BaseModel):
    subtype: str
    group: str
    count: int
    share: float


class PackageRankingOut(BaseModel):
    package_name: str
    total_count: int
    affected_device_count: int
    latest_detected_at: Optional[str] = None
    subtype_breakdown: list[PackageSubtypeCountOut] = []


class AeeDashboardSectionOut(BaseModel):
    total_events: int = 0
    affected_device_count: int = 0
    top_package_name: Optional[str] = None
    top_subtype: Optional[str] = None
    subtype_distribution: list[SubtypeDistributionOut] = []
    package_ranking: list[PackageRankingOut] = []


class WatcherAgentOpsMetrics(BaseModel):
    pruned_total: int = 0
    local_disk_usage_pct: Optional[float] = None
    spill_cycles: int = 0
    spilled_total: int = 0


class WatcherArchiveOut(BaseModel):
    ops_metrics: WatcherAgentOpsMetrics = Field(default_factory=WatcherAgentOpsMetrics)
    scan_status: Optional[str] = None
    scan_triggered_at: Optional[str] = None
    archived_jobs: int = 0
    pending_jobs: int = 0
    failed_jobs: int = 0


class WatcherSummaryOut(BaseModel):
    plan_run_id: int
    window_minutes: Optional[int] = None
    time_scope: str = "all"
    window_start_at: str
    window_end_at: str
    categories: list[WatcherCategoryOut]
    total: int
    affected_device_count: int
    total_devices: int
    abnormal_rate: float               # affected_device_count / total_devices
    threshold: float
    exceeded: bool
    supports_origin_split: bool = False
    current_run: AeeDashboardSectionOut = AeeDashboardSectionOut()
    preexisting: AeeDashboardSectionOut = AeeDashboardSectionOut()
    # M0/PR #2: AEE 细分(reconciler signal 才会填充);无关联 Job 时 None
    aee_breakdown: Optional[AeeBreakdownOut] = None
    # M0/C-6 (§2.4 #5): 该 PlanRun 下 Job 的 watcher 能力快照(取最"降级"的一档)。
    #   来源:JobInstance.watcher_capability 列(由 Agent 在 complete/heartbeat 回填的
    #   JobSession.summary.watcher_capability)。无可靠来源时为 None;
    #   前端在 'unavailable' 时显示「Watcher 不可用」徽章(watcher 未正常启动,
    #   AEE reconciler 可能未运行,勿当作有 reconciler 兜底)。
    watcher_capability: Optional[str] = None
    # ADR-0025 Sprint 3: 运行日志归档状态（控制面按需拉取聚合）；无关联 Job 时 None
    archive: Optional[WatcherArchiveOut] = None


__all__ = [
    "StepTraceOut",
    "JobInstanceOut",
    "PlanRunOut",
    "PlanRunAbortIn",
    "JobManualActionIn",
    "JobManualActionOut",
    "ChainNodeOut",
    "PlanChainOut",
    "StageStepOut",
    "StageOut",
    "PlanRunTimelineOut",
    "EventOut",
    "PlanRunEventsOut",
    "DeviceMatrixItem",
    "PlanRunDevicesOut",
    "WatcherCategoryOut",
    "PackageStatOut",
    "AeeBreakdownOut",
    "PackageSubtypeCountOut",
    "SubtypeDistributionOut",
    "PackageRankingOut",
    "AeeDashboardSectionOut",
    "WatcherAgentOpsMetrics",
    "WatcherArchiveOut",
    "WatcherSummaryOut",
]
