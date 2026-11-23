import { classifyApiError } from '@/utils/api/client';

/**
 * 会话探活结果 → 路由门控决策。
 *
 * 为什么单独成函数：`#703` 早已把「超时/5xx/断网 ≠ 未授权」写进 REST 拦截器，
 * 但路由守卫是**另一条独立发起的** `/auth/me`，它只看 `isSuccess`，于是把
 * 「探活失败」和「确实没登录」折叠成同一个值——一次接口抖动就会把**会话仍有效**
 * 的用户踢回登录页（#3226，实测撤掉故障注入后 `/auth/me` 立刻 200）。
 *
 * #3531：`/auth/me` 经 `apiClient` 响应拦截器重抛为 `ApiError`（有顶层 `.status`，
 * 无 `.response`）；此前直接喂给只读裸 axios `.response.status` 的 `classifyAuthFailure`，
 * 导致真实 403 被永久钉死在 `transient`。改走形状容错的 `classifyApiError()`，同时兼容
 * `ApiError` 与裸 axios 形状（`401/403 → permission → anonymous`，其余均属 `transient`）。
 */
export type AuthGate = 'loading' | 'transient' | 'anonymous' | 'authorized';

export interface AuthGateInput {
  isLoading: boolean;
  isSuccess: boolean;
  isError: boolean;
  error?: unknown;
}

export function resolveAuthGate(q: AuthGateInput): AuthGate {
  if (q.isSuccess) return 'authorized';
  if (q.isError) {
    return classifyApiError(q.error) === 'permission' ? 'anonymous' : 'transient';
  }
  if (q.isLoading) return 'loading';
  return 'anonymous';
}
