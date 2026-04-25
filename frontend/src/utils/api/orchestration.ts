import apiClient from './client';
import { unwrapApiResponse } from './client';
import type {
  WorkflowDefinition, WorkflowDefinitionCreate, WorkflowRun, WorkflowRunCreate,
  PaginatedJobList, JobInstance, RunReport, JiraDraft, WorkflowSummary,
  JobArtifactEntry, RunStep, PipelineDef, WorkflowRunPreview,
} from './types';

export const orchestration = {
  list: (skip = 0, limit = 50) =>
    unwrapApiResponse<WorkflowDefinition[]>(
      apiClient.get('/workflows', { params: { skip, limit } })
    ),
  get: (id: number) =>
    unwrapApiResponse<WorkflowDefinition>(apiClient.get(`/workflows/${id}`)),
  create: (data: WorkflowDefinitionCreate) =>
    unwrapApiResponse<WorkflowDefinition>(apiClient.post('/workflows', data)),
  update: (id: number, data: Partial<WorkflowDefinitionCreate & {
    task_templates?: { name: string; pipeline_def: PipelineDef; sort_order?: number }[];
    setup_pipeline?: PipelineDef | null;
    teardown_pipeline?: PipelineDef | null;
  }>) =>
    unwrapApiResponse<WorkflowDefinition>(apiClient.put(`/workflows/${id}`, data)),
  delete: (id: number) =>
    unwrapApiResponse<void>(apiClient.delete(`/workflows/${id}`)),
  previewRun: (id: number, data: WorkflowRunCreate) =>
    unwrapApiResponse<WorkflowRunPreview>(apiClient.post(`/workflows/${id}/run/preview`, data)),
  run: (id: number, data: WorkflowRunCreate) =>
    unwrapApiResponse<WorkflowRun>(apiClient.post(`/workflows/${id}/run`, data)),
};

export const execution = {
  listJobs: (skip = 0, limit = 50, workflowId?: number, status?: string) =>
    unwrapApiResponse<PaginatedJobList>(
      apiClient.get('/jobs', {
        params: {
          skip, limit,
          ...(workflowId ? { workflow_id: workflowId } : {}),
          ...(status ? { status } : {}),
        },
      })
    ),
  listRuns: (skip = 0, limit = 50) =>
    unwrapApiResponse<WorkflowRun[]>(apiClient.get('/workflow-runs', { params: { skip, limit } })),
  getRun: (runId: number) =>
    unwrapApiResponse<WorkflowRun>(apiClient.get(`/workflow-runs/${runId}`)),
  getRunJobs: (runId: number) =>
    unwrapApiResponse<JobInstance[]>(apiClient.get(`/workflow-runs/${runId}/jobs`)),
  getJobReport: (runId: number, jobId: number) =>
    unwrapApiResponse<RunReport>(apiClient.get(`/workflow-runs/${runId}/jobs/${jobId}/report`)),
  createJobJiraDraft: (runId: number, jobId: number) =>
    unwrapApiResponse<JiraDraft>(apiClient.post(`/workflow-runs/${runId}/jobs/${jobId}/jira-draft`)),
  getWorkflowSummary: (runId: number) =>
    unwrapApiResponse<WorkflowSummary>(apiClient.get(`/workflow-runs/${runId}/summary`)),
  listJobArtifacts: (runId: number, jobId: number) =>
    unwrapApiResponse<JobArtifactEntry[]>(apiClient.get(`/workflow-runs/${runId}/jobs/${jobId}/artifacts`)),
  getJobReportExportUrl: (_runId: number, jobId: number, format: 'markdown' | 'json' = 'markdown') =>
    `${apiClient.defaults.baseURL}/runs/${jobId}/report/export?format=${format}`,
  getJobSteps: (jobId: number) => apiClient.get<RunStep[]>(`/runs/${jobId}/steps`),
  getCachedJobReport: (jobId: number) => apiClient.get<RunReport>(`/runs/${jobId}/report/cached`),
  getCachedJobJiraDraft: (jobId: number) => apiClient.get<JiraDraft>(`/runs/${jobId}/jira-draft/cached`),
  artifactDownloadUrl: (_runId: number, jobId: number, artifactId: number) =>
    // Legacy backend route names this segment run_id but validates it as job_id.
    `/api/v1/runs/${jobId}/artifacts/${artifactId}/download`,
};
