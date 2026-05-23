import axios from 'axios';
import { clearAppQueryCache } from '@/components/QueryProvider';

// 防抖：避免并发 refresh 导致重复请求 (axios interceptor + Socket.IO recovery 共用)
let _refreshInFlight: Promise<boolean> | null = null;

/**
 * 浏览器端会话刷新入口。
 *
 * Why: 前端不再持有 access/refresh token，统一依赖 HttpOnly cookie。
 *      所有 401 恢复都走这里，避免并发 refresh 风暴。
 */
export async function refreshAccessToken(): Promise<boolean> {
  if (_refreshInFlight) return _refreshInFlight;

  _refreshInFlight = (async () => {
    try {
      await axios.post(
        '/api/v1/auth/refresh',
        undefined,
        { withCredentials: true },
      );
      return true;
    } catch {
      // 已经在 /login 时跳过 clearAppQueryCache + redirect:
      // queryClient.clear() 会重置仍挂载的 useAuthSession 观察者 → 立即重新 GET /auth/me
      // → 再次 401 → 再次清缓存,形成 "校验登录状态中..." 无限刷新。pathname 已是 /login
      // 时本就无需 redirect,这两个副作用纯粹是死循环触发器,必须一并跳过。
      if (window.location.pathname !== '/login') {
        clearAppQueryCache();
        window.location.href = '/login';
      }
      return false;
    } finally {
      _refreshInFlight = null;
    }
  })();

  return _refreshInFlight;
}
