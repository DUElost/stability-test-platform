import apiClient from './client';
import type { ResultsSummary, ActivityResponse, DeviceMetricsResponse, CompletionTrendResponse, DashboardSummary, FileServerOverview, HostFailureRateResponse, PlanSuccessRateResponse, PlanRunPassRateTrendResponse, RiskTrend } from './types';

export const results = {
  summary: (limit?: number, projectKey?: string) =>
    apiClient.get<ResultsSummary>('/results/summary', {
      params: {
        ...(limit ? { limit } : {}),
        ...(projectKey ? { project_key: projectKey } : {}),
      },
    }).then(r => r.data),
  /** ADR-0029 P2：项目级风险趋势（按天 S/A/B，run 级 DLE 权威聚合）。 */
  riskTrend: (projectKey?: string, days: number = 30) =>
    apiClient.get<RiskTrend>('/results/risk-trend', {
      params: {
        days,
        ...(projectKey ? { project_key: projectKey } : {}),
      },
    }).then(r => r.data),
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
  fileServer: (hours: number = 6) =>
    apiClient.get<FileServerOverview>('/stats/file-server', { params: { hours } }).then(r => r.data),
  hostFailureRate: (days: number = 30, limit: number = 10) =>
    apiClient.get<HostFailureRateResponse>('/stats/host-failure-rate', { params: { days, limit } }).then(r => r.data),
  planSuccessRate: (days: number = 30, limit: number = 10) =>
    apiClient.get<PlanSuccessRateResponse>('/stats/plan-success-rate', { params: { days, limit } }).then(r => r.data),
  planRunPassRateTrend: (days: number = 30) =>
    apiClient.get<PlanRunPassRateTrendResponse>('/stats/plan-run-pass-rate-trend', { params: { days } }).then(r => r.data),
};
