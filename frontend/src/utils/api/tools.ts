import apiClient from './client';
import { unwrapApiResponse } from './client';
import type {
  ActionTemplateEntry, ActionTemplateCreatePayload, ActionTemplateUpdatePayload,
  ScriptEntry,
} from './types';

export const actionTemplates = {
  list: (isActive?: boolean) =>
    unwrapApiResponse<ActionTemplateEntry[]>(
      apiClient.get('/action-templates', { params: isActive != null ? { is_active: isActive } : {} })
    ),
  get: (id: number) =>
    unwrapApiResponse<ActionTemplateEntry>(apiClient.get(`/action-templates/${id}`)),
  create: (data: ActionTemplateCreatePayload) =>
    unwrapApiResponse<ActionTemplateEntry>(apiClient.post('/action-templates', data)),
  update: (id: number, data: ActionTemplateUpdatePayload) =>
    unwrapApiResponse<ActionTemplateEntry>(apiClient.put(`/action-templates/${id}`, data)),
  remove: (id: number) =>
    unwrapApiResponse<void>(apiClient.delete(`/action-templates/${id}`)),
};

export const scripts = {
  listCategories: () =>
    unwrapApiResponse<string[]>(apiClient.get('/scripts/categories')),
  list: (isActive?: boolean) =>
    unwrapApiResponse<ScriptEntry[]>(
      apiClient.get('/scripts', { params: isActive != null ? { is_active: isActive } : {} }),
    ),
  get: (id: number) =>
    unwrapApiResponse<ScriptEntry>(apiClient.get(`/scripts/${id}`)),
  create: (data: Omit<ScriptEntry, 'id' | 'created_at' | 'updated_at'>) =>
    unwrapApiResponse<ScriptEntry>(apiClient.post('/scripts', data)),
  update: (id: number, data: Partial<Omit<ScriptEntry, 'id' | 'created_at' | 'updated_at'>>) =>
    unwrapApiResponse<ScriptEntry>(apiClient.put(`/scripts/${id}`, data)),
  remove: (id: number) =>
    unwrapApiResponse<void>(apiClient.delete(`/scripts/${id}`)),
  scan: () =>
    unwrapApiResponse<{ created: number; skipped: number; deactivated: number; conflicts: any[] }>(
      apiClient.post('/scripts/scan'),
    ),
  createVersion: (name: string, data: {
    version: string;
    nfs_path: string;
    content_sha256: string;
    param_schema?: Record<string, any>;
    default_params: Record<string, any>;
  }) =>
    unwrapApiResponse<ScriptEntry>(apiClient.post(`/scripts/${name}/versions`, data)),
};
