import apiClient, { unwrapApiResponse } from './client';
import { fetchAllPages, type PagedResult } from './paginate';
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

/**
 * 单次请求的页大小。**这是性能选择，不是正确性约束**——`limit` 是服务端单次响应
 * 护栏（`backend/api/routes/devices.py` 的 `le=`），设备数越过它时单次请求会被静默
 * 截断；拿全量靠 `fetchAllPages` 翻页，改这个值只影响请求次数。取值只需 ≤ 后端 `le`。
 */
const DEVICE_PAGE_LIMIT = 1200;

/** GET /devices 的筛选维度（与 `devices.list` 的查询参数一一对应）。 */
export interface DeviceListFilters {
  status?: string;
  projectKey?: string;
  unassigned?: boolean;
}

/**
 * 设备全量（按服务端 `total` 翻页）——`limit` 是单次响应护栏、不是 fleet 总量
 * （按 25 台/主机，48 host 满挂 = 1200 已压满上限；ADR-0026 目标是 60+ host /
 * 1000+ device，见 #3131），所以卡内/选择器一律走本函数，不能单次请求当全量。
 *
 * 通用判据（为什么要翻页、为什么依赖服务端全序）见 `fetchAllPages`。
 */
export async function fetchAllDevicePages(
  filters: DeviceListFilters = {},
): Promise<PagedResult<Device>> {
  return fetchAllPages(
    (skip, limit) =>
      devices.list(
        skip,
        limit,
        filters.status,
        undefined,
        filters.projectKey,
        filters.unassigned ?? false,
      ),
    DEVICE_PAGE_LIMIT,
  );
}

/** 只要设备数组的便捷形态（PlanExecutePage / 排程设备选择器用）。 */
export async function fetchAllDevices(status?: string): Promise<Device[]> {
  return (await fetchAllDevicePages({ status })).items;
}
