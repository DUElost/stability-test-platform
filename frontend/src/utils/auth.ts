import axios from 'axios';
import { AUTH_REFRESH_TIMEOUT_MS } from './api/timeouts';

// 防抖：避免单标签内并发 refresh 导致重复请求 (axios interceptor + Socket.IO recovery 共用)
let _refreshInFlight: Promise<AuthRecoveryResult> | null = null;

// 按最小结构消费 Web Locks：不依赖具体 lib.dom 版本的 LockManager 类型，
// 也不假定 navigator 在所有运行环境存在（vitest/jsdom）。
interface LockManagerLike {
  request<T>(name: string, callback: () => Promise<T>): Promise<T>;
}

/**
 * #703：会话恢复结果三分。
 * - recovered：refresh 成功
 * - rejected：明确未授权（401/403）——可走登出
 * - transient：超时 / 网络 / 5xx / 429——过载或瞬时故障，不得强制登出
 */
export type AuthRecoveryResult = 'recovered' | 'rejected' | 'transient';

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/** 将 auth 相关 HTTP 失败分为「真拒绝」与「瞬时不可达」。 */
export function classifyAuthFailure(error: unknown): 'rejected' | 'transient' {
  const source = isRecord(error) ? error : undefined;
  const isTimeout =
    source?.code === 'ECONNABORTED' ||
    source?.code === 'ETIMEDOUT' ||
    (typeof source?.message === 'string' && /timeout/i.test(source.message));
  if (isTimeout) return 'transient';

  const response = isRecord(source?.response) ? source.response : undefined;
  const status = typeof response?.status === 'number' ? response.status : undefined;
  if (status === 401 || status === 403) return 'rejected';
  // 无响应（断网）、5xx、429：控制面过载 / QueuePool 打满时常见 —— 非会话终态
  return 'transient';
}

// #1039：rotation（#1016 消费即吊销）后，同会话多标签会携带同一个旧
// refresh cookie 并发刷新，输家 401（旧 jti 已被吊销）即被强制登出，其
// 401 响应的 clear cookie 还可能覆盖赢家刚写入的新 cookie。跨标签锁把
// refresh 串行化：后到标签拿到锁时 cookie jar 已被先到的标签更新，刷新
// 直接成功。锁不可用（旧浏览器）时退回单标签防抖，行为与历史一致。
const REFRESH_LOCK_NAME = 'stp:auth:refresh';

async function requestRefreshOnce(): Promise<AuthRecoveryResult> {
  try {
    await axios.post(
      '/api/v1/auth/refresh',
      undefined,
      {
        withCredentials: true,
        // #1199：refresh 挂起会占住 _refreshInFlight 与跨标签 Web Lock，
        // 后续 401 恢复全部排队；超时（失败）后 finally 释放，允许重试
        timeout: AUTH_REFRESH_TIMEOUT_MS,
      },
    );
    return 'recovered';
  } catch (error) {
    return classifyAuthFailure(error);
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
 * 副作用边界：本函数只负责尝试 refresh 并返回结果三分。清缓存/断 socket/跳转
 * 登录等副作用由调用方负责（client.ts 401 拦截器经
 * registerAuthFailureHandler 注册的 handler 执行；useSocketIO 仅据返回值
 * 决定是否重连，不触发页面跳转）。
 */
export async function refreshAccessToken(): Promise<AuthRecoveryResult> {
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
