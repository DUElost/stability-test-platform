import apiClient, { unwrapApiResponse } from './client';
import type {
  ApiResponseEnvelope,
  Device,
  InventoryModel,
  InventorySummary,
  ProjectCreateInput,
  ProjectDetail,
  ProjectMapPreview,
  ProjectModelCoverage,
  ProjectSummary,
} from './types';

/** ADR-0029 P2.5 — 人工项目登记簿 + Fleet 事实 + 型号映射。 */
export const projects = {
  list: () =>
    unwrapApiResponse(
      apiClient.get<ApiResponseEnvelope<ProjectSummary[]>>('/projects'),
    ),
  get: (projectKey: string) =>
    unwrapApiResponse(
      apiClient.get<ApiResponseEnvelope<ProjectDetail>>(`/projects/${projectKey}`),
    ),
  create: (payload: ProjectCreateInput) =>
    unwrapApiResponse(
      apiClient.post<ApiResponseEnvelope<ProjectSummary>>('/projects', payload),
    ),
  inventoryModels: () =>
    unwrapApiResponse(
      apiClient.get<ApiResponseEnvelope<InventoryModel[]>>('/projects/inventory/models'),
    ),
  inventorySummary: () =>
    unwrapApiResponse(
      apiClient.get<ApiResponseEnvelope<InventorySummary>>('/projects/inventory/summary'),
    ),
  modelsOf: (projectKey: string) =>
    unwrapApiResponse(
      apiClient.get<ApiResponseEnvelope<ProjectModelCoverage[]>>(
        `/projects/${projectKey}/models`,
      ),
    ),
  mapPreview: (projectKey: string, models: string[], reassignConflicts = false) =>
    unwrapApiResponse(
      apiClient.post<ApiResponseEnvelope<ProjectMapPreview>>(
        `/projects/${projectKey}/map/preview`,
        { models, reassign_conflicts: reassignConflicts },
      ),
    ),
  mapApply: (projectKey: string, models: string[], reassignConflicts = false) =>
    unwrapApiResponse(
      apiClient.post<ApiResponseEnvelope<ProjectMapPreview>>(
        `/projects/${projectKey}/map/apply`,
        { models, reassign_conflicts: reassignConflicts },
      ),
    ),
};

/** 设备批量归入项目（admin 动作）。返回更新后的设备列表。 */
export async function assignDevicesToProject(
  projectKey: string,
  deviceIds: number[],
): Promise<Device[]> {
  return unwrapApiResponse(
    apiClient.post<ApiResponseEnvelope<Device[]>>('/devices/bulk-project', {
      project_key: projectKey,
      device_ids: deviceIds,
    }),
  );
}
