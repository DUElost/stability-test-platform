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
  list: (skip = 0, limit = 50) =>
    apiClient.get<PaginatedResponse<Host>>('/hosts', { params: { skip, limit } }).then(r => r.data),
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

export const heartbeat = {
  send: (hostId: number, data: { status: string; mount_status?: Record<string, any> }) =>
    apiClient.post('/heartbeat', { host_id: hostId, ...data }).then(r => r.data),
};

export interface HotUpdateResult {
  ok: boolean;
  host_id: number;
  message: string;
  duration_ms?: number;
  deps_refreshed?: boolean;
  code_version?: string;
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

export interface AgentInstallResult {
  ok: boolean;
  rc: number;
  log_path?: string | null;
  console_run_id?: string | null;
  message: string;
}

export interface AgentInstallTriggerResult {
  ok: boolean;
  host_id: string;
  saq_key: string;
  console_run_id: string;
  room: string;
  status: string;
  message: string;
}

export interface AgentInstallStatus {
  host_id: string;
  saq_key: string;
  status: string; // queued | active | complete | failed | aborted | unknown
  console_run_id?: string | null;
  console_status?: string | null;
  room?: string | null;
  log_path?: string | null;
  result?: AgentInstallResult | null;
}

export const agentInstall = {
  trigger: (hostId: number | string) =>
    apiClient
      .post<AgentInstallTriggerResult>(`/hosts/${hostId}/install`)
      .then(r => r.data),
  status: (hostId: number | string) =>
    apiClient.get<AgentInstallStatus>(`/hosts/${hostId}/install/status`).then(r => r.data),
};
