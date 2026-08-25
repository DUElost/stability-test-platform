import apiClient, { unwrapApiResponse } from './client';
import type { ApiResponseEnvelope, TestSuiteSummary } from './types';

/** ADR-0030 — 套件管理面（Plan 绑定用 name 引用）。 */
export const suites = {
  list: (params?: { project_key?: string; is_active?: boolean; q?: string }) =>
    unwrapApiResponse(
      apiClient.get<ApiResponseEnvelope<TestSuiteSummary[]>>('/test-suites', {
        params,
      }),
    ),
};
