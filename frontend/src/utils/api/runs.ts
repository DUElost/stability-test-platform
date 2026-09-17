import apiClient, { unwrapApiResponse } from './client';
import type { JiraDraft, JiraDraftListItem, RunReport } from './types';

/**
 * Job-level run report (path param is Job ID, not PlanRun ID).
 *
 * #2420：URL 里的这个 id 一直是 `JobInstance.id`，端点本身却不校验「这个 job 属于
 * 哪个 run」。传 `planRunId` 即要求配对（从 PlanRun 详情进来时一定传），后端不匹配
 * 就 404 `job_not_in_plan_run`；不传保持旧行为，脚本与历史深链不受影响。
 */
export const runs = {
  getCachedReport: (jobId: number, opts?: { planRunId?: number }) =>
    unwrapApiResponse<RunReport>(apiClient.get(`/runs/${jobId}/report/cached`, {
      params: opts?.planRunId != null ? { plan_run_id: opts.planRunId } : undefined,
    })),

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
