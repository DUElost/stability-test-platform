// ─── 基础实体类型 ──────────────────────────────────────────────────────────────

export interface Host {
  id: number;
  name: string;
  ip: string;
  ssh_port: number;
  ssh_user: string | null;
  status: 'ONLINE' | 'OFFLINE' | 'DEGRADED';
  last_heartbeat: string | null;
  extra: Record<string, any>;
  mount_status: Record<string, any>;
}

export interface Device {
  id: number;
  serial: string;
  model: string | null;
  host_id: number | null;
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

export interface TaskTemplate {
  type: string;
  name: string;
  description: string;
  default_params: Record<string, any>;
  script_paths: Record<string, string>;
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
  task_template_id?: number | null;
  tool_id?: number | null;
  task_type: string;
  params: Record<string, any>;
  target_device_id?: number | null;
  workflow_definition_id?: number | null;
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
  task_type?: string;
  params?: Record<string, any>;
  enabled?: boolean;
  workflow_definition_id?: number | null;
  device_ids?: number[];
  task_template_id?: number | null;
  tool_id?: number | null;
  target_device_id?: number | null;
}

export interface TaskScheduleUpdatePayload {
  name?: string;
  cron_expression?: string;
  task_type?: string;
  params?: Record<string, any>;
  enabled?: boolean;
  workflow_definition_id?: number | null;
  device_ids?: number[];
  task_template_id?: number | null;
  tool_id?: number | null;
  target_device_id?: number | null;
}

export interface ScheduleRunNowResult {
  message: string;
  task_id?: number | null;
  workflow_run_id?: number | null;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  skip: number;
  limit: number;
}

// ─── 编排模型类型 ──────────────────────────────────────────────────────────────

export interface ToolEntry {
  id: number;
  name: string;
  version: string;
  script_path: string;
  script_class?: string | null;
  param_schema: Record<string, any>;
  is_active: boolean;
  description?: string | null;
  category?: string | null;
  created_at: string;
  updated_at?: string;
}

export interface ScriptEntry {
  id: number;
  name: string;
  display_name?: string | null;
  category?: string | null;
  script_type: 'python' | 'shell' | 'bat' | string;
  version: string;
  nfs_path: string;
  entry_point?: string | null;
  content_sha256: string;
  param_schema: Record<string, any>;
  is_active: boolean;
  description?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface BuiltinActionEntry {
  name: string;
  label: string;
  category: 'device' | 'process' | 'file' | 'log' | 'script';
  description: string;
  param_schema: Record<string, any>;
  is_active: boolean;
  updated_at: string;
}

export interface BuiltinActionUpdatePayload {
  label?: string;
  category?: 'device' | 'process' | 'file' | 'log' | 'script';
  description?: string;
  param_schema?: Record<string, any>;
  is_active?: boolean;
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

export interface PipelineDef {
  stages: {
    prepare?: PipelineStep[];
    execute?: PipelineStep[];
    post_process?: PipelineStep[];
  };
}

export interface PipelineStepOverride {
  template_name: string;
  stage: 'prepare' | 'execute' | 'post_process';
  step_id: string;
  params?: Record<string, any>;
  timeout_seconds?: number;
  retry?: number;
  enabled?: boolean;
}

export interface TaskTemplateEntry {
  id: number;
  workflow_definition_id: number;
  name: string;
  sort_order: number;
  pipeline_def: PipelineDef;
}

export interface WorkflowDefinition {
  id: number;
  name: string;
  description?: string | null;
  failure_threshold: number;
  setup_pipeline?: PipelineDef | null;
  teardown_pipeline?: PipelineDef | null;
  task_templates?: TaskTemplateEntry[];
  created_at: string;
}

export interface WorkflowDefinitionCreate {
  name: string;
  description?: string;
  failure_threshold?: number;
  setup_pipeline?: PipelineDef | null;
  teardown_pipeline?: PipelineDef | null;
  task_templates?: Omit<TaskTemplateEntry, 'id' | 'workflow_definition_id'>[];
}

export type WorkflowStatus = 'RUNNING' | 'SUCCESS' | 'PARTIAL_SUCCESS' | 'FAILED' | 'DEGRADED';
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
  stage: 'prepare' | 'execute' | 'post_process';
  event_type: 'STARTED' | 'COMPLETED' | 'FAILED' | 'RETRIED';
  status: string;
  output?: string | null;
  error_message?: string | null;
  original_ts: string;
}

export interface JobInstance {
  id: number;
  workflow_run_id: number;
  workflow_definition_id?: number | null;
  task_template_id: number;
  host_id: string;
  device_id: number;
  device_serial?: string | null;
  status: JobStatus;
  status_reason?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  created_at: string;
  updated_at?: string;
  step_traces?: StepTrace[];
}

export interface PaginatedJobList {
  items: JobInstance[];
  total: number;
  skip: number;
  limit: number;
}

export interface WorkflowRun {
  id: number;
  workflow_definition_id: number;
  status: WorkflowStatus;
  failure_threshold: number;
  triggered_by?: string | null;
  started_at: string;
  ended_at?: string | null;
  jobs?: JobInstance[];
}

export interface WorkflowRunCreate {
  device_ids: number[];
  failure_threshold?: number;
  step_overrides?: PipelineStepOverride[];
}

export interface WorkflowRunPreviewTemplate {
  id?: number;
  name: string;
  sort_order?: number;
  resolved_pipeline: PipelineDef;
  total_steps: number;
  disabled_steps: number;
  executable_steps: number;
}

export interface WorkflowRunPreview {
  workflow_definition_id: number;
  failure_threshold: number;
  device_ids: number[];
  device_count: number;
  template_count: number;
  job_count: number;
  executable_steps_per_device: number;
  templates: WorkflowRunPreviewTemplate[];
}

export interface WorkflowSummary {
  workflow_run_id: number;
  workflow_definition_id: number;
  workflow_name?: string | null;
  status: string;
  failure_threshold: number;
  triggered_by?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  result_summary?: Record<string, any> | null;
  statistics: {
    total_jobs: number;
    status_distribution: Record<string, number>;
    pass_rate: number;
    failed_count: number;
    avg_duration_seconds: number;
  };
  device_results: Array<{
    job_id: number;
    device_id: number;
    device_serial?: string | null;
    status: string;
    status_reason?: string | null;
    started_at?: string | null;
    ended_at?: string | null;
    duration_seconds?: number | null;
  }>;
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

// Legacy aliases
/** @deprecated Use ToolEntry instead */
export type Tool = ToolEntry;
/** @deprecated category is now a string on ToolEntry.category */
export interface ToolCategory {
  name: string;
}
