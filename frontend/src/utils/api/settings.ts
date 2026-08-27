import apiClient, { unwrapApiResponse } from './client';
import type { ApiResponseEnvelope, SystemSettings } from './types';

/** 系统设置概览（只读聚合，对应"系统设置"页展示的运行时配置）。 */
export const settings = {
  get: () =>
    unwrapApiResponse(
      apiClient.get<ApiResponseEnvelope<SystemSettings>>('/api/v1/settings'),
    ),
};
