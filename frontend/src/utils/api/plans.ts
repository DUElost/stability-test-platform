import apiClient from './client';
import { unwrapApiResponse } from './client';
import type { Plan, PlanChainTailCreate, PlanCreate, PlanUpdate, PlanRun, PlanRunCreate, PlanRunPreview, Specialty } from './types';

export const plans = {
  /** ADR-0029 D6（#405）：专项字典，Plan 编辑器下拉数据源。 */
  listSpecialties: () =>
    unwrapApiResponse<Specialty[]>(apiClient.get('/specialties')),

  list: (skip = 0, limit = 50, projectKey?: string) =>
    unwrapApiResponse<Plan[]>(apiClient.get('/plans', {
      params: { skip, limit, ...(projectKey ? { project_key: projectKey } : {}) },
    })),

  get: (id: number) =>
    unwrapApiResponse<Plan>(apiClient.get(`/plans/${id}`)),

  create: (data: PlanCreate) =>
    unwrapApiResponse<Plan>(apiClient.post('/plans', data)),

  update: (id: number, data: PlanUpdate) =>
    unwrapApiResponse<Plan>(apiClient.put(`/plans/${id}`, data)),

  /**
   * 原子链尾追加（#281 P1）：单事务内锁定链尾、校验版本、创建新 Plan、
   * 更新 next_plan_id；版本冲突整体 409 回滚，不产生孤立 Plan。
   */
  appendChainTail: (id: number, data: PlanChainTailCreate) =>
    unwrapApiResponse<Plan>(apiClient.post(`/plans/${id}/append-chain-tail`, data)),

  delete: (id: number, expectedUpdatedAt?: string | null) =>
    unwrapApiResponse<{ deleted: number }>(
      apiClient.delete(`/plans/${id}`, {
        params: expectedUpdatedAt ? { expected_updated_at: expectedUpdatedAt } : undefined,
      }),
    ),

  previewRun: (id: number, data: PlanRunCreate) =>
    unwrapApiResponse<PlanRunPreview>(apiClient.post(`/plans/${id}/run/preview`, data)),

  run: (id: number, data: PlanRunCreate) =>
    unwrapApiResponse<PlanRun>(apiClient.post(`/plans/${id}/run`, data)),
};
