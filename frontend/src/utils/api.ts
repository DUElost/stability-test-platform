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
    console.log(`[API] ${config.method?.toUpperCase()} ${config.url}`);
    return config;
  },
  (error) => {
    console.error('[API] Request error:', error);
    return Promise.reject(error);
  }
);

// 响应拦截器
apiClient.interceptors.response.use(
  (response) => {
    console.log(`[API] Response:`, response.data);
    return response;
  },
  async (error) => {
    console.error('[API] Response error:', error);

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
  target_device_id: number | null;
  status: 'PENDING' | 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELED';
  priority: number;
  created_at: string;

  // 分布式任务支持
  group_id?: string;
  is_distributed?: boolean;
  runs_count?: number;
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

// API 函数
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
    list: () => apiClient.get<Host[]>('/hosts'),
    get: (id: number) => apiClient.get<Host>(`/hosts/${id}`),
    create: (data: { name: string; ip: string; ssh_port?: number; ssh_user?: string }) =>
      apiClient.post<Host>('/hosts', data),
  },

  // 设备相关
  devices: {
    list: () => apiClient.get<Device[]>('/devices'),
    get: (id: number) => apiClient.get<Device>(`/devices/${id}`),
    create: (data: { serial: string; model?: string; host_id?: number; tags?: string[] }) =>
      apiClient.post<Device>('/devices', data),
  },

  // 任务相关
  tasks: {
    list: () => apiClient.get<Task[]>('/tasks'),
    get: (id: number) => apiClient.get<Task>(`/tasks/${id}`),
    listTemplates: () => apiClient.get<TaskTemplate[]>('/task-templates'),
    create: (data: {
      name: string;
      type: string;
      template_id?: number;
      target_device_id?: number;
      device_serial?: string;
      params?: Record<string, any>;
      priority?: number;
    }) =>
      apiClient.post<Task>('/tasks', data),
    cancel: (id: number) => apiClient.post(`/tasks/${id}/cancel`),
    retry: (id: number) => apiClient.post(`/tasks/${id}/retry`),
    dispatch: (taskId: number, data: { host_id: number; device_id: number }) =>
      apiClient.post<TaskRun>(`/tasks/${taskId}/dispatch`, data),
    getRuns: (taskId: number) => apiClient.get<TaskRun[]>(`/tasks/${taskId}/runs`),
    getRunReport: (runId: number) => apiClient.get<RunReport>(`/runs/${runId}/report`),
    getRunReportExportUrl: (runId: number, format: 'markdown' | 'json' = 'markdown') =>
      `/api/v1/runs/${runId}/report/export?format=${format}`,
    createRunJiraDraft: (runId: number) => apiClient.post<JiraDraft>(`/runs/${runId}/jira-draft`),
    artifactDownloadUrl: (taskId: number, runId: number, artifactId: number) =>
      `/api/v1/tasks/${taskId}/runs/${runId}/artifacts/${artifactId}/download`,
    // 查询Agent日志
    queryAgentLogs: (data: { host_id: number; log_path?: string; lines?: number }) =>
      apiClient.post<AgentLogOut>('/agent/logs', data),
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
  },

  // 用户管理相关
  users: {
    list: () => apiClient.get<User[]>('/users'),
    get: (id: number) => apiClient.get<User>(`/users/${id}`),
    create: (data: { username: string; password: string; role: string }) =>
      apiClient.post<User>('/users', data),
    update: (id: number, data: { username?: string; password?: string; role?: string; is_active?: string }) =>
      apiClient.put<User>(`/users/${id}`, data),
    delete: (id: number) => apiClient.delete<void>(`/users/${id}`),
    toggleActive: (id: number) => apiClient.post<User>(`/users/${id}/toggle-active`),
  },

  // 工具管理相关
  tools: {
    // 专项分类
    listCategories: () => apiClient.get<ToolCategory[]>('/tools/categories'),
    getCategory: (id: number) => apiClient.get<ToolCategory>(`/tools/categories/${id}`),
    createCategory: (data: { name: string; description?: string; icon?: string; order?: number; enabled?: boolean }) =>
      apiClient.post<ToolCategory>('/tools/categories', data),
    updateCategory: (id: number, data: { name: string; description?: string; icon?: string; order?: number; enabled?: boolean }) =>
      apiClient.put<ToolCategory>(`/tools/categories/${id}`, data),
    deleteCategory: (id: number) => apiClient.delete<void>(`/tools/categories/${id}`),

    // 工具
    list: (categoryId?: number) => apiClient.get<Tool[]>('/tools', { params: { category_id: categoryId } }),
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
};

export default apiClient;
