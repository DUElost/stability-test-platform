import apiClient from './client';
import type { PipelineTemplate } from './types';

export const pipeline = {
  listTemplates: (specialtyKey?: string) =>
    apiClient
      .get<PipelineTemplate[]>('/pipeline/templates', {
        params: specialtyKey ? { specialty_key: specialtyKey } : undefined,
      })
      .then(r => r.data),
  getTemplate: (name: string) =>
    apiClient.get<PipelineTemplate>(`/pipeline/templates/${encodeURIComponent(name)}`).then(r => r.data),
};
