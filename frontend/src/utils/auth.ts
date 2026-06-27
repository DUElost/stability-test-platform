import axios from 'axios';

// 防抖：避免并发 refresh 导致重复请求 (axios interceptor + Socket.IO recovery 共用)
let _refreshInFlight: Promise<boolean> | null = null;

/**
 * 浏览器端会话刷新入口。
 *
 * Why: 前端不再持有 access/refresh token，统一依赖 HttpOnly cookie。
 *      所有 401 恢复都走这里，避免并发 refresh 风暴。
 *
 * 副作用边界：本函数只负责尝试 refresh 并返回成败。清缓存/断 socket/跳转
 * 登录等副作用由调用方负责（client.ts 401 拦截器经
 * registerAuthFailureHandler 注册的 handler 执行；useSocketIO 仅据返回值
 * 决定是否重连，不触发页面跳转）。
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
      return false;
    } finally {
      _refreshInFlight = null;
    }
  })();

  return _refreshInFlight;
}
