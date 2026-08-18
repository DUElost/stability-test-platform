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
  id: string;
  name?: string | null;
  ip?: string | null;
  ssh_port?: number;
  ssh_user: string | null;
  status: 'ONLINE' | 'OFFLINE' | 'DEGRADED';
  watcher_admin_active?: boolean;
  last_heartbeat: string | null;
  extra: Record<string, unknown>;
  mount_status: Record<string, unknown>;
  capacity?: {
    active_jobs: number;
    active_devices: number;
    online_healthy_devices: number;
    /** 空闲设备槽位（未叠加健康门限） */
    available_slots?: number;
    /** 剩余可派发槽位（= min(available_slots, health_limit)，心跳数据） */
    effective_slots?: number;
  };
  health?: {
    status: 'HEALTHY' | 'DEGRADED' | 'UNSCHEDULABLE';
    reasons: string[];
    cpu_load: number;
    ram_usage: number;
    disk_usage: number | null;
    mount_ok: boolean;
    adb_ok: boolean;
  };
  // ADR-0021 hot-update gate — populated only on GET /hosts/{id}.
  active_job_count?: number;
  active_jobs?: HostActiveJob[];
  // ssh-keyscan result on create/update ("ok" | "failed: <reason>" | null).
  host_key_trust?: string | null;
  /** 与 ONLINE/OFFLINE 正交：曾安装 / 有过心跳 / agent_version */
  agent_installed?: boolean;
  agent_installed_at?: string | null;
  agent_protocol_version?: string | null;
  agent_code_revision?: string | null;
  expected_code_revision?: string | null;
  agent_code_deployed?: string | null;
  agent_code_deployed_at?: string | null;
  agent_code_sync_status?: 'unknown' | 'matched' | 'drift' | 'pending';
}

export interface Device {
  id: number;
  serial: string;
  model: string | null;
  /** SoC platform (#73). Gates MTK-only subsystems such as the AEE Reconciler. */
  platform?: 'MTK' | 'UNISOC' | 'QCOM' | 'UNKNOWN' | null;
  host_id: string | number | null;
  status: 'ONLINE' | 'OFFLINE' | 'BUSY' | 'ERROR';
  /** Authoritative backend admission decision. Legacy servers may omit it. */
  schedulable?: boolean;
  last_seen: string | null;
  tags: string[];
  extra?: Record<string, unknown>;
  current_task?: { name?: string } | null;
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
  params: Record<string, unknown>;
  pipeline_def?: Record<string, unknown> | null;
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
  params: Record<string, unknown>;
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
  artifact_type?: string | null;
  size_bytes: number | null;
  checksum: string | null;
  created_at: string;
}

