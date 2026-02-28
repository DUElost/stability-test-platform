import axios from 'axios';

// API 基础配置
const apiClient = axios.create({
  baseURL: '/api/v1',
  headers: {
    'Content-Type': 'application/json',
  },
});

// 请求拦截器
apiClient.interceptors.request.use(
  (config) => {
    // 添加认证 token
    const token = localStorage.getItem('access_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    if (import.meta.env.DEV) console.log(`[API] ${config.method?.toUpperCase()} ${config.url}`);
    return config;
  },
  (error) => {
    if (import.meta.env.DEV) console.error('[API] Request error:', error);
    return Promise.reject(error);
  }
);

// 响应拦截器
apiClient.interceptors.response.use(
  (response) => {
    if (import.meta.env.DEV) console.log(`[API] Response:`, response.data);
    return response;
  },
  async (error) => {
    if (import.meta.env.DEV) console.error('[API] Response error:', error);

    // 处理 401 未授权错误
    if (error.response?.status === 401) {
      const refreshToken = localStorage.getItem('refresh_token');
      if (refreshToken && error.config && !error.config.__retry) {
        error.config.__retry = true;
        try {
          // 尝试刷新 token
          const response = await axios.post('/api/v1/auth/refresh', {
            refresh_token: refreshToken,
          });
          const { access_token, refresh_token } = response.data;
          localStorage.setItem('access_token', access_token);
          localStorage.setItem('refresh_token', refresh_token);

          // 重试原请求
          error.config.headers.Authorization = `Bearer ${access_token}`;
          return apiClient(error.config);
        } catch (refreshError) {
          // 刷新失败，清除 token 并跳转登录
          localStorage.removeItem('access_token');
          localStorage.removeItem('refresh_token');
          window.location.href = '/login';
          return Promise.reject(refreshError);
        }
      } else {
        // 没有 refresh token，直接跳转登录
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
        window.location.href = '/login';
      }
    }

    return Promise.reject(error);
  }
);

// 类型定义
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
  // ADB 连接状态
  adb_state?: string | null;
  adb_connected?: boolean | null;
  // 硬件信息
  battery_level?: number | null;
  battery_temp?: number | null;
  temperature?: number | null;
  wifi_rssi?: number | null;
  wifi_ssid?: string | null;
  network_latency?: number | null;  // 网络延迟 (ms)
  // 系统资源
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

  // 分布式任务支持
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

  // 分布式任务支持
  group_id?: string;

  // 进度信息
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

// 工具管理类型
export interface ToolCategory {
  id: number;
  name: string;
  description?: string;
  icon?: string;
  order: number;
  enabled: boolean;
  created_at: string;
  tools_count?: number;
}

export interface Tool {
  id: number;
  category_id: number;
  category_name?: string;
  name: string;
  description?: string;
  script_path: string;
  script_class?: string;
  script_type: string;
  default_params: Record<string, any>;
  param_schema: Record<string, any>;
  timeout: number;
  need_device: boolean;
  enabled: boolean;
  created_at: string;
  updated_at?: string;
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

// Results summary types
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

// Workflow types
export interface WorkflowStep {
  id: number;
  workflow_id: number;
  order: number;
  name: string;
  tool_id?: number | null;
  task_type?: string | null;
  params: Record<string, any>;
  target_device_id?: number | null;
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'SKIPPED';
  task_run_id?: number | null;
  error_message?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface Workflow {
  id: number;
  name: string;
  description?: string | null;
  status: 'DRAFT' | 'READY' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELED';
  created_by?: number | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  steps: WorkflowStep[];
}

export interface WorkflowStepCreate {
  name: string;
  tool_id?: number | null;
  task_type?: string | null;
  params?: Record<string, any>;
  target_device_id?: number | null;
}

export interface WorkflowCreate {
  name: string;
  description?: string;
  steps: WorkflowStepCreate[];
}

// Stats types
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

// Notification types
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

// Paginated response type
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  skip: number;
  limit: number;
}

// ─── 新编排模型类型 ────────────────────────────────────────────────────────────

export interface ToolEntry {
  id: number;
  name: string;
  version: string;
  script_path: string;
  script_class?: string | null;
  param_schema: Record<string, any>;
  is_active: boolean;
  created_at: string;
}

export interface PipelineStep {
  step_id: string;
  action: string;
  version?: string;
  params?: Record<string, any>;
  timeout_seconds: number;
  retry?: number;
}

export interface PipelineDef {
  stages: {
    prepare?: PipelineStep[];
    execute?: PipelineStep[];
    post_process?: PipelineStep[];
  };
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
  task_templates?: TaskTemplateEntry[];
  created_at: string;
}

export interface WorkflowDefinitionCreate {
  name: string;
  description?: string;
  failure_threshold?: number;
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
  task_template_id: number;
  host_id: string;
  device_id: number;
  device_serial?: string | null;
  status: JobStatus;
  status_reason?: string | null;
  created_at: string;
  updated_at: string;
  step_traces?: StepTrace[];
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
}

// 解包后端统一响应格式 { data: T, error: null | { code, message } }
async function unwrapApiResponse<T>(request: Promise<{ data: { data?: T; error?: { code: string; message: string } | null } }>): Promise<T> {
  const resp = await request;
  const body = resp.data as any;
  if (body?.error) throw new Error(`[${body.error.code}] ${body.error.message}`);
  return body?.data ?? body;
}

// ─── API 函数 ───────────────────────────────────────────────────────────────────

export const api = {
  // 认证相关
  auth: {
    me: () => apiClient.get<User>('/auth/me'),
    login: (username: string, password: string) =>
      apiClient.post<{ access_token: string; refresh_token: string; token_type: string }>(
        '/auth/login',
        new URLSearchParams({ username, password }),
        { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } }
      ),
    register: (data: { username: string; password: string; role?: string }) =>
      apiClient.post<User>('/auth/register', data),
    refresh: (refreshToken: string) =>
      apiClient.post<{ access_token: string; refresh_token: string; token_type: string }>(
        '/auth/refresh',
        { refresh_token: refreshToken }
      ),
  },

  // 主机相关
  hosts: {
    list: (skip = 0, limit = 50) => apiClient.get<PaginatedResponse<Host>>('/hosts', { params: { skip, limit } }),
    get: (id: number) => apiClient.get<Host>(`/hosts/${id}`),
    create: (data: { name: string; ip: string; ssh_port?: number; ssh_user?: string }) =>
      apiClient.post<Host>('/hosts', data),
    update: (id: number, data: { name: string; ip: string; ssh_port?: number; ssh_user?: string }) =>
      apiClient.put<Host>(`/hosts/${id}`, data),
  },

  // 设备相关
  devices: {
    list: (skip = 0, limit = 50, status?: string, tags?: string) => apiClient.get<PaginatedResponse<Device>>('/devices', { params: { skip, limit, ...(status ? { status } : {}), ...(tags ? { tags } : {}) } }),
    get: (id: number) => apiClient.get<Device>(`/devices/${id}`),
    create: (data: { serial: string; model?: string; host_id?: number; tags?: string[] }) =>
      apiClient.post<Device>('/devices', data),
    updateTags: (id: number, tags: string[]) =>
      apiClient.put<Device>(`/devices/${id}/tags`, tags),
  },

  // 任务相关
  tasks: {
    list: (skip = 0, limit = 50, status?: string) => apiClient.get<PaginatedResponse<Task>>('/tasks', { params: { skip, limit, ...(status ? { status } : {}) } }),
    get: (id: number) => apiClient.get<Task>(`/tasks/${id}`),
    listTemplates: () => apiClient.get<TaskTemplate[]>('/task-templates'),
    create: (data: {
      name: string;
      type: string;
      template_id?: number;
      target_device_id?: number;
      device_serial?: string;
      params?: Record<string, any>;
      pipeline_def?: Record<string, any>;
      priority?: number;
    }) =>
      apiClient.post<Task>('/tasks', data),
    cancel: (id: number) => apiClient.post(`/tasks/${id}/cancel`),
    delete: (id: number) => apiClient.delete<void>(`/tasks/${id}`),
    retry: (id: number) => apiClient.post(`/tasks/${id}/retry`),
    batchCancel: (taskIds: number[]) =>
      apiClient.post<{ success: number[]; failed: any[]; total: number }>('/tasks/batch/cancel', { task_ids: taskIds }),
    batchRetry: (taskIds: number[]) =>
      apiClient.post<{ success: number[]; failed: any[]; total: number }>('/tasks/batch/retry', { task_ids: taskIds }),
    dispatch: (taskId: number, data: { host_id: number; device_id: number }) =>
      apiClient.post<TaskRun>(`/tasks/${taskId}/dispatch`, data),
    getRuns: (taskId: number, skip = 0, limit = 50) => apiClient.get<PaginatedResponse<TaskRun>>(`/tasks/${taskId}/runs`, { params: { skip, limit } }),
    getRunReport: (runId: number) => apiClient.get<RunReport>(`/runs/${runId}/report`),
    getRunReportExportUrl: (runId: number, format: 'markdown' | 'json' = 'markdown') =>
      `/api/v1/runs/${runId}/report/export?format=${format}`,
    createRunJiraDraft: (runId: number) => apiClient.post<JiraDraft>(`/runs/${runId}/jira-draft`),
    getCachedReport: (runId: number) => apiClient.get<RunReport>(`/runs/${runId}/report/cached`),
    getCachedJiraDraft: (runId: number) => apiClient.get<JiraDraft>(`/runs/${runId}/jira-draft/cached`),
    artifactDownloadUrl: (taskId: number, runId: number, artifactId: number) =>
      `/api/v1/tasks/${taskId}/runs/${runId}/artifacts/${artifactId}/download`,
    // 查询Agent日志
    queryAgentLogs: (data: { host_id: number; log_path?: string; lines?: number }) =>
      apiClient.post<AgentLogOut>('/agent/logs', data),
    // RunStep API
    getRunSteps: (runId: number) => apiClient.get<RunStep[]>(`/runs/${runId}/steps`),
    // Pipeline templates
    listPipelineTemplates: () => apiClient.get<PipelineTemplate[]>('/pipeline/templates'),
    getPipelineTemplate: (name: string) => apiClient.get<PipelineTemplate>(`/pipeline/templates/${name}`),
  },

  // 心跳相关
  heartbeat: {
    send: (hostId: number, data: { status: string; mount_status?: Record<string, any> }) =>
      apiClient.post(`/heartbeat`, { host_id: hostId, ...data }),
  },

  // 部署相关
  deploy: {
    trigger: (hostId: number, installPath: string = '/opt/stability-test-agent') =>
      apiClient.post<{ id: number; host_id: number; status: string; started_at: string }>(
        `/deploy/hosts/${hostId}`,
        { install_path: installPath }
      ),
    getHistory: (hostId: number, limit: number = 10) =>
      apiClient.get<any[]>(`/deploy/hosts/${hostId}/history?limit=${limit}`),
    getLatest: (hostId: number) => apiClient.get<any>(`/deploy/hosts/${hostId}/latest`),
    batchDeploy: (hostIds: number[], installPath: string = '/opt/stability-test-agent') =>
      apiClient.post<{ deployments: any[]; total: number }>('/deploy/batch', { host_ids: hostIds, install_path: installPath }),
  },

  // 用户管理相关
  users: {
    list: (skip = 0, limit = 50) => apiClient.get<PaginatedResponse<User>>('/users', { params: { skip, limit } }),
    get: (id: number) => apiClient.get<User>(`/users/${id}`),
    create: (data: { username: string; password: string; role: string }) =>
      apiClient.post<User>('/users', data),
    update: (id: number, data: { username?: string; password?: string; role?: string; is_active?: string }) =>
      apiClient.put<User>(`/users/${id}`, data),
    delete: (id: number) => apiClient.delete<void>(`/users/${id}`),
    toggleActive: (id: number) => apiClient.post<User>(`/users/${id}/toggle-active`),
    changePassword: (data: { old_password: string; new_password: string }) =>
      apiClient.post('/users/change-password', data),
  },

  // 工具管理相关
  tools: {
    // 专项分类
    listCategories: (skip = 0, limit = 50) => apiClient.get<PaginatedResponse<ToolCategory>>('/tools/categories', { params: { skip, limit } }),
    getCategory: (id: number) => apiClient.get<ToolCategory>(`/tools/categories/${id}`),
    createCategory: (data: { name: string; description?: string; icon?: string; order?: number; enabled?: boolean }) =>
      apiClient.post<ToolCategory>('/tools/categories', data),
    updateCategory: (id: number, data: { name: string; description?: string; icon?: string; order?: number; enabled?: boolean }) =>
      apiClient.put<ToolCategory>(`/tools/categories/${id}`, data),
    deleteCategory: (id: number) => apiClient.delete<void>(`/tools/categories/${id}`),

    // 工具
    list: (categoryId?: number, skip = 0, limit = 50) => apiClient.get<PaginatedResponse<Tool>>('/tools', { params: { category_id: categoryId, skip, limit } }),
    get: (id: number) => apiClient.get<Tool>(`/tools/${id}`),
    create: (data: {
      category_id: number;
      name: string;
      description?: string;
      script_path: string;
      script_class?: string;
      script_type?: string;
      default_params?: Record<string, any>;
      param_schema?: Record<string, any>;
      timeout?: number;
      need_device?: boolean;
      enabled?: boolean;
    }) => apiClient.post<Tool>('/tools', data),
    update: (id: number, data: {
      category_id: number;
      name: string;
      description?: string;
      script_path: string;
      script_class?: string;
      script_type?: string;
      default_params?: Record<string, any>;
      param_schema?: Record<string, any>;
      timeout?: number;
      need_device?: boolean;
      enabled?: boolean;
    }) => apiClient.put<Tool>(`/tools/${id}`, data),
    delete: (id: number) => apiClient.delete<void>(`/tools/${id}`),

    // 扫描
    scan: () => apiClient.post<{ message: string; result: { categories: number; tools: number } }>('/tools/scan'),
    previewScan: () => apiClient.get<{ tools: any[]; count: number }>('/tools/scan/preview'),
  },

  // 结果汇总
  results: {
    summary: (limit?: number) =>
      apiClient.get<ResultsSummary>('/results/summary', { params: limit ? { limit } : {} }),
  },

  // 工作流管理
  workflows: {
    list: (skip = 0, limit = 50) => apiClient.get<PaginatedResponse<Workflow>>('/workflows', { params: { skip, limit } }),
    get: (id: number) => apiClient.get<Workflow>(`/workflows/${id}`),
    create: (data: WorkflowCreate) => apiClient.post<Workflow>('/workflows', data),
    start: (id: number) => apiClient.post<Workflow>(`/workflows/${id}/start`),
    cancel: (id: number) => apiClient.post<Workflow>(`/workflows/${id}/cancel`),
    delete: (id: number) => apiClient.delete<void>(`/workflows/${id}`),
    clone: (id: number) => apiClient.post<Workflow>(`/workflows/${id}/clone`),
    toggleTemplate: (id: number) => apiClient.post<Workflow>(`/workflows/${id}/toggle-template`),
  },

  // 统计数据
  stats: {
    activity: (hours: number = 24) =>
      apiClient.get<ActivityResponse>('/stats/activity', { params: { hours } }),
    deviceMetrics: (deviceId: number, hours: number = 24) =>
      apiClient.get<DeviceMetricsResponse>(`/stats/device/${deviceId}/metrics`, { params: { hours } }),
    completionTrend: (days: number = 7) =>
      apiClient.get<CompletionTrendResponse>('/stats/completion-trend', { params: { days } }),
  },

  // 通知管理
  notifications: {
    listChannels: (skip = 0, limit = 50) => apiClient.get<PaginatedResponse<NotificationChannel>>('/notifications/channels', { params: { skip, limit } }),
    createChannel: (data: { name: string; type: string; config: Record<string, any>; enabled?: boolean }) =>
      apiClient.post<NotificationChannel>('/notifications/channels', data),
    updateChannel: (id: number, data: Partial<{ name: string; type: string; config: Record<string, any>; enabled: boolean }>) =>
      apiClient.put<NotificationChannel>(`/notifications/channels/${id}`, data),
    deleteChannel: (id: number) => apiClient.delete<void>(`/notifications/channels/${id}`),
    testChannel: (id: number) => apiClient.post<{ ok: boolean; message: string }>(`/notifications/channels/${id}/test`),

    listRules: (skip = 0, limit = 50) => apiClient.get<PaginatedResponse<AlertRule>>('/notifications/rules', { params: { skip, limit } }),
    createRule: (data: { name: string; event_type: string; channel_id: number; filters?: Record<string, any>; enabled?: boolean }) =>
      apiClient.post<AlertRule>('/notifications/rules', data),
    updateRule: (id: number, data: Partial<{ name: string; event_type: string; channel_id: number; filters: Record<string, any>; enabled: boolean }>) =>
      apiClient.put<AlertRule>(`/notifications/rules/${id}`, data),
    deleteRule: (id: number) => apiClient.delete<void>(`/notifications/rules/${id}`),
  },

  // 定时任务
  schedules: {
    list: (skip = 0, limit = 50) => apiClient.get<PaginatedResponse<any>>('/schedules', { params: { skip, limit } }),
    get: (id: number) => apiClient.get<any>(`/schedules/${id}`),
    create: (data: any) => apiClient.post<any>('/schedules', data),
    update: (id: number, data: any) => apiClient.put<any>(`/schedules/${id}`, data),
    delete: (id: number) => apiClient.delete<void>(`/schedules/${id}`),
    toggle: (id: number) => apiClient.post<any>(`/schedules/${id}/toggle`),
    runNow: (id: number) => apiClient.post<{ message: string; task_id: number }>(`/schedules/${id}/run-now`),
  },

  // 任务模板
  templates: {
    list: (skip = 0, limit = 50) => apiClient.get<PaginatedResponse<any>>('/templates', { params: { skip, limit } }),
    get: (id: number) => apiClient.get<any>(`/templates/${id}`),
    create: (data: any) => apiClient.post<any>('/templates', data),
    update: (id: number, data: any) => apiClient.put<any>(`/templates/${id}`, data),
    delete: (id: number) => apiClient.delete<void>(`/templates/${id}`),
  },

  // 审计日志
  audit: {
    list: (
      skip = 0,
      limit = 50,
      filters?: {
        resource_type?: string;
        action?: string;
        user_id?: number;
        start_time?: string;
        end_time?: string;
      }
    ) => {
      const params: Record<string, any> = { skip, limit };
      if (filters) {
        Object.entries(filters).forEach(([k, v]) => {
          if (v !== '' && v !== undefined) params[k] = v;
        });
      }
      return apiClient.get<PaginatedResponse<any>>('/audit-logs', { params });
    },
  },

  // ─── 新编排层 API ──────────────────────────────────────────────────────────

  // WorkflowDefinition (蓝图) 管理
  orchestration: {
    list: (skip = 0, limit = 50) =>
      unwrapApiResponse<WorkflowDefinition[]>(
        apiClient.get('/workflows', { params: { skip, limit } })
      ),
    get: (id: number) =>
      unwrapApiResponse<WorkflowDefinition>(apiClient.get(`/workflows/${id}`)),
    create: (data: WorkflowDefinitionCreate) =>
      unwrapApiResponse<WorkflowDefinition>(apiClient.post('/workflows', data)),
    update: (id: number, data: Partial<WorkflowDefinitionCreate & {
      task_templates?: { name: string; pipeline_def: PipelineDef; sort_order?: number }[];
    }>) =>
      unwrapApiResponse<WorkflowDefinition>(apiClient.put(`/workflows/${id}`, data)),
    delete: (id: number) =>
      unwrapApiResponse<void>(apiClient.delete(`/workflows/${id}`)),
    run: (id: number, data: WorkflowRunCreate) =>
      unwrapApiResponse<WorkflowRun>(apiClient.post(`/workflows/${id}/run`, data)),
  },

  // WorkflowRun (执行记录) 查询
  execution: {
    listRuns: (skip = 0, limit = 50) =>
      unwrapApiResponse<WorkflowRun[]>(apiClient.get('/workflow-runs', { params: { skip, limit } })),
    getRun: (runId: number) =>
      unwrapApiResponse<WorkflowRun>(apiClient.get(`/workflow-runs/${runId}`)),
    getRunJobs: (runId: number) =>
      unwrapApiResponse<JobInstance[]>(apiClient.get(`/workflow-runs/${runId}/jobs`)),
  },

  // Tool Catalog (新工具目录，Phase 3 格式)
  toolCatalog: {
    list: (isActive?: boolean) =>
      unwrapApiResponse<ToolEntry[]>(
        apiClient.get('/tools', { params: isActive != null ? { is_active: isActive } : {} })
      ),
    get: (id: number) =>
      unwrapApiResponse<ToolEntry>(apiClient.get(`/tools/${id}`)),
    create: (data: Omit<ToolEntry, 'id' | 'created_at'>) =>
      unwrapApiResponse<ToolEntry>(apiClient.post('/tools', data)),
    update: (id: number, data: Partial<Omit<ToolEntry, 'id' | 'created_at'>>) =>
      unwrapApiResponse<ToolEntry>(apiClient.put(`/tools/${id}`, data)),
    remove: (id: number) =>
      unwrapApiResponse<void>(apiClient.delete(`/tools/${id}`)),
  },
};

export default apiClient;
