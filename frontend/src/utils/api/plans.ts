import apiClient from './client';
import { unwrapApiResponse } from './client';
import { fetchAllPages, type PagedResult } from './paginate';
import type { Plan, PlanChainTailCreate, PlanCreate, PlanUpdate, PlanRunCreate, PlanRunPreview, PlanRunTriggerResult, PaginatedResponse, Specialty } from './types';

export const plans = {
  /** ADR-0029 D6（#405）：专项字典，Plan 编辑器下拉数据源。 */
  listSpecialties: () =>
    unwrapApiResponse<Specialty[]>(apiClient.get('/specialties')),

  /**
   * 计划列表（#3147：`{items, total, skip, limit}` — 与 devices/hosts 同形）。
   *
   * 此前返回裸数组且**没有 `total`**，计划侧因此是仓内唯一"截断不可检测"的列表；
   * 要"全部计划"的消费方必须走 `fetchAllPlanPages`，不能靠把 `limit` 调大。
   */
  list: (skip = 0, limit = 50, projectKey?: string, specialtyKey?: string) =>
    apiClient.get<PaginatedResponse<Plan>>('/plans', {
      params: {
        skip,
        limit,
        ...(projectKey ? { project_key: projectKey } : {}),
        ...(specialtyKey ? { specialty_key: specialtyKey } : {}),
      },
    }).then(r => r.data),

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
    unwrapApiResponse<PlanRunTriggerResult>(apiClient.post(`/plans/${id}/run`, data)),
};

/**
 * 单次请求的页大小（后端 `le=_PLAN_LIST_MAX_LIMIT` = 200，取同值）。
 * 这是性能选择、不是正确性约束——拿全量靠翻页，改它只影响请求次数。
 */
const PLAN_PAGE_LIMIT = 200;

/**
 * 计划全量（按服务端 `total` 翻页）。
 *
 * 选择器与校验需要**完整**计划集：单次请求一旦被 `le` 截断就是静默少选项/少校验对象，
 * 而此前 `/plans` 连 `total` 都没有，连"没拿全"都判不出来（#3147）。
 */
export async function fetchAllPlanPages(): Promise<PagedResult<Plan>> {
  return fetchAllPages((skip, limit) => plans.list(skip, limit), PLAN_PAGE_LIMIT);
}

/** 只要计划数组的便捷形态（两个计划选择器 / Plan 编辑器校验用）。 */
export async function fetchAllPlans(): Promise<Plan[]> {
  return (await fetchAllPlanPages()).items;
}
