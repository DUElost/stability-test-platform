import apiClient from './client';
import type { Host, PaginatedResponse } from './types';

export const hosts = {
  list: (skip = 0, limit = 50) => apiClient.get<PaginatedResponse<Host>>('/hosts', { params: { skip, limit } }),
  get: (id: number) => apiClient.get<Host>(`/hosts/${id}`),
  create: (data: { name: string; ip: string; ssh_port?: number; ssh_user?: string }) =>
    apiClient.post<Host>('/hosts', data),
  update: (id: number, data: { name: string; ip: string; ssh_port?: number; ssh_user?: string }) =>
    apiClient.put<Host>(`/hosts/${id}`, data),
};

export const heartbeat = {
  send: (hostId: number, data: { status: string; mount_status?: Record<string, any> }) =>
    apiClient.post(`/heartbeat`, { host_id: hostId, ...data }),
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
