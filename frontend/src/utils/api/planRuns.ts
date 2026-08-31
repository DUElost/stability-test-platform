import apiClient from './client';
import { unwrapApiResponse } from './client';
import type {
  PlanRun,
  PlanRunStatus,
  PlanRunListPage,
  PlanJobInstance,
  PlanRunSummary,
  JobArtifactEntry,
  PlanChain,
  PlanRunTimeline,
  PlanRunEventsPayload,
  PlanRunDevicesPayload,
  WatcherSummary,
  WatcherTimeScope,
  PlanRunLogEventsPayload,
  TestCaseResultsPayload,
  JobManualActionResult,
  PlanRunAbortResult,
  PlanRunDispatchRetryResult,
  EventStage,
  EventSeverity,
  DeviceUiStatus,
  DeviceLinkStatus,
  CrashDetailEntry,
} from './types';

export interface ListPlanRunEventsParams {
  stage?: EventStage | 'all';
  severity?: EventSeverity | 'all';
  limit?: number;
  offset?: number;
}

export interface ListPlanRunDevicesParams {
  /** 执行维度 — 过滤 `job_exec_status`。 */
  status?: DeviceUiStatus | 'all';
  /** 连接维度 — 过滤 `device_link_status`。与 `status` 正交，可叠加。 */
  link_status?: DeviceLinkStatus | 'all';
  host_id?: string | 'all';
}

export interface ListPlanRunsParams {
  skip?: number;
  limit?: number;
  planId?: number;
  /** 单值或多值（排队中 = QUEUED+PRECHECK） */
  status?: PlanRunStatus | PlanRunStatus[];
  projectKey?: string;
  /** Run ID / Plan 名 / 触发者 */
  q?: string;
}

function buildPlanRunListParams(params: ListPlanRunsParams): URLSearchParams {
  const sp = new URLSearchParams();
  sp.set('skip', String(params.skip ?? 0));
  sp.set('limit', String(params.limit ?? 50));
  if (params.planId != null) sp.set('plan_id', String(params.planId));
  if (params.projectKey) sp.set('project_key', params.projectKey);
  if (params.q?.trim()) sp.set('q', params.q.trim());
  const statuses = params.status == null
    ? []
    : Array.isArray(params.status)
      ? params.status
      : [params.status];
  for (const s of statuses) {
    sp.append('status', s);
  }
  return sp;
}

