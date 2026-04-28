import apiClient from './client';
import { unwrapApiResponse } from './client';
import type {
  ScriptExecutionCreatePayload,
  ScriptExecutionCreated,
  ScriptExecutionDetail,
  ScriptExecutionList,
  ScriptSequence,
  ScriptSequenceList,
  ScriptSequencePayload,
} from './types';

export const scriptSequences = {
  list: (skip = 0, limit = 100, q?: string) =>
    unwrapApiResponse<ScriptSequenceList>(
      apiClient.get('/script-sequences', { params: { skip, limit, q } }),
    ),
  get: (id: number) =>
    unwrapApiResponse<ScriptSequence>(apiClient.get(`/script-sequences/${id}`)),
  create: (data: ScriptSequencePayload) =>
    unwrapApiResponse<ScriptSequence>(apiClient.post('/script-sequences', data)),
  update: (id: number, data: Partial<ScriptSequencePayload>) =>
    unwrapApiResponse<ScriptSequence>(apiClient.put(`/script-sequences/${id}`, data)),
  remove: (id: number) =>
    unwrapApiResponse<{ deleted: number }>(apiClient.delete(`/script-sequences/${id}`)),
};

export const scriptExecutions = {
  list: (skip = 0, limit = 50) =>
    unwrapApiResponse<ScriptExecutionList>(
      apiClient.get('/script-executions', { params: { skip, limit } }),
    ),
  create: (data: ScriptExecutionCreatePayload) =>
    unwrapApiResponse<ScriptExecutionCreated>(apiClient.post('/script-executions', data)),
  get: (runId: number) =>
    unwrapApiResponse<ScriptExecutionDetail>(apiClient.get(`/script-executions/${runId}`)),
  rerun: (runId: number) =>
    unwrapApiResponse<ScriptExecutionCreated>(apiClient.post(`/script-executions/${runId}/rerun`)),
};
