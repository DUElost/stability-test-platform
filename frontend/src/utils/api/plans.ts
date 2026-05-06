import apiClient from './client';
import { unwrapApiResponse } from './client';
import type { Plan, PlanCreate, PlanUpdate, PlanRun, PlanRunCreate, PlanRunPreview } from './types';

export const plans = {
  list: (skip = 0, limit = 50) =>
    unwrapApiResponse<Plan[]>(apiClient.get('/plans', { params: { skip, limit } })),

  get: (id: number) =>
    unwrapApiResponse<Plan>(apiClient.get(`/plans/${id}`)),

  create: (data: PlanCreate) =>
    unwrapApiResponse<Plan>(apiClient.post('/plans', data)),

  update: (id: number, data: PlanUpdate) =>
    unwrapApiResponse<Plan>(apiClient.put(`/plans/${id}`, data)),

  delete: (id: number) =>
    unwrapApiResponse<{ deleted: number }>(apiClient.delete(`/plans/${id}`)),

  previewRun: (id: number, data: PlanRunCreate) =>
    unwrapApiResponse<PlanRunPreview>(apiClient.post(`/plans/${id}/run/preview`, data)),

  run: (id: number, data: PlanRunCreate) =>
    unwrapApiResponse<PlanRun>(apiClient.post(`/plans/${id}/run`, data)),
};
