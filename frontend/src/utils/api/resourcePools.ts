import apiClient from './client';
import { unwrapApiResponse } from './client';
import type { ResourcePool, ResourcePoolLoad, ResourcePoolCreatePayload } from './types';

export const resourcePools = {
  list: (resourceType?: string) =>
    unwrapApiResponse<ResourcePool[]>(
      apiClient.get('/resource-pools', { params: resourceType ? { resource_type: resourceType } : {} }),
    ),
  /** #955: 普通用户可选池列表（active only，config 白名单剥密，无 password）。 */
  available: (resourceType?: string) =>
    unwrapApiResponse<ResourcePool[]>(
      apiClient.get('/resource-pools/available', {
        params: resourceType ? { resource_type: resourceType } : {},
      }),
    ),
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
