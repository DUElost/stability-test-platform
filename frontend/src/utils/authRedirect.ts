/**
 * 登录后回跳目标解析，优先级：router state.from（SPA 内 401 软跳，ProtectedRoute/
 * AdminRoute 的 <Navigate state>）→ ?next=（client.ts / main.tsx 401 硬跳，无法
 * 携带 state）→ '/'。仅接受站内路径：必须以单个 '/' 开头，拒绝 '//host' 协议
 * 相对与外部 URL（open-redirect 防护）。GUI 评测 2026-09-14 引入。
 */
export function resolvePostLoginTarget(state: unknown, nextParam: string | null): string {
  const fromState =
    typeof state === 'object' && state !== null && typeof (state as { from?: unknown }).from === 'string'
      ? (state as { from: string }).from
      : null;
  const candidate = fromState ?? nextParam;
  if (candidate && candidate.startsWith('/') && !candidate.startsWith('//')) {
    return candidate;
  }
  return '/';
}
