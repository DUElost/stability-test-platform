import apiClient from './client';
import { unwrapApiResponse } from './client';
import type {
  ToolEntry, BuiltinActionEntry, BuiltinActionUpdatePayload,
  ActionTemplateEntry, ActionTemplateCreatePayload, ActionTemplateUpdatePayload,
} from './types';

export const tools = {
  listCategories: () =>
    unwrapApiResponse<string[]>(apiClient.get('/tools/categories')),
  list: (category?: string) =>
    unwrapApiResponse<ToolEntry[]>(
      apiClient.get('/tools', { params: category ? { category } : {} }),
    ),
  get: (id: number) =>
    unwrapApiResponse<ToolEntry>(apiClient.get(`/tools/${id}`)),
  create: (data: Omit<ToolEntry, 'id' | 'created_at'>) =>
    unwrapApiResponse<ToolEntry>(apiClient.post('/tools', data)),
  update: (id: number, data: Partial<Omit<ToolEntry, 'id' | 'created_at'>>) =>
    unwrapApiResponse<ToolEntry>(apiClient.put(`/tools/${id}`, data)),
  delete: (id: number) =>
    unwrapApiResponse<void>(apiClient.delete(`/tools/${id}`)),
  scan: () =>
    unwrapApiResponse<{ created: number; updated: number }>(apiClient.post('/tools/scan')),
  previewScan: () =>
    unwrapApiResponse<{ tools: any[]; count: number }>(apiClient.get('/tools/scan/preview')),
};

export const toolCatalog = {
  list: (isActive?: boolean) =>
    unwrapApiResponse<ToolEntry[]>(
      apiClient.get('/tools', { params: isActive != null ? { is_active: isActive } : {} })
    ),
  get: (id: number) =>
    unwrapApiResponse<ToolEntry>(apiClient.get(`/tools/${id}`)),
  create: (data: Omit<ToolEntry, 'id' | 'created_at'>) =>
    unwrapApiResponse<ToolEntry>(apiClient.post('/tools', data)),
  update: (id: number, data: Partial<Omit<ToolEntry, 'id' | 'created_at'>>) =>
    unwrapApiResponse<ToolEntry>(apiClient.put(`/tools/${id}`, data)),
  remove: (id: number) =>
    unwrapApiResponse<void>(apiClient.delete(`/tools/${id}`)),
};

export const builtinCatalog = {
  list: (isActive?: boolean) =>
    unwrapApiResponse<BuiltinActionEntry[]>(
      apiClient.get('/builtin-actions', { params: isActive != null ? { is_active: isActive } : {} })
    ),
  get: (name: string) =>
    unwrapApiResponse<BuiltinActionEntry>(apiClient.get(`/builtin-actions/${name}`)),
  update: (name: string, data: BuiltinActionUpdatePayload) =>
    unwrapApiResponse<BuiltinActionEntry>(apiClient.put(`/builtin-actions/${name}`, data)),
};

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
