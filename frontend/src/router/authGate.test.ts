/**
 * #3226：会话探活瞬时故障 ≠ 未登录。
 *
 * 两部分判据：
 *  1. `resolveAuthGate` 真值表——含「撤掉故障后会话其实一直有效」这一事实的对应分支；
 *  2. 结构性守卫（沿用 #2360 的 `adminRoutes.test.ts` 风格）：两个路由门都必须**经过**
 *     `resolveAuthGate`。本单的成因正是「判据只落在 REST 拦截器、守卫那条没落」，
 *     所以只测函数不够——将来新增一条门却自己写 `isSuccess` 二分支，就会原样复发。
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { ApiError } from '@/utils/api/client';
import { classifyAuthFailure } from '@/utils/auth';
import { resolveAuthGate } from './authGate';

const gate = (over: Partial<{isLoading: boolean; isSuccess: boolean; isError: boolean; error: unknown}>) =>
  resolveAuthGate({ isLoading: false, isSuccess: false, isError: false, ...over });

describe('resolveAuthGate：会话查询四态（#3226 / #3531 真实 ApiError 形状）', () => {
  it('ApiError 502 → transient（控制面重启 / nginx 502 同形）', () => {
    expect(gate({ isError: true, error: new ApiError('HTTP_502', 'Bad Gateway', { status: 502 }) })).toBe('transient');
  });
  it('ApiError 断网（NETWORK_ERROR / 无 status）→ transient', () => {
    expect(gate({ isError: true, error: new ApiError('NETWORK_ERROR', '网络请求失败') })).toBe('transient');
    expect(gate({ isError: true, error: new Error('Network Error') })).toBe('transient');
  });
  it('ApiError 超时（TIMEOUT / ECONNABORTED）→ transient（#1199 的上界触发后不得登出）', () => {
    expect(gate({ isError: true, error: new ApiError('TIMEOUT', '请求超时，请重试') })).toBe('transient');
    expect(gate({ isError: true, error: { code: 'ECONNABORTED' } })).toBe('transient');
  });
  it('ApiError 503/504/429 → transient（过载≠未授权，同 #2959 的现实场景）', () => {
    for (const status of [503, 504, 429]) {
      expect(gate({ isError: true, error: new ApiError(`HTTP_${status}`, 'Overloaded', { status }) })).toBe('transient');
    }
  });
  it('真 ApiError 401 / 403 → anonymous（#3531：无 .response 字段也不得误落 transient 死路）', () => {
    expect(gate({ isError: true, error: new ApiError('HTTP_401', 'Unauthorized', { status: 401 }) })).toBe('anonymous');
    expect(gate({ isError: true, error: new ApiError('HTTP_403', 'Forbidden', { status: 403 }) })).toBe('anonymous');
  });
  it('裸 axios 401 / 403 仍兼容 → anonymous', () => {
    expect(gate({ isError: true, error: { response: { status: 401 } } })).toBe('anonymous');
    expect(gate({ isError: true, error: { response: { status: 403 } } })).toBe('anonymous');
  });
  it('classifyAuthFailure 同时识别 ApiError 与裸 axios 的 401/403 与超时（#3531）', () => {
    expect(classifyAuthFailure(new ApiError('HTTP_401', 'Unauthorized', { status: 401 }))).toBe('rejected');
    expect(classifyAuthFailure(new ApiError('HTTP_403', 'Forbidden', { status: 403 }))).toBe('rejected');
    expect(classifyAuthFailure(new ApiError('TIMEOUT', '请求超时，请重试'))).toBe('transient');
    expect(classifyAuthFailure(new ApiError('HTTP_503', 'Service Unavailable', { status: 503 }))).toBe('transient');
  });
  it('成功 → authorized；加载中 → loading', () => {
    expect(gate({ isSuccess: true })).toBe('authorized');
    expect(gate({ isLoading: true })).toBe('loading');
  });
  it('既未加载也未失败也未成功（访客冷启动，未发请求）→ anonymous', () => {
    expect(gate({})).toBe('anonymous');
  });
});

describe('探活查询必须有一次重试（#3226：单次抖动不得即定论）', () => {
  it('useAuthSession 的 retry 不再是 false', () => {
    const file = path.resolve(process.cwd(), 'src/hooks/useAuthSession.ts');
    const src = readFileSync(
      exists(file) ? file : path.resolve(process.cwd(), 'frontend/src/hooks/useAuthSession.ts'), 'utf-8');
    expect(src).not.toMatch(/retry:\s*false/);
    expect(src).toMatch(/retry:\s*1/);
  });
});

function exists(p: string): boolean {
  try { readFileSync(p); return true; } catch { return false; }
}

describe('结构性守卫：两条路由门都必须经过 resolveAuthGate', () => {
  const src = readFileSync(
    (() => {
      const a = path.resolve(process.cwd(), 'src/router/index.tsx');
      return exists(a) ? a : path.resolve(process.cwd(), 'frontend/src/router/index.tsx');
    })(), 'utf-8');

  it('ProtectedRoute 与 AdminRoute 各自调用 resolveAuthGate', () => {
    for (const fn of ['ProtectedRoute', 'AdminRoute']) {
      const start = src.indexOf(`function ${fn}()`);
      expect(start, `${fn} 不存在`).toBeGreaterThanOrEqual(0);
      const block = src.slice(start, start + 900);
      expect(block, `${fn} 未接入 resolveAuthGate`).toContain('resolveAuthGate');
      // 不允许再退回"isSuccess 二分支"老写法
      expect(block, `${fn} 又写回裸 isSuccess 二分支`).not.toMatch(
        /return sessionQ\.isSuccess \?[\s\S]{0,120}<Navigate to="\/login"/);
    }
  });
});
