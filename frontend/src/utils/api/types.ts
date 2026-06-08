// ─── 基础实体类型 ──────────────────────────────────────────────────────────────

// ADR-0021 hot-update gate: per-host snapshot of an active Job.
export interface HostActiveJob {
  id: number;
  plan_run_id?: number | null;
  plan_id?: number | null;
  device_id: number;
  status: string;
  started_at?: string | null;
  abort_pending?: boolean;  // v3: PlanRun.run_context 含 abort_requested
}

export interface Host {
  id: string | number;
  name: string;
  ip: string;
  ssh_port: number;
  ssh_user: string | null;
  status: 'ONLINE' | 'OFFLINE' | 'DEGRADED';
  watcher_admin_active?: boolean;
  last_heartbeat: string | null;
  extra: Record<string, any>;
  mount_status: Record<string, any>;
  capacity?: {
    active_jobs: number;
    active_devices: number;
    online_healthy_devices: number;
  };
  health?: {
    status: 'HEALTHY' | 'DEGRADED' | 'UNSCHEDULABLE';
    reasons: string[];
    cpu_load: number;
    ram_usage: number;
    disk_usage: number;
    mount_ok: boolean;
    adb_ok: boolean;
  };
  // ADR-0021 hot-update gate — populated only on GET /hosts/{id}.
  active_job_count?: number;
  active_jobs?: HostActiveJob[];
}

export interface Device {
  id: number;
  serial: string;
  model: string | null;
  host_id: string | number | null;
  status: 'ONLINE' | 'OFFLINE' | 'BUSY';
  last_seen: string | null;
  tags: string[];
  extra?: Record<string, any>;
  adb_state?: string | null;
  adb_connected?: boolean | null;
  battery_level?: number | null;
  battery_temp?: number | null;
  temperature?: number | null;
  wifi_rssi?: number | null;
  wifi_ssid?: string | null;
  network_latency?: number | null;
  build_display_id?: string | null;
  cpu_usage?: number | null;
  mem_total?: number | null;
  mem_used?: number | null;
  disk_total?: number | null;
  disk_used?: number | null;
}

export interface Task {
  id: number;
  name: string;
  type: string;
  template_id: number | null;
  params: Record<string, any>;
  pipeline_def?: Record<string, any> | null;
  target_device_id: number | null;
  status: 'PENDING' | 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELED';
  priority: number;
  created_at: string;
  group_id?: string;
  is_distributed?: boolean;
  runs_count?: number;
}

export interface RunStep {
  id: number;
  run_id: number;
  phase: string;
  step_order: number;
  name: string;
  action: string;
  params: Record<string, any>;
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'SKIPPED' | 'CANCELED';
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  error_message: string | null;
  log_line_count: number;
  created_at: string;
}

export interface TaskRun {
  id: number;
  task_id: number;
  host_id: number;
  device_id: number;
  status: string;
  group_id?: string;
  progress?: number;
  progress_message?: string;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  error_code: string | null;
  error_message: string | null;
  log_summary: string | null;
  artifacts: LogArtifact[];
  risk_summary?: RunRiskSummary | null;
}

export interface RuntimeLogEntry {
  stream_id?: string;
  job_id?: number | null;
  step_id?: string;
  level: string;
  timestamp: string;
  message: string;
}

export interface RuntimeLogQueryResponse {
  items: RuntimeLogEntry[];
  next_cursor: string | null;
  has_more: boolean;
  scanned: number;
}

export interface LogArtifact {
  id: number;
  run_id: number;
  storage_uri: string;
  size_bytes: number | null;
  checksum: string | null;
  created_at: string;
}

export interface RunRiskSummary {
  generated_at?: string;
  risk_level?: 'LOW' | 'MEDIUM' | 'HIGH' | string;
  monitor_summary?: string;
  counts?: {
    events_total?: number;
    aee_entries?: number;
    restart_count?: number;
    by_type?: Record<string, number>;
  };
}

