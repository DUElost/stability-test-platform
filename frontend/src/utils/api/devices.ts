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

/** 后端 GET /devices 的 limit 上限（backend/api/routes/devices.py le=1200）。 */
const DEVICE_PAGE_LIMIT = 1200;

/** total 感知拉全设备 — 目标规模 1000 台时通常仅 1 次请求。 */
export async function fetchAllDevices(status?: string): Promise<Device[]> {
  const all: Device[] = [];
  let total = Infinity;
  while (all.length < total) {
    const page = await devices.list(all.length, DEVICE_PAGE_LIMIT, status);
    all.push(...page.items);
    total = page.total;
    if (page.items.length === 0) break;
  }
  return all;
}
