import { classifyAuthFailure } from '@/utils/auth';

/**
 * 会话探活结果 → 路由门控决策。
 *
 * 为什么单独成函数：`#703` 早已把「超时/5xx/断网 ≠ 未授权」写进 REST 拦截器，
 * 但路由守卫是**另一条独立发起的** `/auth/me`，它只看 `isSuccess`，于是把
 * 「探活失败」和「确实没登录」折叠成同一个值——一次接口抖动就会把**会话仍有效**
 * 的用户踢回登录页（#3226，实测撤掉故障注入后 `/auth/me` 立刻 200）。
 * 判据不新造：沿用仓内既有的 `classifyAuthFailure()` 三分。
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
  if (q.isError && classifyAuthFailure(q.error) === 'transient') return 'transient';
  if (q.isLoading) return 'loading';
  return 'anonymous';
}
