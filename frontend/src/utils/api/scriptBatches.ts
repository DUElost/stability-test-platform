import apiClient from './client';
import { unwrapApiResponse } from './client';
import type {
  ScriptBatchCreatePayload, ScriptBatch, ScriptBatchList,
} from './types';

export const scriptBatches = {
  create: (data: ScriptBatchCreatePayload) =>
    unwrapApiResponse<ScriptBatch[]>(apiClient.post('/script-batches', data)),
  list: (params?: { skip?: number; limit?: number; device_id?: number; status?: string }) =>
    unwrapApiResponse<ScriptBatchList>(apiClient.get('/script-batches', { params })),
  get: (id: number) =>
    unwrapApiResponse<ScriptBatch>(apiClient.get(`/script-batches/${id}`)),
  rerun: (id: number) =>
    unwrapApiResponse<ScriptBatch>(apiClient.post(`/script-batches/${id}/rerun`)),
};
