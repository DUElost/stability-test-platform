import apiClient, { unwrapApiResponse } from './client';
import type {
  ApiResponseEnvelope,
  Device,
  ProjectDetail,
  ProjectSummary,
} from './types';

/** ADR-0029 P2 — 项目登记簿 API。对外一律 project_key（F2 口径）。 */
export const projects = {
  list: () =>
    unwrapApiResponse(
      apiClient.get<ApiResponseEnvelope<ProjectSummary[]>>('/projects'),
    ),
  get: (projectKey: string) =>
    unwrapApiResponse(
      apiClient.get<ApiResponseEnvelope<ProjectDetail>>(`/projects/${projectKey}`),
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
