import apiClient, { unwrapApiResponse } from './client';
import type {
  ApiResponseEnvelope,
  AiActionLogEntry,
  AiAssistantAction,
  AiAssistantConfig,
  AiAssistantConfigUpdate,
  AiChatMessage,
  AiChatSession,
  AiConnectionTestResult,
} from './types';

// client.ts baseURL 已含 /api/v1，此处用相对路径（与 management.ts 等模块一致）
const BASE = '/ai-assistant';

/**
 * 平台 AI 助手（ADR-0031）。
 *
 * 权限矩阵：config 三个端点限 admin；会话/消息全员（按用户隔离）；
 * approve/reject/cancel 限 admin。未配置时写操作返回 409 + `ai_not_configured`。
 */
export const aiAssistant = {
  // ── 配置（admin）──
  getConfig: () =>
    unwrapApiResponse(
      apiClient.get<ApiResponseEnvelope<AiAssistantConfig>>(`${BASE}/config`),
    ),
  updateConfig: (payload: AiAssistantConfigUpdate) =>
    unwrapApiResponse(
      apiClient.put<ApiResponseEnvelope<AiAssistantConfig>>(`${BASE}/config`, payload),
    ),
  testConnection: () =>
    unwrapApiResponse(
      apiClient.post<ApiResponseEnvelope<AiConnectionTestResult>>(
        `${BASE}/config/test-connection`,
      ),
    ),

  // ── 会话（全员，按用户隔离）──
  listSessions: () =>
    unwrapApiResponse(
      apiClient.get<ApiResponseEnvelope<AiChatSession[]>>(`${BASE}/sessions`),
    ),
  createSession: (title?: string) =>
    unwrapApiResponse(
      apiClient.post<ApiResponseEnvelope<AiChatSession>>(`${BASE}/sessions`, {
        title,
      }),
    ),
  deleteSession: (sessionId: number) =>
    unwrapApiResponse(
      apiClient.delete<ApiResponseEnvelope<null>>(`${BASE}/sessions/${sessionId}`),
    ),
  listMessages: (sessionId: number) =>
    unwrapApiResponse(
      apiClient.get<ApiResponseEnvelope<AiChatMessage[]>>(
        `${BASE}/sessions/${sessionId}/messages`,
      ),
    ),
  /** 发送消息：后端入队一轮编排，返回 200 + pending 占位消息。 */
  sendMessage: (sessionId: number, content: string) =>
    unwrapApiResponse(
      apiClient.post<ApiResponseEnvelope<AiChatMessage>>(
        `${BASE}/sessions/${sessionId}/messages`,
        { content },
      ),
    ),

  // ── 动作（提案人或 admin 可读；approve/reject/cancel 限 admin）──
  getAction: (actionId: number) =>
    unwrapApiResponse(
      apiClient.get<ApiResponseEnvelope<AiAssistantAction>>(`${BASE}/actions/${actionId}`),
    ),
  approveAction: (actionId: number) =>
    unwrapApiResponse(
      apiClient.post<ApiResponseEnvelope<AiAssistantAction>>(
        `${BASE}/actions/${actionId}/approve`,
      ),
    ),
  rejectAction: (actionId: number) =>
    unwrapApiResponse(
      apiClient.post<ApiResponseEnvelope<AiAssistantAction>>(
        `${BASE}/actions/${actionId}/reject`,
      ),
    ),
  getActionLog: (actionId: number, fromSeq = 0) =>
    unwrapApiResponse(
      apiClient.get<ApiResponseEnvelope<AiActionLogEntry[]>>(`${BASE}/actions/${actionId}/log`, {
        params: { from_seq: fromSeq },
      }),
    ),
  cancelAction: (actionId: number) =>
    unwrapApiResponse(
      apiClient.post<ApiResponseEnvelope<AiAssistantAction>>(
        `${BASE}/actions/${actionId}/cancel`,
      ),
    ),
};
