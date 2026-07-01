/** ADR-0025 §10: dedup → Jira 提单 API（独立「问题管理」活动，与 PlanRun 解耦）。 */
import apiClient, { unwrapApiResponse } from './client';
import type { JiraRunRecord } from './types';

export type JiraVendor = 'transsion' | 'tinno';
export type JiraStage = 'upload_list' | 'create';

export interface JiraRunStart {
  console_run_id: string;
  room: string;
  vendor: string;
  stage: string;
}

export interface ConsoleRunStatus {
  run_id: string;
  run_key: string;
  label: string;
  status: 'RUNNING' | 'SUCCESS' | 'FAILED' | 'CANCELED' | string;
  exit_code: number | null;
  started_at: string;
  ended_at: string | null;
  seq: number;
  error: string | null;
}

export interface ConsoleLogReplay {
  run_id: string;
  from_seq: number;
  lines: string[];
  seq: number;
  status: string;
}

export interface StartJiraRunParams {
  vendor: JiraVendor;
  stage: JiraStage;
  dryRun: boolean;
  reporter?: string;
  /** 输入来源：upload=手动上传文件；plan_run=选 PlanRunArtifact（免上传） */
  source?: 'upload' | 'plan_run';
  /** source=plan_run 时必填：PlanRunArtifact.id */
  artifactId?: number;
  /** source=upload 时必填：上传的文件 */
  file?: File;
}

export const dedup = {
  startJiraRun: (p: StartJiraRunParams) => {
    const fd = new FormData();
    fd.append('vendor', p.vendor);
    fd.append('stage', p.stage);
    fd.append('dry_run', String(p.dryRun));
    fd.append('source', p.source ?? 'upload');
    if (p.reporter) fd.append('reporter', p.reporter);
    if ((p.source ?? 'upload') === 'plan_run') {
      if (!p.artifactId) throw new Error('artifactId is required for source=plan_run');
      fd.append('artifact_id', String(p.artifactId));
    } else {
      if (!p.file) throw new Error('file is required for source=upload');
      fd.append('file', p.file);
    }
    return unwrapApiResponse<JiraRunStart>(apiClient.post(`/jira/runs`, fd));
  },

  getRunStatus: (consoleRunId: string) =>
    unwrapApiResponse<ConsoleRunStatus>(apiClient.get(`/jira/runs/${consoleRunId}`)),

  getRunLog: (consoleRunId: string, fromSeq = 0) =>
    unwrapApiResponse<ConsoleLogReplay>(
      apiClient.get(`/jira/runs/${consoleRunId}/log`, { params: { from_seq: fromSeq } }),
    ),

  cancelRun: (consoleRunId: string) =>
    unwrapApiResponse<{ console_run_id: string; canceled: boolean }>(
      apiClient.post(`/jira/runs/${consoleRunId}/cancel`, {}),
    ),

  listRuns: (params?: { vendor?: string; status?: string; limit?: number }) =>
    unwrapApiResponse<JiraRunRecord[]>(
      apiClient.get(`/jira/runs`, { params }),
    ),

  getRunRecord: (consoleRunId: string) =>
    unwrapApiResponse<JiraRunRecord>(apiClient.get(`/jira/runs/${consoleRunId}/record`)),
};
