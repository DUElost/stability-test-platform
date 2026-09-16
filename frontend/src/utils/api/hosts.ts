import apiClient from './client';
import type { Host, PaginatedResponse } from './types';

export interface HostMutationInput {
  name: string;
  ip: string;
  ssh_port?: number;
  ssh_user?: string;
  ssh_password?: string | null;
  ssh_auth_type?: string;
  ssh_key_path?: string | null;
}

export const hosts = {
  list: (skip = 0, limit = 50, includeRetired = false) =>
    apiClient.get<PaginatedResponse<Host>>('/hosts', {
      // ADR-0038 D5：默认排除退役主机（后端同口径）；显式置 true 才显示
      params: { skip, limit, include_retired: includeRetired },
    }).then(r => r.data),
  /** ADR-0038 D2：退役（admin；reason 必填，审计 who/when/reason）。 */
  retire: (id: number | string, reason: string) =>
    apiClient.post<Host>(`/hosts/${id}/retire`, { retire_reason: reason }).then(r => r.data),
  /** ADR-0038 D2：解除退役（admin；reason 必填）。 */
  unretire: (id: number | string, reason: string) =>
    apiClient.post<Host>(`/hosts/${id}/unretire`, { retire_reason: reason }).then(r => r.data),
  get: (id: number | string) => apiClient.get<Host>(`/hosts/${id}`).then(r => r.data),
  getDetail: (id: number | string) =>
    apiClient.get<Host>(`/hosts/${id}`).then(r => r.data),
  create: (data: HostMutationInput) =>
    apiClient.post<Host>('/hosts', data).then(r => r.data),
  update: (id: number | string, data: HostMutationInput) =>
    apiClient.put<Host>(`/hosts/${id}`, data).then(r => r.data),
  delete: (id: number | string) =>
    apiClient.delete<{ ok: boolean; host_id: string; message: string }>(`/hosts/${id}`).then(r => r.data),
  updateWatcherAdminState: (
    id: number | string,
    data: { watcher_admin_active: boolean },
  ) => apiClient.patch<Host>(`/hosts/${id}/watcher-admin-state`, data).then(r => r.data),
};

/** Shared react-query fetcher — always returns Host[], never the paginated envelope. */
export const fetchHostList = (skip = 0, limit = 200, includeRetired = false) =>
  hosts.list(skip, limit, includeRetired).then((res) => res.items);

/** Normalize react-query cache to Host[] (tolerates legacy PaginatedResponse pollution). */
export function coerceHostList(data: unknown): Host[] {
  if (Array.isArray(data)) return data;
  if (
    data &&
    typeof data === 'object' &&
    Array.isArray((data as PaginatedResponse<Host>).items)
  ) {
    return (data as PaginatedResponse<Host>).items;
  }
  return [];
}

export const heartbeat = {
  send: (hostId: number, data: { status: string; mount_status?: Record<string, unknown> }) =>
    apiClient.post('/heartbeat', { host_id: hostId, ...data }).then(r => r.data),
};

export interface HotUpdateResult {
  ok: boolean;
  host_id: number;
  message: string;
  duration_ms?: number;
  deps_refreshed?: boolean;
  code_version?: string;
  // ADR-0040 D3/D5 (#1907): digest no-op gate — converged means nothing was
  // deployed (desired == current); reason explains the outcome.
  converged?: boolean;
  reason?: string;
  artifact_digest?: string;
  // Present when the request was issued with abort_running_jobs=true.
  aborted?: {
    plan_runs?: number[];
    aborted_jobs?: number[];
    drained_lingering_jobs?: number[];
  };
}

/** v3: 409 response detail shape for hot-update gate errors. */
export interface HotUpdateConflictDetail {
  code: 'HOST_HAS_ACTIVE_JOBS' | 'HOST_ABORT_PENDING';
  message: string;
  active_jobs?: Array<{
    id: number;
    plan_run_id?: number | null;
    plan_id?: number | null;
    device_id: number;
    status: string;
    abort_pending?: boolean;
  }>;
  retry_after_seconds?: number;  // present when code=HOST_ABORT_PENDING
}

export const hotUpdate = {
  /**
   * Trigger a hot-update.  When `abortRunningJobs=true`, the backend will
   * abort any active Jobs on the host first (release leases, wait ≤45s for
   * the Agent to drain), then run the hot-update.
   *
   * Without that flag and with active Jobs present, the backend returns 409
   * with `detail.active_jobs` populated — the caller should pop the confirm
   * dialog and ask the user to opt into the abort path.
   */
  trigger: (
    hostId: number | string,
    opts: { abortRunningJobs?: boolean } = {},
  ) =>
    apiClient.post<HotUpdateResult>(
      `/hosts/${hostId}/hot-update`,
      undefined,
      opts.abortRunningJobs
        ? { params: { abort_running_jobs: true } }
        : undefined,
    ).then(r => r.data),
};

// ADR-0044：安装由 RunConsole 自持——不再有 SAQ 作业面（saq_key/作业状态已移除）。
export interface AgentInstallTriggerResult {
  ok: boolean;
  host_id: string;
  console_run_id: string;
  room: string;
  status: string;
  log_path?: string | null;
  message: string;
}

export interface AgentInstallStatus {
  host_id: string;
  /** console 派生摘要：idle | running | succeeded | failed | canceled | lost */
  status: string;
  console_run_id?: string | null;
  /** console 终态：RUNNING | SUCCESS | FAILED | CANCELED（lost/idle 时为 null） */
  console_status?: string | null;
  /** false = 无活动运行；结果可能来自 DB 回放（见 last_install）或已丢失 */
  console_found: boolean;
  exit_code?: number | null;
  room?: string | null;
  log_path?: string | null;
}

export interface AgentInstallCancelResult {
  ok: boolean;
  host_id: string;
  console_run_id: string;
  canceled: boolean;
  status: string;
  message: string;
}

export const agentInstall = {
  trigger: (hostId: number | string) =>
    apiClient
      .post<AgentInstallTriggerResult>(`/hosts/${hostId}/install`)
      .then(r => r.data),
  status: (hostId: number | string) =>
    apiClient.get<AgentInstallStatus>(`/hosts/${hostId}/install/status`).then(r => r.data),
  // #2255：卡住时取消在跑的安装（此前只能重启控制面收尾）
  cancel: (hostId: number | string) =>
    apiClient.post<AgentInstallCancelResult>(`/hosts/${hostId}/install/cancel`).then(r => r.data),
};
