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

/**
 * 单次请求的页大小。**这是性能选择，不是正确性约束**——`limit` 是服务端单次响应
 * 护栏（`backend/api/routes/devices.py` 的 `le=`），设备数越过它时单次请求会被静默
 * 截断；拿全量靠下面的翻页，改这个值只影响请求次数。取值只需 ≤ 后端 `le`。
 */
const DEVICE_PAGE_LIMIT = 1200;

/** GET /devices 的筛选维度（与 `devices.list` 的查询参数一一对应）。 */
export interface DeviceListFilters {
  status?: string;
  projectKey?: string;
  unassigned?: boolean;
}

/**
 * 翻页拉全量，并**连同 `total` 一起返回**——`total` 是判「有没有拿全」的唯一依据。
 *
 * `limit` 是**单次响应护栏**，不是 fleet 总量：48 host × 25 台 = 1200 恰等于上限，
 * 而 ADR-0026 的规模目标是 60+ host / 1000+ device（= 1500 台），上限已经落在承诺
 * 包线之内（#3131）。所以任何「请求一次拿全部」的写法都会在扩容后静默少设备——
 * 调用方必须用本函数（或 `fetchAllDevices`），并把 `items.length < total` 显式提示
 * 出来，**不能拿 `items.length` 当设备总数**。
 *
 * `skip` 递增的分页只在服务端排序是**跨请求不变的全序**时才不重不漏：排序列若含
 * `last_seen` 这类被心跳持续重写的遥测列，翻页会重复/丢设备（#3123 实测：页大小
 * 200 + 页间 150ms，5 轮中 3 轮各丢 14-20 台）。服务端已改为 `(host_id, id)`
 * （`devices.list_devices`），若将来再调默认排序，必须保持该全序性质。
 */
export async function fetchAllDevicePages(
  filters: DeviceListFilters = {},
): Promise<{ items: Device[]; total: number }> {
  const fetchPage = (skip: number) =>
    devices.list(
      skip,
      DEVICE_PAGE_LIMIT,
      filters.status,
      undefined,
      filters.projectKey,
      filters.unassigned ?? false,
    );

  let page = await fetchPage(0);
  const items = [...page.items];
  // 两个出口都要有：拿齐了（items.length >= total）正常退出；服务端 total 与实际
  // 不符时（并发增删）靠空页退出——否则死循环。
  while (page.items.length > 0 && items.length < page.total) {
    page = await fetchPage(items.length);
    items.push(...page.items);
  }
  return { items, total: page.total };
}

/** 只要设备数组的便捷形态（PlanExecutePage / 排程设备选择器用）。 */
export async function fetchAllDevices(status?: string): Promise<Device[]> {
  return (await fetchAllDevicePages({ status })).items;
}
