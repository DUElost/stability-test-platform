import apiClient from './client';
import { unwrapApiResponse } from './client';
import type {
  PlanRun,
  PlanRunStatus,
  PlanJobInstance,
  PlanRunSummary,
  JobArtifactEntry,
  PlanChain,
  PlanRunTimeline,
  PlanRunEventsPayload,
  PlanRunDevicesPayload,
  WatcherSummary,
  WatcherTimeScope,
  JobManualActionResult,
  PlanRunAbortResult,
  PlanRunDispatchRetryResult,
  EventStage,
  EventSeverity,
  DeviceUiStatus,
  CrashDetailEntry,
} from './types';

export interface ListPlanRunEventsParams {
  stage?: EventStage | 'all';
  severity?: EventSeverity | 'all';
  limit?: number;
  offset?: number;
}

export interface ListPlanRunDevicesParams {
  status?: DeviceUiStatus | 'all';
  host_id?: string | 'all';
}

export const planRuns = {
  list: (skip = 0, limit = 50, planId?: number, status?: PlanRunStatus) => {
    const params: Record<string, string | number> = { skip, limit };
    if (planId != null) params.plan_id = planId;
    if (status) params.status = status;
    return unwrapApiResponse<PlanRun[]>(apiClient.get('/plan-runs', { params }));
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
    unwrapApiResponse<{ plan_run_id: number; artifacts: unknown[] }>(
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
