import apiClient, { unwrapApiResponse } from './client';
import type { Host, PaginatedResponse } from './types';

export const hosts = {
  list: (skip = 0, limit = 50) => apiClient.get<PaginatedResponse<Host>>('/hosts', { params: { skip, limit } }),
  get: (id: number | string) => apiClient.get<Host>(`/hosts/${id}`),
  /**
   * ADR-0021 hot-update gate — fetch the live `active_jobs` snapshot for a host.
   * Wraps `GET /hosts/{id}` and unwraps the ApiResponse envelope so callers
   * receive the typed `Host` directly (with `active_jobs` populated).
   */
  getDetail: (id: number | string) =>
    unwrapApiResponse<Host>(apiClient.get(`/hosts/${id}`)),
  create: (data: { name: string; ip: string; ssh_port?: number; ssh_user?: string }) =>
    apiClient.post<Host>('/hosts', data),
  update: (id: number, data: { name: string; ip: string; ssh_port?: number; ssh_user?: string }) =>
    apiClient.put<Host>(`/hosts/${id}`, data),
};

export const heartbeat = {
  send: (hostId: number, data: { status: string; mount_status?: Record<string, any> }) =>
    apiClient.post(`/heartbeat`, { host_id: hostId, ...data }),
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
    ),
};

export const deploy = {
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
};
