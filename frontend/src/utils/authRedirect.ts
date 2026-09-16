/**
 * 登录后回跳目标解析，优先级：router state.from（SPA 内 401 软跳，ProtectedRoute/
 * AdminRoute 的 <Navigate state>）→ ?next=（client.ts / main.tsx 401 硬跳，无法
 * 携带 state）→ '/'。仅接受**站内同源**路径（open-redirect 防护）。
 * GUI 评测 2026-09-14 引入；#2081 补强为归一化判定。
 */
export function resolvePostLoginTarget(state: unknown, nextParam: string | null): string {
  const fromState =
    typeof state === 'object' && state !== null && typeof (state as { from?: unknown }).from === 'string'
      ? (state as { from: string }).from
      : null;
  const candidate = fromState ?? nextParam;
  if (!candidate || !candidate.startsWith('/')) {
    return '/';
  }
  // #2081：只挡字面 '//' 不够——WHATWG URL 对特殊协议会把 '\' 归一为 '/'、
  // 把制表/换行直接剔除，于是 '/\evil.com'、'/\\evil.com'、'/\t/evil.com' 都等价于
  // '//evil.com'（跨源）。跨源目标的后果不是站外跳转（navigate 会抛），而是
  // SecurityError 被 LoginPage 的 catch 当成登录失败——会话已建立却停在登录页。
  // 判据改为「按 URL 规则解析后仍是本站同源」，并把**归一后的**路径交给调用方，
  // 保证「校验的对象」与「跳转的对象」是同一个。
  try {
    const origin = window.location.origin;
    const resolved = new URL(candidate, origin);
    if (resolved.origin !== origin) {
      return '/';
    }
    return `${resolved.pathname}${resolved.search}${resolved.hash}`;
  } catch {
    return '/';
  }
}
