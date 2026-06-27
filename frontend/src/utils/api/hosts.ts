import apiClient from './client';
import type { Host, PaginatedResponse } from './types';

export const hosts = {
  list: (skip = 0, limit = 50) =>
    apiClient.get<PaginatedResponse<Host>>('/hosts', { params: { skip, limit } }).then(r => r.data),
  get: (id: number | string) => apiClient.get<Host>(`/hosts/${id}`).then(r => r.data),
  getDetail: (id: number | string) =>
    apiClient.get<Host>(`/hosts/${id}`).then(r => r.data),
  create: (data: { name: string; ip: string; ssh_port?: number; ssh_user?: string }) =>
    apiClient.post<Host>('/hosts', data).then(r => r.data),
  update: (id: number | string, data: { name: string; ip: string; ssh_port?: number; ssh_user?: string }) =>
    apiClient.put<Host>(`/hosts/${id}`, data).then(r => r.data),
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

export const deploy = {
  trigger: (hostId: number | string, installPath: string = '/opt/stability-test-agent') =>
    apiClient.post<{ id: number; host_id: number; status: string; started_at: string }>(
      `/deploy/hosts/${hostId}`,
      { install_path: installPath },
    ).then(r => r.data),
  getHistory: (hostId: number | string, limit: number = 10) =>
    apiClient.get<any[]>(`/deploy/hosts/${hostId}/history?limit=${limit}`).then(r => r.data),
  getLatest: (hostId: number | string) =>
    apiClient.get<any>(`/deploy/hosts/${hostId}/latest`).then(r => r.data),
  batchDeploy: (hostIds: Array<number | string>, installPath: string = '/opt/stability-test-agent') =>
    apiClient.post<{ deployments: any[]; total: number }>('/deploy/batch', { host_ids: hostIds, install_path: installPath }).then(r => r.data),
};
