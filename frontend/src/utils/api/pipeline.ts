import apiClient from './client';
import type { PipelineTemplate } from './types';

export const pipeline = {
  listTemplates: () => apiClient.get<PipelineTemplate[]>('/pipeline/templates'),
  getTemplate: (name: string) => apiClient.get<PipelineTemplate>(`/pipeline/templates/${name}`),
};
