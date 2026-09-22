import apiClient, { unwrapApiResponse } from './client';
import type { ApiResponseEnvelope, BulkSwipeTrailResult, Device, PaginatedResponse } from './types';

export const devices = {
  list: (skip = 0, limit = 50, status?: string, tags?: string, projectKey?: string, unassigned = false) =>
    apiClient.get<PaginatedResponse<Device>>('/devices', {
      params: {
        skip,
        limit,
        ...(status ? { status } : {}),
        ...(tags ? { tags } : {}),
        ...(projectKey ? { project_key: projectKey } : {}),
        ...(unassigned ? { unassigned: true } : {}),
      },
    }).then(r => r.data),
  get: (id: number) => apiClient.get<Device>(`/devices/${id}`).then(r => r.data),
  create: (data: { serial: string; model?: string; host_id?: string; tags?: string[] }) =>
    apiClient.post<Device>('/devices', data).then(r => r.data),
  updateTags: (id: number, tags: string[]) =>
    apiClient.put<Device>(`/devices/${id}/tags`, tags).then(r => r.data),
  bulkSwipeTrail: (deviceIds: number[], enabled: boolean) =>
    unwrapApiResponse(
      apiClient.post<ApiResponseEnvelope<BulkSwipeTrailResult>>('/devices/bulk-swipe-trail', {
        device_ids: deviceIds,
        enabled,
      }),
    ),
};

/** 后端 GET /devices 的 limit 上限（backend/api/routes/devices.py le=1200）。 */
const DEVICE_PAGE_LIMIT = 1200;

/**
 * total 感知拉全设备 — 目标规模 1000 台时通常仅 1 次请求。
 *
 * `skip` 递增的分页只在服务端排序是**跨请求不变的全序**时才不重不漏：排序列若含
 * `last_seen` 这类被心跳持续重写的遥测列，翻页会重复/丢设备（#3123 实测：页大小
 * 200 + 页间 150ms，5 轮中 3 轮各丢 14-20 台）。服务端已改为 `(host_id, id)`
 * （`devices.list_devices`），若将来再调默认排序，必须保持该全序性质。
 */
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