export interface RunRiskSummary {
  risk_level?: 'S' | 'A' | 'B' | string;
  counts?: {
    events_total?: number;
    aee_entries?: number;
    restart_count?: number;
    by_type?: Record<string, number>;
    by_severity?: { S?: number; A?: number; B?: number };
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
  summary_metrics: Record<string, unknown>;
  risk_summary: RunRiskSummary | null;
  report_status?: string | null;
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
  environment: Record<string, unknown>;
  custom_fields: Record<string, unknown>;
  extra: Record<string, unknown>;
}

export interface JiraRunRecord {
  id: number;
  console_run_id: string;
  vendor: 'transsion' | 'tinno' | string;
  stage: 'upload_list' | 'create' | string;
  dry_run: boolean;
  reporter?: string | null;
  input_source: string;
  plan_run_id?: number | null;
  artifact_id?: number | null;
  status: 'RUNNING' | 'SUCCESS' | 'FAILED' | 'CANCELED' | string;
  started_at: string;
  ended_at?: string | null;
  exit_code?: number | null;
  issue_keys: string[];
  error?: string | null;
  created_by_user_id?: number | null;
  created_at: string;
}

export interface PipelineTemplate {
  name: string;
  description: string;
  pipeline_def: Record<string, unknown>;
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
  avg_disk_usage: number | null;
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
  disk_usage: number | null;
}

export interface DashboardSummary {
  hosts: DashboardHostSummary;
  devices: DashboardDeviceSummary;
  alerts: DashboardAlertSummary;
  host_resources: DashboardHostResourcePoint[];
}

// ─── File server / NFS operations ───────────────────────────────────────────

export interface FileServerMetricPoint {
  timestamp: number;
  value: number;
}

export interface FileServerNodeIdentity {
  hostname: string;
  address: string;
  cpu_count: number | null;
  uptime_seconds: number | null;
}

export interface FileServerNodeMonitoring {
  prometheus_available: boolean;
  error: string | null;
}

export interface FileServerClientMount {
  path: string;
  source: string | null;
  filesystem: string | null;
  mounted: boolean;
  backend_write_access: boolean;
}

export interface FileServerNodeSystem {
  cpu_usage_pct: number | null;
  memory_usage_pct: number | null;
  memory_total_bytes: number | null;
  load1: number | null;
  disk_read_bytes_per_second: number | null;
  disk_write_bytes_per_second: number | null;
  network_receive_bytes_per_second: number | null;
  network_transmit_bytes_per_second: number | null;
}

export interface FileServerStorage {
  path: string;
  source: string | null;
  filesystem: string | null;
  mounted: boolean;
  backend_write_access: boolean;
  total_bytes: number;
  used_bytes: number;
  available_bytes: number;
  used_pct: number;
  inode_total: number;
  inode_used: number;
  inode_available: number;
  inode_used_pct: number;
}

export interface FileServerNfs {
  service_ready: boolean;
  exported: boolean;
  export_targets: string[];
  server_threads: number | null;
  requests_per_second: number | null;
  rpc_errors_per_second: number | null;
  stale_file_handles_total: number | null;
  connections_total: number | null;
}

export interface FileServerControlPlanePanel {
  node: FileServerNodeIdentity;
  system: FileServerNodeSystem;
  client_mount: FileServerClientMount;
  monitoring: FileServerNodeMonitoring;
}

export interface FileServerStoragePanel {
  node: FileServerNodeIdentity;
  same_source: boolean;
  system: FileServerNodeSystem;
  disk: FileServerStorage;
  nfs: FileServerNfs;
  monitoring: FileServerNodeMonitoring;
}

export interface FileServerDeviceLogDisk {
  host_id: string;
  ip: string | null;
  path: string;
  total_bytes: number;
  used_bytes: number;
  available_bytes: number;
  usage_percent: number;
  last_heartbeat: string | null;
}

export interface FileServerOverview {
  generated_at: string;
  status: 'healthy' | 'warning' | 'critical';
  control_plane: FileServerControlPlanePanel;
  storage_server: FileServerStoragePanel;
  agents: {
    total: number;
    mounted: number;
    failed: number;
    unreported: number;
    items: Array<{
      host_id: string;
      ip: string | null;
      status: string;
      mounted: boolean | null;
      last_heartbeat: string | null;
    }>;
  };
  device_log_disks: {
    total: number;
    reported: number;
    warning: number;
    critical: number;
    items: FileServerDeviceLogDisk[];
  };
  history: {
    hours: number;
    capacity_usage_pct: FileServerMetricPoint[];
    cpu_usage_pct: FileServerMetricPoint[];
    memory_usage_pct: FileServerMetricPoint[];
    nfs_requests_per_second: FileServerMetricPoint[];
  };
  alerts: Array<{
    severity: 'warning' | 'critical';
    code: string;
    message: string;
  }>;
}

// ─── Phase 2: 成功率/失败率细分 ──────────────────────────────────────────────

export interface HostFailureRateItem {
  host_id: string;
  hostname: string;
  ip_address: string | null;
  total_jobs: number;
  failed: number;
  failure_rate: number;
}

export interface HostFailureRateResponse {
  items: HostFailureRateItem[];
  days: number;
}

export interface PlanSuccessRateItem {
  plan_id: number;
  plan_name: string;
  total_jobs: number;
  passed: number;
  failed: number;
  pass_rate: number;
}

export interface PlanSuccessRateResponse {
  items: PlanSuccessRateItem[];
  days: number;
}

export interface PlanRunPassRatePoint {
  date: string;
  avg_pass_rate: number;
  run_count: number;
}

export interface PlanRunPassRateTrendResponse {
  points: PlanRunPassRatePoint[];
  days: number;
}

// ─── 通知/调度/审计类型 ──────────────────────────────────────────────────────

export interface NotificationChannel {
  id: number;
  name: string;
  type: 'WEBHOOK' | 'EMAIL' | 'DINGTALK';
  config: Record<string, unknown>;
  enabled: boolean;
  created_at: string;
}

export interface AlertRule {
  id: number;
  name: string;
  event_type: 'RUN_COMPLETED' | 'RUN_FAILED' | 'RISK_HIGH' | 'DEVICE_OFFLINE';
  channel_id: number;
  channel_name?: string;
  filters: Record<string, unknown>;
  enabled: boolean;
  created_at: string;
}

export interface NotificationLog {
  id: number;
  source: 'PLATFORM' | 'ALERTMANAGER';
  event_type: string;
  severity: 'info' | 'warning' | 'critical';
  title: string;
  message: string;
  context: Record<string, unknown>;
  read: boolean;
  created_at: string;
}

export interface NotificationLogsResponse {
  items: NotificationLog[];
  total: number;
  skip: number;
  limit: number;
}

export interface UnreadCountResponse {
  unread: number;
}

export interface TaskSchedule {
  id: number;
  name: string;
  cron_expr: string;
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
  cron_expr: string;
  enabled?: boolean;
  plan_id: number;
  device_ids?: number[];
}

export interface TaskScheduleUpdatePayload {
  name?: string;
  cron_expr?: string;
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

// ─── API error contract ───────────────────────────────────────────────────────

export interface ApiErrorCapabilities {
  retry?: boolean;
  retry_dispatch?: boolean;
  navigate_to_plan_run?: boolean;
  [key: string]: boolean | undefined;
}

export interface StructuredApiError {
  code: string;
  message: string;
  retryable?: boolean;
  plan_run_id?: number;
  current_status?: string;
  capabilities?: ApiErrorCapabilities;
  [key: string]: unknown;
}

export interface ApiResponseEnvelope<T> {
  data?: T;
  error?: StructuredApiError | null;
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
  param_schema: Record<string, unknown>;
  default_params: Record<string, unknown>;
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
  params: Record<string, unknown>;
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
  params?: Record<string, unknown>;
  timeout_seconds?: number;
  retry?: number;
  is_active?: boolean;
}

export interface ActionTemplateUpdatePayload {
  name?: string;
  description?: string;
  action?: string;
  version?: string | null;
  params?: Record<string, unknown>;
  timeout_seconds?: number;
  retry?: number;
  is_active?: boolean;
}

export interface PipelineStep {
  step_id: string;
  action: string;
  version?: string;
  params?: Record<string, unknown>;
  timeout_seconds: number;
  /**
   * 停滞钟（#115）：多久无 PROGRESS 戳算卡死。编辑器不提供输入框，但**必须**
   * 带在这里——PlanEditPage 保存时整体替换 PlanStep 行，字段不透传就等于把
   * 已配好的停滞钟静默清成 NULL。
   */
  stall_seconds?: number | null;
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
  | 'UNKNOWN';

export interface StepTrace {
  id: number;
  job_id: number;
  step_id: string;
  stage: string;
  event_type: 'STARTED' | 'COMPLETED' | 'FAILED' | 'RETRIED';
  status: string;
  output?: string | null;
  error_message?: string | null;
  exit_code?: number | null;
  metadata?: Record<string, unknown> | null;
  original_ts: string;
  created_at: string;
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
  /** 停滞钟（#115 阶段 1）：多久无 PROGRESS 戳算卡死。null/0 = 关闭。 */
  stall_seconds?: number | null;
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
  stall_seconds?: number | null;
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
  /**
   * INIT→PATROL barrier 预算（秒）。null = 沿用后端 600s。
   * 不是独立旋钮：只有先到者在等，要覆盖同 host 的 init 落差
   * ≈ (ceil(设备数 / permit_cap) − 1) × 单设备 init 耗时。
   * 含自动刷机等长耗时前置步骤的计划必须抬高。
   */
  barrier_timeout_seconds?: number | null;
  /**
   * #174: barrier 绝对硬顶（秒）。null = 不设上限（#117 续期行为）。
   * 从首次进入 barrier 等待起算，与 barrier_timeout_seconds（全体停滞
   * 滑动兜底）取更早者。
   */
  barrier_max_wait_seconds?: number | null;
  auto_archive_interval_seconds?: number | null;
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
  /**
   * INIT→PATROL barrier 预算（秒）。null = 沿用后端 600s。
   * 不是独立旋钮：只有先到者在等，要覆盖同 host 的 init 落差
   * ≈ (ceil(设备数 / permit_cap) − 1) × 单设备 init 耗时。
   * 含自动刷机等长耗时前置步骤的计划必须抬高。
   */
  barrier_timeout_seconds?: number | null;
  /** #174: barrier 绝对硬顶（秒）。null = 不设上限。 */
  barrier_max_wait_seconds?: number | null;
  auto_archive_interval_seconds?: number | null;
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
  /**
   * INIT→PATROL barrier 预算（秒）。null = 沿用后端 600s。
   * 不是独立旋钮：只有先到者在等，要覆盖同 host 的 init 落差
   * ≈ (ceil(设备数 / permit_cap) − 1) × 单设备 init 耗时。
   * 含自动刷机等长耗时前置步骤的计划必须抬高。
   */
  barrier_timeout_seconds?: number | null;
  /** #174: barrier 绝对硬顶（秒）。null = 不设上限。 */
  barrier_max_wait_seconds?: number | null;
  auto_archive_interval_seconds?: number | null;
  next_plan_id?: number | null;
  watcher_policy?: WatcherPolicy | null;
  steps?: PlanStepCreate[];
  /**
   * 乐观锁令牌（#268 多Worker B3）：带上加载时 Plan.updated_at；
   * 与后端当前值不一致 → 409，防两个浏览器基于同一旧版本互相覆盖。
   */
  expected_updated_at?: string;
}

/**
 * 链尾追加（#281 P1）：由后端在单事务内「锁定链尾 → 校验版本 → 创建新
 * Plan → 更新 next_plan_id」，冲突整体回滚，不再产生孤立 Plan。
 */
export interface PlanChainTailCreate {
  name: string;
  description?: string;
  steps?: PlanStepCreate[];
  /**
   * 链尾版本令牌：加载链尾时的 updated_at；无法确定链尾（超出最近 200 条
   * 窗口）时可省略，后端仍以行锁保证原子追加。
   */
  expected_updated_at?: string | null;
}

export type PlanRunStatus =
  | 'QUEUED'
  | 'PRECHECK'
  | 'RUNNING'
  | 'SUCCESS'
  | 'PARTIAL_SUCCESS'
  | 'FAILED'
  | 'DEGRADED';
export type PlanRunType = 'MANUAL' | 'SCHEDULE' | 'CHAIN';

export interface PlanDispatchState {
  enqueue_key?: string | null;
  requeue_attempts?: number;
  status?: 'queued' | 'running' | 'completed' | 'failed' | string;
  enqueued_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  last_error?: string | null;
  /** Backend authority for whether retry-dispatch is currently legal. */
  retryable?: boolean;
  /** Backend-derived stale projection/deadline. */
  stale?: boolean;
  deadline_at?: string | null;
}

export interface PlanSnapshotStep {
  id?: number;
  stage: 'init' | 'patrol' | 'teardown';
  step_key: string;
  script_name: string;
  script_version: string;
  nfs_path: string;
  script_type?: string;
  content_sha256?: string;
  param_schema: Record<string, unknown>;
  default_params: Record<string, unknown>;
  timeout_seconds?: number | null;
  retry: number;
  enabled: boolean;
  sort_order: number;
}

export interface PlanSnapshot {
  schema_version?: number;
  captured_at?: string;
  plan: {
    id: number;
    name: string;
    description?: string | null;
    failure_threshold: number;
    patrol_interval_seconds?: number | null;
    timeout_seconds?: number | null;
    auto_archive_interval_seconds?: number | null;
    next_plan_id?: number | null;
    watcher_policy: WatcherPolicy | Record<string, never>;
    created_by?: string | null;
    created_at?: string;
    updated_at?: string;
  };
  steps: PlanSnapshotStep[];
  lifecycle?: PipelineLifecycle;
}

export interface PlanRunCapabilities {
  abort?: boolean;
  retry_dispatch?: boolean;
  final_archive?: boolean;
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
  plan_name?: string | null;
  capabilities?: PlanRunCapabilities | null;
  /** ADR-0026 admission queue — null/absent for legacy runs. */
  queue_reason?: 'DEVICE_BUSY' | 'RESOURCE_BUSY' | 'PRIORITY_WAIT' | 'PRECHECK_STALE' | string | null;
  enqueued_at?: string | null;
  next_admission_at?: string | null;
  priority?: number;
}

export interface PlanRunCreate {
  device_ids: number[];
  /** Optional operator note → PlanRun.run_context.note */
  note?: string;
  /**
   * Per-execution WiFi choice → PlanRun.run_context.wifi_pool_id.
   * Omit / null = do not connect (default). Credentials are never sent
   * inline — pick a pre-configured wifi ResourcePool instead.
   */
  wifi_pool_id?: number | null;
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

/** ADR-0026 §3: RUNNING job sub-state for permit/barrier/patrol observability. */
export type JobExecutionState =
  | 'WAITING_BARRIER'
  | 'WAITING_EXECUTION_SLOT'
  | 'EXECUTING_STEP'
  | 'PATROL_SLEEP';

export interface PlanJobInstance {
  id: number;
  plan_run_id?: number | null;
  plan_id?: number | null;
  device_id: number;
  device_serial?: string | null;
  host_id?: string | null;
  status: JobStatus;
  status_reason?: string | null;
  execution_state?: JobExecutionState | null;
  last_execution_heartbeat_at?: string | null;
  last_progress_at?: string | null;
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
  result_summary?: Record<string, unknown> | null;
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

export interface PlanRunAbortRequest {
  at: string;
  reason: string;
  triggered_by?: string | null;
  deadline_at?: string | null;
  requested_job_ids?: number[];
  acknowledged_job_ids?: number[];
}

export interface PlanRunContext {
  precheck?: PrecheckState;
  dispatch_state?: PlanDispatchState | null;
  dispatch_device_ids?: number[];
  abort_requested?: PlanRunAbortRequest;
  /** Optional operator note from plan-execute (no dedicated DB column). */
  note?: string;
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

/** GET /api/v1/log-signals/orphans — admin (#213 D3) */
export interface OrphanLogSignal {
  id: number;
  host_id: string;
  device_serial: string;
  seq_no: number;
  category: string;
  source: string;
  path_on_device: string;
  artifact_uri?: string | null;
  detected_at?: string | null;
  received_at?: string | null;
  device_log_event_id?: string | null;
  extra?: Record<string, unknown> | null;
}

export interface OrphanLogSignalList {
  items: OrphanLogSignal[];
  total: number;
  skip: number;
  limit: number;
  excluding_call_sites: string[];
}

export type DeviceUiStatus =
  | 'completed'
  | 'running'
  | 'failed'
  | 'aborted'
  | 'unknown'
  | 'backoff'
  | 'pending';

/** Device ADB / host reachability — orthogonal to job execution state. */
export type DeviceLinkStatus =
  | 'online'
  | 'offline'
  | 'adb_error'
  | 'host_offline'
  | 'unknown';

export interface JobActionCapabilities {
  manual_retry: boolean;
  manual_exit: boolean;
  open_report?: boolean;
  /** Present when manual_retry is false for a RUNNING job due to device disconnect. */
  manual_retry_blocked_reason?: 'device_disconnected' | null;
}

export interface DeviceMatrixItem {
  device_id: number;
  device_serial?: string | null;
  device_model?: string | null;
  host_id?: string | null;
  job_id: number;
  job_status: JobStatus;
  ui_status: DeviceUiStatus;
  /**
   * Job execution projection independent of device link (patrol/backoff/etc.).
   * Prefer this over `ui_status` for the 执行 dimension — `ui_status` blends in
   * device reachability and is kept only for backward compatibility.
   */
  job_exec_status?: DeviceUiStatus;
  /** Device ADB / host reachability for manual-retry gating and display. */
  device_link_status?: DeviceLinkStatus;
  adb_state?: string | null;
  adb_connected?: boolean | null;
  current_stage: 'init' | 'patrol' | 'teardown' | 'done' | 'pending' | 'failed' | 'aborted' | 'unknown';
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
  /** Absolute server-derived claim deadline, preferred over frontend SLA math. */
  pending_claim_deadline_at?: string | null;
  /** Absolute server-derived RUNNING heartbeat/stall deadline. */
  heartbeat_deadline_at?: string | null;
  /** Authoritative backend projection; avoids duplicating timeout policy. */
  is_stuck?: boolean;
  /** Why device is BUSY / blocked: active_lease | device_offline | host_offline */
  busy_reason?: string | null;
  /** Job ID holding the active lease when busy_reason=active_lease. */
  busy_lease_job_id?: number | null;
  /** Authoritative manual actions for the current Job state. */
  capabilities?: JobActionCapabilities | null;
}

export interface PlanRunDevicesPayload {
  plan_run_id: number;
  total: number;
  /** Facet over `job_exec_status` (执行维度). Includes 'all'. */
  by_status: Record<string, number>;
  /** Facet over `device_link_status` (连接维度). Includes 'all'. */
  by_link_status?: Record<string, number>;
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

export interface CrashDetailEntry {
  package_name: string;
  subtype: string;
  group: string;
  device_serial: string;
  detected_at: string | null;
  entry_origin?: string | null;
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
  // ADR-0025 Sprint 3: 运行日志归档状态（控制面按需拉取聚合）；无关联 Job 时 null
  archive?: WatcherArchive | null;
}

export interface WatcherAgentOpsMetrics {
  pruned_total: number;
  local_disk_usage_pct: number | null;
  spill_cycles: number;
  spilled_total: number;
}

export type DedupScanStatus = 'pending' | 'scanned' | 'merged' | null;

export interface WatcherArchive {
  ops_metrics: WatcherAgentOpsMetrics;
  scan_status?: DedupScanStatus;
  scan_triggered_at?: string | null;
  archived_jobs?: number;
  pending_jobs?: number;
  failed_jobs?: number;
  /** Authoritative readiness for final archive extraction. */
  readiness?: {
    ready: boolean;
    reason?: string | null;
  } | null;
  /** Compatibility with backends exposing the readiness boolean directly. */
  ready_for_extract?: boolean;
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
  phase?: 'precheck' | 'running';
  abort_requested?: PlanRunAbortRequest | null;
  aborted_jobs?: number[];
  pending_aborted_job_ids?: number[];
  running_abort_requested_job_ids?: number[];
  quarantined_job_ids?: number[];
  // Legacy counters retained during the one-shot API transition.
  released_leases?: number;
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
  config: Record<string, unknown>;
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
  config?: Record<string, unknown>;
  max_concurrent_devices?: number;
  host_group?: string | null;
  is_active?: boolean;
}
