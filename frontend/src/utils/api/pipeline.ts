import apiClient from './client';
import type { PipelineTemplate } from './types';

export const pipeline = {
  listTemplates: () => apiClient.get<PipelineTemplate[]>('/pipeline/templates').then(r => r.data),
  getTemplate: (name: string) => apiClient.get<PipelineTemplate>(`/pipeline/templates/${name}`).then(r => r.data),
};
