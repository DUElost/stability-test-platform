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
  ProjectUpdateInput,
} from './types';

/** ADR-0029 P2.5 — 人工项目登记簿 + Fleet 事实 + 型号映射。 */
export const projects = {
  list: () =>
    unwrapApiResponse(
      apiClient.get<ApiResponseEnvelope<ProjectSummary[]>>('/projects'),
    ),
  /** ADR-0029 P0：SEED 回填标签（待转正队列） */
  listSeed: () =>
    unwrapApiResponse(
      apiClient.get<ApiResponseEnvelope<ProjectSummary[]>>('/projects', {
        params: { source: 'seed' },
      }),
    ),
  /** ADR-0029 P0：SEED 标签就地转正为人工项目（admin）。 */
  promoteSeed: (projectKey: string) =>
    unwrapApiResponse(
      apiClient.post<ApiResponseEnvelope<ProjectSummary>>(
        `/projects/seed/${projectKey}/promote`,
      ),
    ),
  get: (projectKey: string) =>
    unwrapApiResponse(
      apiClient.get<ApiResponseEnvelope<ProjectDetail>>(`/projects/${projectKey}`),
    ),
  create: (payload: ProjectCreateInput) =>
    unwrapApiResponse(
      apiClient.post<ApiResponseEnvelope<ProjectSummary>>('/projects', payload),
    ),
  update: (projectKey: string, payload: ProjectUpdateInput) =>
    unwrapApiResponse(
      apiClient.put<ApiResponseEnvelope<ProjectSummary>>(
        `/projects/${projectKey}`,
        payload,
      ),
    ),
  archive: (projectKey: string) =>
    unwrapApiResponse(
      apiClient.post<ApiResponseEnvelope<ProjectSummary>>(
        `/projects/${projectKey}/archive`,
      ),
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
  /** ADR-0029 复盘：删除一条型号规则（admin）。 */
  /** ADR-0029 D2 复核：项目重命名（admin）。 */
  rename: (projectKey: string, newKey: string) =>
    unwrapApiResponse(
      apiClient.put<ApiResponseEnvelope<ProjectSummary>>(
        `/projects/${projectKey}/rename`,
        { new_key: newKey },
      ),
    ),
  /** 按活跃规则重算存量归属（显式纠正，不依赖心跳）。 */
  recomputeRules: (projectKey: string) =>
    unwrapApiResponse(
      apiClient.post<ApiResponseEnvelope<{ project_key: string; rules: number; devices_moved: number }>>(
        `/projects/${projectKey}/rules/recompute`,
      ),
    ),
  removeRule: (projectKey: string, model: string) =>
    unwrapApiResponse(
      apiClient.delete<ApiResponseEnvelope<{ project_key: string; model: string }>>(
        `/projects/${projectKey}/rules/${encodeURIComponent(model)}`,
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