export const planRuns = {
  /** 分页列表（含 total / stats）。 */
  listPage: (params: ListPlanRunsParams = {}) =>
    unwrapApiResponse<PlanRunListPage>(
      apiClient.get(`/plan-runs?${buildPlanRunListParams(params).toString()}`),
    ),

  /**
   * 兼容旧调用：返回当前页 items。
   * 新列表页请用 `listPage` 拿 total/stats。
   */
  list: async (
    skip = 0,
    limit = 50,
    planId?: number,
    status?: PlanRunStatus | PlanRunStatus[],
    projectKey?: string,
    q?: string,
  ) => {
    const page = await planRuns.listPage({ skip, limit, planId, status, projectKey, q });
    return page.items;
  },

  get: (id: number) =>
    unwrapApiResponse<PlanRun>(apiClient.get(`/plan-runs/${id}`)),

  listJobs: (runId: number) =>
    unwrapApiResponse<PlanJobInstance[]>(apiClient.get(`/plan-runs/${runId}/jobs`)),

  getSummary: (runId: number) =>
    unwrapApiResponse<PlanRunSummary>(apiClient.get(`/plan-runs/${runId}/summary`)),

  exportReport: async (runId: number, format: 'markdown' | 'json' = 'markdown') => {
    const response = await apiClient.get(`/plan-runs/${runId}/report/export`, {
      params: { format },
      responseType: 'blob',
    });
    return response.data as Blob;
  },

  // ── ADR-0021/0022 C5a₂ aggregation endpoints ──
  getChain: (runId: number) =>
    unwrapApiResponse<PlanChain>(apiClient.get(`/plan-runs/${runId}/chain`)),

  getTimeline: (runId: number) =>
    unwrapApiResponse<PlanRunTimeline>(apiClient.get(`/plan-runs/${runId}/timeline`)),

  getEvents: (runId: number, params: ListPlanRunEventsParams = {}) =>
    unwrapApiResponse<PlanRunEventsPayload>(
      apiClient.get(`/plan-runs/${runId}/events`, { params: cleanParams(params) }),
    ),

  getDevices: (runId: number, params: ListPlanRunDevicesParams = {}) =>
    unwrapApiResponse<PlanRunDevicesPayload>(
      apiClient.get(`/plan-runs/${runId}/devices`, { params: cleanParams(params) }),
    ),

  getWatcherSummary: (runId: number, timeScope: WatcherTimeScope = 'all') =>
    unwrapApiResponse<WatcherSummary>(
      apiClient.get(`/plan-runs/${runId}/watcher-summary`, {
        params: { time_scope: timeScope },
      }),
    ),

  getLogEvents: (
    runId: number,
    params: { skip?: number; limit?: number; state?: string } = {},
  ) =>
    unwrapApiResponse<PlanRunLogEventsPayload>(
      apiClient.get(`/plan-runs/${runId}/log-events`, { params: cleanParams(params) }),
    ),

  getTestCaseResults: (
    runId: number,
    params: { skip?: number; limit?: number; status?: string } = {},
  ) =>
    unwrapApiResponse<TestCaseResultsPayload>(
      apiClient.get(`/plan-runs/${runId}/test-case-results`, { params: cleanParams(params) }),
    ),

  // ── ADR-0021 D7 abort + ADR-0022 D7 manual intervention ──
  abort: (runId: number, reason?: string) =>
    unwrapApiResponse<PlanRunAbortResult>(
      apiClient.post(`/plan-runs/${runId}/abort`, reason ? { reason } : {}),
    ),

  retryDispatch: (runId: number) =>
    unwrapApiResponse<PlanRunDispatchRetryResult>(
      apiClient.post(`/plan-runs/${runId}/retry-dispatch`, {}),
    ),

  manualRetryJob: (runId: number, jobId: number, reason?: string) =>
    unwrapApiResponse<JobManualActionResult>(
      apiClient.post(
        `/plan-runs/${runId}/jobs/${jobId}/manual-retry`,
        reason ? { reason } : {},
      ),
    ),

  manualExitJob: (runId: number, jobId: number, reason?: string) =>
    unwrapApiResponse<JobManualActionResult>(
      apiClient.post(
        `/plan-runs/${runId}/jobs/${jobId}/manual-exit`,
        reason ? { reason } : {},
      ),
    ),

  listJobArtifacts: (runId: number, jobId: number) =>
    unwrapApiResponse<JobArtifactEntry[]>(
      apiClient.get(`/plan-runs/${runId}/jobs/${jobId}/artifacts`),
    ),

  artifactDownloadUrl: (runId: number, jobId: number, artifactId: number) =>
    `/api/v1/plan-runs/${runId}/jobs/${jobId}/artifacts/${artifactId}/download`,

  // ADR-0025 Sprint 3: crash 详情端点
  getCrashDetails: (runId: number, packageName?: string) =>
    unwrapApiResponse<CrashDetailEntry[]>(
      apiClient.get(`/plan-runs/${runId}/crash-details`, {
        params: packageName ? { package_name: packageName } : undefined,
      }),
    ),

  // ADR-0025 Sprint 4: 归档-2/3 scan/merge/extract
  getDedupStatus: (runId: number) =>
    unwrapApiResponse<{
      plan_run_id: number;
      artifacts: unknown[];
      archive?: {
        hosts_triggered?: number;
        hosts_with_artifacts?: number;
        scan_artifacts_registered?: number;
        hosts_not_acked?: number;
      } | null;
      scan_failed?: boolean;
    }>(
      apiClient.get(`/plan-runs/${runId}/dedup/status`),
    ),

  triggerScan: (runId: number, isFinal: boolean = false) =>
    unwrapApiResponse<{ plan_run_id: number; triggered_hosts: string[]; skipped_offline: unknown[] }>(
      apiClient.post(`/plan-runs/${runId}/dedup/scan`, null, { params: { is_final: isFinal } }),
    ),

  triggerMerge: (runId: number) =>
    unwrapApiResponse<{ status: string; plan_run_id: number }>(
      apiClient.post(`/plan-runs/${runId}/dedup/merge`, {}),
    ),

  triggerExtract: (runId: number) =>
    unwrapApiResponse<{ plan_run_id: number; jira_dir: string; extracted_count: number }>(
      apiClient.post(`/plan-runs/${runId}/dedup/extract`, {}),
    ),
};

function cleanParams(p: object): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(p)) {
    if (v === undefined || v === null || v === '' || v === 'all') continue;
    out[k] = v;
  }
  return out;
}
