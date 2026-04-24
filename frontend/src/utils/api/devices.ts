import apiClient from './client';
import type { Device, PaginatedResponse } from './types';

export const devices = {
  list: (skip = 0, limit = 50, status?: string, tags?: string) =>
    apiClient.get<PaginatedResponse<Device>>('/devices', {
      params: { skip, limit, ...(status ? { status } : {}), ...(tags ? { tags } : {}) },
    }),
  get: (id: number) => apiClient.get<Device>(`/devices/${id}`),
  create: (data: { serial: string; model?: string; host_id?: number; tags?: string[] }) =>
    apiClient.post<Device>('/devices', data),
  updateTags: (id: number, tags: string[]) =>
    apiClient.put<Device>(`/devices/${id}/tags`, tags),
};
