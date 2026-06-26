import apiClient, { unwrapApiResponse } from './client';
import type { JiraDraft, RunReport } from './types';

/** Job-level run report (path param is Job ID, not PlanRun ID). */
export const runs = {
  getCachedReport: (jobId: number) =>
    unwrapApiResponse<RunReport>(apiClient.get(`/runs/${jobId}/report/cached`)),

  getCachedJiraDraft: (jobId: number) =>
    unwrapApiResponse<JiraDraft>(apiClient.get(`/runs/${jobId}/jira-draft/cached`)),
};
