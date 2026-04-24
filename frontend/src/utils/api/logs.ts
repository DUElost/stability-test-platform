import apiClient from './client';
import type { RuntimeLogQueryResponse, AgentLogOut } from './types';

export const logs = {
  queryRuntime: (params: {
    job_id?: number;
    job_ids?: number[];
    level?: string;
    q?: string;
    step_id?: string;
    from_ts?: string;
    to_ts?: string;
    cursor?: string;
    limit?: number;
  }) => {
    const reqParams: Record<string, any> = { ...params };
    if (params.job_ids && params.job_ids.length > 0) {
      reqParams.job_ids = params.job_ids.join(',');
    } else {
      delete reqParams.job_ids;
    }
    return apiClient.get<RuntimeLogQueryResponse>('/logs/query', { params: reqParams });
  },
  queryAgent: (data: { host_id: number; log_path?: string; lines?: number }) =>
    apiClient.post<AgentLogOut>('/agent/logs', data),
};
