import apiClient from './client';
import type { ResultsSummary, ActivityResponse, DeviceMetricsResponse, CompletionTrendResponse, DashboardSummary } from './types';

export const results = {
  summary: (limit?: number) =>
    apiClient.get<ResultsSummary>('/results/summary', { params: limit ? { limit } : {} }).then(r => r.data),
};

export const stats = {
  activity: (hours: number = 24) =>
    apiClient.get<ActivityResponse>('/stats/activity', { params: { hours } }).then(r => r.data),
  deviceMetrics: (deviceId: number, hours: number = 24) =>
    apiClient.get<DeviceMetricsResponse>(`/stats/device/${deviceId}/metrics`, { params: { hours } }).then(r => r.data),
  completionTrend: (days: number = 7) =>
    apiClient.get<CompletionTrendResponse>('/stats/completion-trend', { params: { days } }).then(r => r.data),
  dashboardSummary: () =>
    apiClient.get<DashboardSummary>('/stats/dashboard-summary').then(r => r.data),
};
