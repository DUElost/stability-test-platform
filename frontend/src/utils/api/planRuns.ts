import apiClient from './client';
import { unwrapApiResponse } from './client';
import type {
  PlanRun,
  PlanJobInstance,
  PlanRunSummary,
  JobArtifactEntry,
} from './types';

export const planRuns = {
  list: (skip = 0, limit = 50, planId?: number, status?: string) => {
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

  listJobArtifacts: (runId: number, jobId: number) =>
    unwrapApiResponse<JobArtifactEntry[]>(
      apiClient.get(`/plan-runs/${runId}/jobs/${jobId}/artifacts`)
    ),

  artifactDownloadUrl: (runId: number, jobId: number, artifactId: number) =>
    `/api/v1/plan-runs/${runId}/jobs/${jobId}/artifacts/${artifactId}/download`,
};
