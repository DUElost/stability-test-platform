import apiClient from './client';
import type { Device, PaginatedResponse } from './types';

export const devices = {
  list: (skip = 0, limit = 50, status?: string, tags?: string) =>
    apiClient.get<PaginatedResponse<Device>>('/devices', {
      params: { skip, limit, ...(status ? { status } : {}), ...(tags ? { tags } : {}) },
    }).then(r => r.data),
  get: (id: number) => apiClient.get<Device>(`/devices/${id}`).then(r => r.data),
  create: (data: { serial: string; model?: string; host_id?: number; tags?: string[] }) =>
    apiClient.post<Device>('/devices', data).then(r => r.data),
  updateTags: (id: number, tags: string[]) =>
    apiClient.put<Device>(`/devices/${id}/tags`, tags).then(r => r.data),
};
