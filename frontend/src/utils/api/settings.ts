import apiClient, { unwrapApiResponse } from './client';
import type { ApiResponseEnvelope, SystemSettings } from './types';

/** 系统设置概览（只读聚合，对应"系统设置"页展示的运行时配置）。
 *  路径为相对路径——apiClient baseURL 已含 /api/v1（400281f 曾误写全路径致 404）。 */
export const settings = {
  get: () =>
    unwrapApiResponse(
      apiClient.get<ApiResponseEnvelope<SystemSettings>>('/settings'),
    ),
};
