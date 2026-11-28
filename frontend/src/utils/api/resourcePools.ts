import apiClient from './client';
import { unwrapApiResponse } from './client';
import type { ResourcePool, ResourcePoolLoad, ResourcePoolCreatePayload } from './types';

export interface AvailableResourcePoolFilter {
  resourceType?: string | null;
  requiredType?: string | null;
  requiredEgress?: string | null;
}

export const resourcePools = {
  list: (resourceType?: string) =>
    unwrapApiResponse<ResourcePool[]>(
      apiClient.get('/resource-pools', { params: resourceType ? { resource_type: resourceType } : {} }),
    ),
  /**
   * #955 / ADR-0066 D3.5：普通用户可选网络供给池列表（active only，config 白名单剥密，无密码）。
   * 支持传旧位置参数 ``resourceType`` 或交集过滤对象 ``{ requiredType, requiredEgress }``。
   */
  available: (filter?: string | AvailableResourcePoolFilter) => {
    const opts: AvailableResourcePoolFilter =
      typeof filter === 'string' ? { resourceType: filter } : (filter ?? {});
    const params: Record<string, string> = {};
    if (opts.resourceType) params.resource_type = opts.resourceType;
    if (opts.requiredType) params.required_type = opts.requiredType;
    if (opts.requiredEgress) params.required_egress = opts.requiredEgress;
    return unwrapApiResponse<ResourcePool[]>(
      apiClient.get('/resource-pools/available', { params }),
    );
  },
  listLoads: () =>
    unwrapApiResponse<ResourcePoolLoad[]>(apiClient.get('/resource-pools/loads')),
  get: (id: number) =>
    unwrapApiResponse<ResourcePool>(apiClient.get(`/resource-pools/${id}`)),
  create: (data: ResourcePoolCreatePayload) =>
    unwrapApiResponse<ResourcePool>(apiClient.post('/resource-pools', data)),
  update: (id: number, data: ResourcePoolCreatePayload) =>
    unwrapApiResponse<ResourcePool>(apiClient.put(`/resource-pools/${id}`, data)),
  delete: (id: number) =>
    apiClient.delete(`/resource-pools/${id}`).then(r => r.data),
};
