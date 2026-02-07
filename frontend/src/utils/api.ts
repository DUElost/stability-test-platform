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
  (error) => {
    console.error('[API] Response error:', error);
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
}

export interface TaskRun {
  id: number;
  task_id: number;
  host_id: number;
  device_id: number;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  error_code: string | null;
  error_message: string | null;
  log_summary: string | null;
}

export interface AgentLogOut {
  host_id: number;
  log_path: string;
  content: string;
  lines_read: number;
  error?: string;
}

// API 函数
export const api = {
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
    create: (data: { name: string; type: string; params?: Record<string, any>; priority?: number }) =>
      apiClient.post<Task>('/tasks', data),
    dispatch: (taskId: number, data: { host_id: number; device_id: number }) =>
      apiClient.post<TaskRun>(`/tasks/${taskId}/dispatch`, data),
    getRuns: (taskId: number) => apiClient.get<TaskRun[]>(`/tasks/${taskId}/runs`),
    // 查询Agent日志
    queryAgentLogs: (data: { host_id: number; log_path?: string; lines?: number }) =>
      apiClient.post<AgentLogOut>('/agent/logs', data),
  },

  // 心跳相关
  heartbeat: {
    send: (hostId: number, data: { status: string; mount_status?: Record<string, any> }) =>
      apiClient.post(`/heartbeat`, { host_id: hostId, ...data }),
  },
};

export default apiClient;
