import axios from 'axios';

// 防抖：避免单标签内并发 refresh 导致重复请求 (axios interceptor + Socket.IO recovery 共用)
let _refreshInFlight: Promise<boolean> | null = null;

// 按最小结构消费 Web Locks：不依赖具体 lib.dom 版本的 LockManager 类型，
// 也不假定 navigator 在所有运行环境存在（vitest/jsdom）。
interface LockManagerLike {
  request<T>(name: string, callback: () => Promise<T>): Promise<T>;
}

// #1039：rotation（#1016 消费即吊销）后，同会话多标签会携带同一个旧
// refresh cookie 并发刷新，输家 401（旧 jti 已被吊销）即被强制登出，其
// 401 响应的 clear cookie 还可能覆盖赢家刚写入的新 cookie。跨标签锁把
// refresh 串行化：后到标签拿到锁时 cookie jar 已被先到的标签更新，刷新
// 直接成功。锁不可用（旧浏览器）时退回单标签防抖，行为与历史一致。
const REFRESH_LOCK_NAME = 'stp:auth:refresh';

async function requestRefreshOnce(): Promise<boolean> {
  try {
    await axios.post(
      '/api/v1/auth/refresh',
      undefined,
      { withCredentials: true },
    );
    return true;
  } catch {
    return false;
  }
}

/**
 * 浏览器端会话刷新入口。
 *
 * Why: 前端不再持有 access/refresh token，统一依赖 HttpOnly cookie。
 *      所有 401 恢复都走这里，避免并发 refresh 风暴。
 *
 * 串行化边界：单标签内由 _refreshInFlight 防抖；跨标签由 Web Locks 互斥
 * （#1039）。锁按「发起即入队、拿到锁才发请求」工作，因此后到标签发出
 * 请求时必然读到赢家写入的新 cookie。
 *
 * 副作用边界：本函数只负责尝试 refresh 并返回成败。清缓存/断 socket/跳转
 * 登录等副作用由调用方负责（client.ts 401 拦截器经
 * registerAuthFailureHandler 注册的 handler 执行；useSocketIO 仅据返回值
 * 决定是否重连，不触发页面跳转）。
 */
export async function refreshAccessToken(): Promise<boolean> {
  if (_refreshInFlight) return _refreshInFlight;

  const locks =
    typeof navigator !== 'undefined'
      ? (navigator as unknown as { locks?: LockManagerLike }).locks
      : undefined;

  _refreshInFlight = (
    locks ? locks.request(REFRESH_LOCK_NAME, requestRefreshOnce) : requestRefreshOnce()
  ).finally(() => {
    _refreshInFlight = null;
  });

  return _refreshInFlight;
}