export interface RunRiskAlert {
  code: string;
  severity: 'HIGH' | 'MEDIUM' | 'LOW';
  message: string;
  metric?: string | null;
  value?: number | null;
  threshold?: number | null;
}

export interface RunReport {
  generated_at: string;
  run: TaskRun;
  task: Task;
  host: {
    id: number;
    name: string;
    ip: string;
    status: string;
  } | null;
  device: {
    id: number;
    serial: string;
    model: string | null;
    host_id: number | null;
    status: string;
  } | null;
  summary_metrics: Record<string, any>;
  risk_summary: RunRiskSummary | null;
  alerts: RunRiskAlert[];
}

export interface JiraDraft {
  run_id: number;
  task_id: number;
  project_key: string;
  issue_type: string;
  priority: 'Critical' | 'Major' | 'Minor';
  component?: string | null;
  fix_version?: string | null;
  assignee?: string | null;
  summary: string;
  description: string;
  labels: string[];
  environment: Record<string, any>;
  custom_fields: Record<string, any>;
  extra: Record<string, any>;
}

export interface PipelineTemplate {
  name: string;
  description: string;
  pipeline_def: Record<string, any>;
}

export interface AgentLogOut {
  host_id: number;
  log_path: string;
  content: string;
  lines_read: number;
  error?: string;
}

export interface User {
  id: number;
  username: string;
  role: string;
  is_active: string;
  created_at: string;
  last_login: string | null;
}

// ─── 统计/分析类型 ────────────────────────────────────────────────────────────

export interface RunsByStatus {
  finished: number;
  failed: number;
  canceled: number;
  running: number;
  total: number;
}

export interface TestTypeStat {
  type: string;
  finished: number;
  failed: number;
  total: number;
}

export interface RiskDistribution {
  high: number;
  medium: number;
  low: number;
  unknown: number;
}

