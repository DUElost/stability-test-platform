import axios from 'axios';
import { clearAppQueryCache } from '@/components/QueryProvider';

// 防抖：避免并发 refresh 导致重复请求 (axios interceptor + Socket.IO recovery 共用)
let _refreshInFlight: Promise<boolean> | null = null;

function redirectToLoginIfNeeded(): void {
  if (window.location.pathname !== '/login') {
    window.location.href = '/login';
  }
}

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
      clearAppQueryCache();
      redirectToLoginIfNeeded();
      return false;
    } finally {
      _refreshInFlight = null;
    }
  })();

  return _refreshInFlight;
}
