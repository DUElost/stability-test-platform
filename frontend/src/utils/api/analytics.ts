import apiClient from './client';
import type { ResultsSummary, ActivityResponse, DeviceMetricsResponse, CompletionTrendResponse } from './types';

export const results = {
  summary: (limit?: number) =>
    apiClient.get<ResultsSummary>('/results/summary', { params: limit ? { limit } : {} }),
};

export const stats = {
  activity: (hours: number = 24) =>
    apiClient.get<ActivityResponse>('/stats/activity', { params: { hours } }),
  deviceMetrics: (deviceId: number, hours: number = 24) =>
    apiClient.get<DeviceMetricsResponse>(`/stats/device/${deviceId}/metrics`, { params: { hours } }),
  completionTrend: (days: number = 7) =>
    apiClient.get<CompletionTrendResponse>('/stats/completion-trend', { params: { days } }),
};
