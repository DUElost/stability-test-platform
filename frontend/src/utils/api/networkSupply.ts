import apiClient, { unwrapApiResponse } from './client';
import type {
  ManualNetworkEnablePayload,
  NetworkSession,
  NetworkSessionAuditOverridePayload,
  NetworkSessionAuditOverrideResult,
} from './types';

export interface NetworkSessionListParams {
  include_released?: boolean;
  host_id?: string;
  resource_type?: string;
  source?: string;
  status?: string;
}

export const networkSupply = {
  listSessions: (params: NetworkSessionListParams = {}) =>
    unwrapApiResponse<NetworkSession[]>(
      apiClient.get('/network-supply/sessions', {
        params: {
          ...(params.include_released ? { include_released: true } : {}),
          ...(params.host_id ? { host_id: params.host_id } : {}),
          ...(params.resource_type ? { resource_type: params.resource_type } : {}),
          ...(params.source ? { source: params.source } : {}),
          ...(params.status ? { status: params.status } : {}),
        },
      }),
    ),
  enableManual: (payload: ManualNetworkEnablePayload) =>
    unwrapApiResponse<NetworkSession>(
      apiClient.post('/network-supply/manual/enable', payload),
    ),
  renewManual: (sessionId: number, extendSeconds = 3600) =>
    unwrapApiResponse<NetworkSession>(
      apiClient.post(`/network-supply/manual/${sessionId}/renew`, {
        extend_seconds: extendSeconds,
      }),
    ),
  releaseSession: (sessionId: number) =>
    unwrapApiResponse<NetworkSession>(
      apiClient.post(`/network-supply/sessions/${sessionId}/release`),
    ),
  auditOverride: (sessionId: number, payload: NetworkSessionAuditOverridePayload) =>
    unwrapApiResponse<NetworkSessionAuditOverrideResult>(
      apiClient.post(`/network-supply/sessions/${sessionId}/audit-override`, payload),
    ),
};
