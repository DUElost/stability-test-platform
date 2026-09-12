import apiClient, { unwrapApiResponse } from './client';
import type { JiraDraft, JiraDraftListItem, RunReport } from './types';

/** Job-level run report (path param is Job ID, not PlanRun ID). */
export const runs = {
  getCachedReport: (jobId: number) =>
    unwrapApiResponse<RunReport>(apiClient.get(`/runs/${jobId}/report/cached`)),

  getCachedJiraDraft: (jobId: number) =>
    unwrapApiResponse<JiraDraft>(apiClient.get(`/runs/${jobId}/jira-draft/cached`)),

  /**
   * #1532：一次取回最近带缓存草稿的 Job（含其 PlanRun 归属）。
   * 不用「PlanRun 列表 → 逐 Run listJobs → 逐 Job 取草稿」的自算扇出：无草稿的
   * Run 上内层短路不触发，会退化成 1 + N + Σ(该 Run 全部 Job) 次串行 404。
   */
  listRecentJiraDrafts: (limit = 50) =>
    unwrapApiResponse<JiraDraftListItem[]>(
      apiClient.get(`/runs/jira-drafts`, { params: { limit } }),
    ),
};