export interface RecentRun {
  run_id: number;
  task_name: string;
  task_type: string;
  status: string;
  risk_level: string;
  duration_seconds: number | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface ResultsSummary {
  runs_by_status: RunsByStatus;
  test_type_stats: TestTypeStat[];
  risk_distribution: RiskDistribution;
  recent_runs: RecentRun[];
}

export interface ActivityPoint {
  hour: string;
  started: number;
  completed: number;
  failed: number;
}

export interface ActivityResponse {
  points: ActivityPoint[];
  hours: number;
}

export interface DeviceMetricPoint {
  timestamp: string;
  battery_level: number | null;
  temperature: number | null;
  network_latency: number | null;
  cpu_usage: number | null;
  mem_used: number | null;
}

export interface DeviceMetricsResponse {
  device_id: number;
  points: DeviceMetricPoint[];
  hours: number;
}

export interface CompletionTrendPoint {
  date: string;
  passed: number;
  failed: number;
}

export interface CompletionTrendResponse {
  points: CompletionTrendPoint[];
  days: number;
}

// ─── Dashboard Summary (权威聚合接口,替代分页列表) ──────────────────────────────

export interface DashboardHostSummary {
  total: number;
  online: number;
  offline: number;
  degraded: number;
  avg_cpu_load: number;
  avg_ram_usage: number;
  avg_disk_usage: number;
  online_rate: number;
}

export interface DashboardDeviceSummary {
  total: number;
  idle: number;
  testing: number;
  offline: number;
  error: number;
  low_battery: number;
  high_temp: number;
}

export interface DashboardAlertSummary {
  total: number;
  low_battery: number;
  high_temp: number;
  error: number;
}

export interface DashboardHostResourcePoint {
  ip: string;
  cpu_load: number;
  ram_usage: number;
  disk_usage: number;
}

export interface DashboardSummary {
  hosts: DashboardHostSummary;
  devices: DashboardDeviceSummary;
  alerts: DashboardAlertSummary;
  host_resources: DashboardHostResourcePoint[];
}

// ─── 通知/调度/审计类型 ──────────────────────────────────────────────────────

export interface NotificationChannel {
  id: number;
  name: string;
  type: 'WEBHOOK' | 'EMAIL' | 'DINGTALK';
  config: Record<string, any>;
  enabled: boolean;
  created_at: string;
}

export interface AlertRule {
  id: number;
  name: string;
  event_type: 'RUN_COMPLETED' | 'RUN_FAILED' | 'RISK_HIGH' | 'DEVICE_OFFLINE';
  channel_id: number;
  channel_name?: string;
  filters: Record<string, any>;
  enabled: boolean;
  created_at: string;
}

export interface TaskSchedule {
  id: number;
  name: string;
  cron_expression: string;
  plan_id: number;
  device_ids?: number[] | null;
  enabled: boolean;
  last_run_at?: string | null;
  next_run_at?: string | null;
  created_by?: number | null;
  created_at: string;
}

export interface TaskScheduleCreatePayload {
  name: string;
  cron_expression: string;
  enabled?: boolean;
  plan_id: number;
  device_ids?: number[];
}

export interface TaskScheduleUpdatePayload {
  name?: string;
  cron_expression?: string;
  enabled?: boolean;
  plan_id?: number;
  device_ids?: number[];
}

export interface ScheduleRunNowResult {
  message: string;
  plan_run_id?: number | null;
  plan_id?: number | null;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  skip: number;
  limit: number;
}

// ─── 编排模型类型 ──────────────────────────────────────────────────────────────

export interface ScriptEntry {
  id: number;
  name: string;
  display_name?: string | null;
  category?: string | null;
  script_type: 'python' | 'shell' | 'bat' | string;
  version: string;
  nfs_path: string;
  content_sha256: string;
  param_schema: Record<string, any>;
  default_params: Record<string, any>;
  is_active: boolean;
  description?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface ActionTemplateEntry {
  id: number;
  name: string;
  description?: string | null;
  action: string;
  version?: string | null;
  params: Record<string, any>;
  timeout_seconds: number;
  retry: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ActionTemplateCreatePayload {
  name: string;
  description?: string;
  action: string;
  version?: string | null;
  params?: Record<string, any>;
  timeout_seconds?: number;
  retry?: number;
  is_active?: boolean;
}

export interface ActionTemplateUpdatePayload {
  name?: string;
  description?: string;
  action?: string;
  version?: string | null;
  params?: Record<string, any>;
  timeout_seconds?: number;
  retry?: number;
  is_active?: boolean;
}

export interface PipelineStep {
  step_id: string;
  action: string;
  version?: string;
  params?: Record<string, any>;
  timeout_seconds: number;
  retry?: number;
  enabled?: boolean;
}

export type PipelinePhase = 'init' | 'patrol' | 'teardown';

export interface PipelinePatrol {
  interval_seconds: number;
  steps: PipelineStep[];
}

export interface PipelineLifecycle {
  timeout_seconds?: number;
  init: PipelineStep[];
  patrol?: PipelinePatrol;
  teardown: PipelineStep[];
}

export interface PipelineDef {
  lifecycle: PipelineLifecycle;
}

export type JobStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'COMPLETED'
  | 'FAILED'
  | 'ABORTED'
  | 'UNKNOWN'
  | 'PENDING_TOOL';

export interface StepTrace {
  id: number;
  job_id: number;
  step_id: string;
  stage: string;
  event_type: 'STARTED' | 'COMPLETED' | 'FAILED' | 'RETRIED';
  status: string;
  output?: string | null;
  error_message?: string | null;
  original_ts: string;
}

export interface JobArtifactEntry {
  id: number;
  job_id: number;
  filename: string | null;
  artifact_type: string;
  size_bytes?: number | null;
  checksum?: string | null;
  created_at?: string | null;
}

// ─── Plan / PlanRun (ADR-0020) ──────────────────────────────────────────────────

export interface PlanStep {
  id: number;
  step_key: string;
  script_name: string;
  script_version: string;
  stage: 'init' | 'patrol' | 'teardown';
  sort_order: number;
  timeout_seconds?: number | null;
  retry: number;
  enabled: boolean;
}

export interface PlanStepCreate {
  step_key: string;
  script_name: string;
  script_version: string;
  stage: 'init' | 'patrol' | 'teardown';
  sort_order?: number;
  timeout_seconds?: number | null;
  retry?: number;
  enabled?: boolean;
}

export type WatcherUnavailableAction = 'fail' | 'degraded' | 'skip';

export interface WatcherPolicy {
  paths?: Record<string, string[]>;
  required_categories?: string[];
  on_unavailable?: WatcherUnavailableAction;
  batch_interval_seconds?: number;
  batch_max_events?: number;
  event_queue_maxsize?: number;
  pull_max_file_mb?: number;
  nfs_quota_mb?: number;
  inotifyd_reconnect_delay?: number;
  polling_interval_seconds?: number;
  probe_timeout_seconds?: number;
  exit_drain_timeout_seconds?: number;
  emit_via_socketio?: boolean;
  emit_via_http_outbox?: boolean;
  log_level?: string;
}

// ADR-0020 §2 唯一事实源：Plan 不再包含 lifecycle JSON，前端按 PlanStep 行 + 直列字段交互。
export interface Plan {
  id: number;
  name: string;
  description?: string | null;
  failure_threshold: number;
  patrol_interval_seconds?: number | null;
  timeout_seconds?: number | null;
  next_plan_id?: number | null;
  watcher_policy?: WatcherPolicy | null;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
  steps: PlanStep[];
}

export interface PlanCreate {
  name: string;
  description?: string;
  failure_threshold?: number;
  patrol_interval_seconds?: number | null;
  timeout_seconds?: number | null;
  next_plan_id?: number | null;
  watcher_policy?: WatcherPolicy | null;
  steps?: PlanStepCreate[];
}

export interface PlanUpdate {
  name?: string;
  description?: string;
  failure_threshold?: number;
  patrol_interval_seconds?: number | null;
  timeout_seconds?: number | null;
  next_plan_id?: number | null;
  watcher_policy?: WatcherPolicy | null;
  steps?: PlanStepCreate[];
}

export type PlanRunStatus = 'RUNNING' | 'SUCCESS' | 'PARTIAL_SUCCESS' | 'FAILED' | 'DEGRADED';
export type PlanRunType = 'MANUAL' | 'SCHEDULE' | 'CHAIN';

export interface PlanDispatchState {
  enqueue_key?: string | null;
  requeue_attempts?: number;
  status?: 'queued' | 'running' | 'completed' | 'failed' | string;
  enqueued_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  last_error?: string | null;
}

export interface PlanSnapshotStep {
  stage: 'init' | 'patrol' | 'teardown';
  step_key: string;
  script_name: string;
  script_version: string;
  nfs_path: string;
  param_schema: Record<string, unknown>;
  default_params: Record<string, unknown>;
  timeout_seconds?: number | null;
  retry: number;
  enabled: boolean;
  sort_order: number;
}

export interface PlanSnapshot {
  plan: {
    id: number;
    name: string;
    description?: string | null;
    failure_threshold: number;
    patrol_interval_seconds?: number | null;
    watcher_policy: WatcherPolicy | Record<string, never>;
  };
  steps: PlanSnapshotStep[];
}

export interface PlanRun {
  id: number;
  plan_id: number;
  status: PlanRunStatus;
  failure_threshold: number;
  run_type: PlanRunType;
  triggered_by?: string | null;
  started_at: string;
  ended_at?: string | null;
  result_summary?: PlanRunResultSummary | null;
  // ADR-0021 dispatch gate progress (PrecheckState typed below)
  run_context?: PlanRunContext | null;
  plan_snapshot?: PlanSnapshot | null;
  parent_plan_run_id?: number | null;
  root_plan_run_id?: number | null;
  chain_index?: number;
  next_plan_triggered?: boolean;
}

export interface PlanRunCreate {
  device_ids: number[];
}

export interface PlanRunPreview {
  plan_id: number;
  plan_name: string;
  device_ids: number[];
  device_count: number;
  job_count: number;
  total_steps: number;
  lifecycle: PipelineLifecycle;
}

export interface PlanJobInstance {
  id: number;
  plan_run_id?: number | null;
  plan_id?: number | null;
  device_id: number;
  device_serial?: string | null;
  host_id?: string | null;
  status: JobStatus;
  status_reason?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  created_at?: string | null;
  step_traces?: StepTrace[];
}

export interface PlanRunSummary {
  plan_run_id: number;
  status: string;
  total_jobs: number;
  status_counts: Record<string, number>;
  pass_rate: number;
  started_at?: string | null;
  ended_at?: string | null;
  result_summary?: Record<string, any> | null;
}

// ─── ADR-0021 dispatch gate precheck (PlanRun.run_context.precheck) ──────────

export type PrecheckPhase = 'verifying' | 'syncing' | 'reverifying' | 'ready' | 'failed';
export type PrecheckHostStatus = 'pending' | 'ok' | 'syncing' | 'synced' | 'failed';
export type PrecheckFinalResult = 'ready' | 'failed' | 'aborted';

export interface PrecheckScriptCheck {
  name: string;
  version: string;
  expected_sha: string;
  actual_sha?: string | null;
  exists: boolean;
  ok: boolean;
  error?: string | null;
}

export interface PrecheckHostState {
  status: PrecheckHostStatus;
  checked_at?: string | null;
  synced_at?: string | null;
  scripts: PrecheckScriptCheck[];
  sync_attempts: number;
  error?: string | null;
}

export interface PrecheckGateFailure {
  code: string;
  message: string;
  inactive_host_ids: string[];
}

export interface PrecheckState {
  phase: PrecheckPhase;
  started_at: string;
  completed_at?: string | null;
  hosts: Record<string, PrecheckHostState>;
  final_result?: PrecheckFinalResult | null;
  errors: string[];
  /** Backend env DISPATCH_SYNC_MAX_ATTEMPTS (ADR Phase B). */
  sync_max_attempts?: number;
  gate_failure?: PrecheckGateFailure | null;
}

export interface PlanRunContext {
  precheck?: PrecheckState;
  dispatch_state?: PlanDispatchState | null;
  dispatch_device_ids?: number[];
  abort_requested?: boolean;
  [key: string]: unknown;
}

// ─── ADR-0021/0022 C5a₂ aggregation endpoints (PlanRunDetailPage) ────────────

export interface ChainDispatchFailed {
  at: string;
  error: string;
}

export interface PlanRunResultSummary {
  total?: number;
  completed?: number;
  failed?: number;
  pass_rate?: number;
  chain_dispatch_failed?: ChainDispatchFailed;
  [key: string]: unknown;
}

export interface ChainNode {
  plan_id: number;
  plan_name?: string | null;
  plan_run_id?: number | null;          // null when status === 'pending' (next not yet triggered)
  status: string;                        // PlanRun.status or 'pending'
  chain_index: number;
  started_at?: string | null;
  ended_at?: string | null;
  duration_seconds?: number | null;
  failure_threshold: number;
  pass_rate?: number | null;
  is_current: boolean;
  is_blocked: boolean;
  block_reason?: string | null;
}

export interface PlanChain {
  plan_run_id: number;
  root_plan_run_id: number;
  nodes: ChainNode[];                    // ordered by chain_index ascending
}

export type StageStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped';

export interface StageStep {
  step_key: string;
  script_name: string;
  stage: 'init' | 'patrol' | 'teardown';
  sort_order: number;
  device_total: number;
  device_succeeded: number;
  device_failed: number;
  device_skipped?: number;  // v3: event_type=COMPLETED + status=SKIPPED
  device_running: number;
}

export interface TimelineStage {
  stage: 'init' | 'patrol' | 'teardown';
  status: StageStatus;
  started_at?: string | null;
  ended_at?: string | null;
  duration_seconds?: number | null;
  device_total: number;
  device_succeeded: number;
  device_failed: number;
  device_skipped?: number;  // v3: summed from steps
  // patrol-only
  patrol_cycle_index?: number | null;
  patrol_active_devices?: number | null;
  patrol_interval_seconds?: number | null;
  steps: StageStep[];
}

export interface PlanRunTimeline {
  plan_run_id: number;
  current_stage: 'init' | 'patrol' | 'teardown' | 'done' | 'pending';
  stages: TimelineStage[];
  aborted_job_count?: number;  // v3: ABORTED jobs 计数
  triggered_at: string;
  triggered_by?: string | null;
  run_type: PlanRunType;
  plan_name?: string | null;
}

export type EventStage = 'trigger' | 'init' | 'patrol' | 'teardown' | 'system';
export type EventSeverity = 'ok' | 'info' | 'warn' | 'err';
export type EventCategory = 'trigger' | 'step' | 'log_signal' | 'audit' | 'system';

export interface PlanRunEvent {
  ts: string;
  stage: EventStage;
  severity: EventSeverity;
  category: EventCategory;
  title: string;
  description: string;
  job_id?: number | null;
  device_id?: number | null;
  device_serial?: string | null;
  ref?: { type: string; id: number } | null;
}

export interface PlanRunEventsPayload {
  plan_run_id: number;
  events: PlanRunEvent[];
  total: number;                         // total under current filter (post-facet)
  facets: {
    by_stage: Record<string, number>;    // includes 'all'
    by_severity: Record<string, number>; // includes 'all'
  };
}

export type DeviceUiStatus = 'completed' | 'running' | 'failed' | 'unknown' | 'backoff' | 'pending';

export interface DeviceMatrixItem {
  device_id: number;
  device_serial?: string | null;
  device_model?: string | null;
  host_id?: string | null;
  job_id: number;
  job_status: JobStatus;
  ui_status: DeviceUiStatus;
  current_stage: 'init' | 'patrol' | 'teardown' | 'done' | 'pending' | 'failed' | 'unknown';
  current_step?: string | null;
  patrol_cycle_count: number;
  patrol_success_cycle_count: number;
  patrol_failed_cycle_count: number;
  current_failure_streak: number;
  next_retry_at?: string | null;
  manual_action?: 'RETRY_NOW' | 'EXIT_REQUESTED' | null;
  log_signal_count: number;
  last_heartbeat_at?: string | null;
  started_at?: string | null;
  created_at?: string | null;
  ended_at?: string | null;
  /** Failure reason e.g. "pending_timeout: agent never claimed job" */
  status_reason?: string | null;
  /** UNKNOWN reconciler grace window remaining (seconds). */
  grace_remaining_seconds?: number | null;
  /** PENDING claim SLA remaining (seconds). */
  pending_claim_remaining_seconds?: number | null;
  /** Why device is BUSY / blocked: active_lease | device_offline | host_offline */
  busy_reason?: string | null;
  /** Job ID holding the active lease when busy_reason=active_lease. */
  busy_lease_job_id?: number | null;
}

export interface PlanRunDevicesPayload {
  plan_run_id: number;
  total: number;
  by_status: Record<string, number>;     // includes 'all'
  by_host: Record<string, number>;
  devices: DeviceMatrixItem[];
}

export interface WatcherCategory {
  category: string;                      // AEE / VENDOR_AEE / ANR / TOMBSTONE / MOBILELOG
  count: number;
  affected_device_count: number;
  trend_change: number;                  // current window - previous (same length) window
  latest_device_serial?: string | null;
  latest_detected_at?: string | null;
}

export interface PackageStat {
  package_name: string;                  // 空 / 缺失统一归 "unknown"
  crash_count: number;                   // AEE + COALESCE(extra.event_type,'CRASH')='CRASH',按 nfs_path 去重
  vendor_crash_count: number;            // VENDOR_AEE 同条件
  anr_count: number;                     // category=ANR OR extra.event_type='ANR',按 path_on_device 去重
  latest_detected_at?: string | null;
}

export interface AeeBreakdown {
  crash_count: number;                   // 跨包累加(与 vendor_crash 互斥)
  vendor_crash_count: number;
  anr_count: number;
  packages: string[];                    // distinct package_name(已合并 unknown 桶)
  by_package: PackageStat[];             // 按 crash + vendor_crash + anr 总数 DESC,平局 pkg ASC
}

export type WatcherTimeScope = 'all' | '15m' | '1h' | '6h' | '24h';

export interface PackageSubtypeCount {
  subtype: string;
  count: number;
}

export interface SubtypeDistribution {
  subtype: string;
  group: 'AEE' | 'VENDOR_AEE' | string;
  count: number;
  share: number;
}

export interface PackageRanking {
  package_name: string;
  total_count: number;
  affected_device_count: number;
  latest_detected_at?: string | null;
  subtype_breakdown: PackageSubtypeCount[];
}

export interface AeeDashboardSection {
  total_events: number;
  affected_device_count: number;
  top_package_name?: string | null;
  top_subtype?: string | null;
  subtype_distribution: SubtypeDistribution[];
  package_ranking: PackageRanking[];
}

export interface WatcherSummary {
  plan_run_id: number;
  window_minutes?: number | null;
  time_scope?: WatcherTimeScope | string;
  window_start_at: string;
  window_end_at: string;
  categories: WatcherCategory[];
  total: number;
  affected_device_count: number;
  total_devices: number;
  abnormal_rate: number;                 // affected / total_devices
  threshold: number;
  exceeded: boolean;
  supports_origin_split?: boolean;
  current_run?: AeeDashboardSection;
  preexisting?: AeeDashboardSection;
  // M0/PR #2: reconciler signal 附带 extra 才会填充;无关联 Job 走早返回 → null
  aee_breakdown?: AeeBreakdown | null;
  // M0/C-6 (§2.4 #5): 该 PlanRun 下 Job 的 watcher 能力快照(后端取最"降级"的一档)。
  //   'unavailable' → reconciler 单通道模式(WatcherSummaryCard 顶栏显示降级徽章);
  //   其余值 / null → 不显示徽章。来源 JobInstance.watcher_capability。
  watcher_capability?: string | null;
}

export interface JobManualActionResult {
  job_id: number;
  plan_run_id: number;
  action: 'manual_retry' | 'manual_exit';
  status: JobStatus;
  manual_action?: string | null;
  next_retry_at?: string | null;
  current_failure_streak: number;
}

export interface PlanRunAbortResult {
  plan_run_id: number;
  status: string;
  released_lease_count?: number;
  aborted_pending_count?: number;
  drained_running_count?: number;
}

export interface PlanRunDispatchRetryResult {
  plan_run_id: number;
  status: string;
  dispatch_state?: PlanDispatchState;
}

// ─── ResourcePool ────────────────────────────────────────────────────────────────

export interface ResourcePool {
  id: number;
  name: string;
  resource_type: string;
  config: Record<string, any>;
  max_concurrent_devices: number;
  host_group: string | null;
  is_active: boolean;
}

export interface ResourcePoolLoad extends ResourcePool {
  current_devices: number;
}

export interface ResourcePoolCreatePayload {
  name: string;
  resource_type?: string;
  config?: Record<string, any>;
  max_concurrent_devices?: number;
  host_group?: string | null;
  is_active?: boolean;
}
