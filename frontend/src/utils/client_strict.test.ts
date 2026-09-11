/**
 * 审计 Frontend #4/#5 — unwrapApiResponse 严格化 + refreshAccessToken 防抖回归；
 * #1039 — refresh 跨标签锁（Web Locks）回归。
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { AUTH_REFRESH_TIMEOUT_MS } from '@/utils/api/timeouts';

describe('unwrapApiResponse — 严格契约 (审计 Frontend #4)', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
  });

  it('returns body.data when success', async () => {
    const { unwrapApiResponse } = await import('@/utils/api/client');
    const promise = Promise.resolve({ data: { data: { id: 1 }, error: null } });
    await expect(unwrapApiResponse(promise)).resolves.toEqual({ id: 1 });
  });

  it('throws ApiError with code+message on body.error', async () => {
    const { unwrapApiResponse, ApiError } = await import('@/utils/api/client');
    const promise = Promise.resolve({
      data: { error: { code: 'VALIDATION_FAILED', message: 'bad request' } },
    });
    await expect(unwrapApiResponse(promise)).rejects.toMatchObject({
      code: 'VALIDATION_FAILED',
      message: 'bad request',
      name: 'ApiError',
    });
    await expect(unwrapApiResponse(promise)).rejects.toBeInstanceOf(ApiError);
  });

  it('throws MALFORMED_RESPONSE when neither data nor error present', async () => {
    const { unwrapApiResponse } = await import('@/utils/api/client');
    const promise = Promise.resolve({ data: {} as any });
    await expect(unwrapApiResponse(promise)).rejects.toMatchObject({
      code: 'MALFORMED_RESPONSE',
    });
  });

  it('throws MALFORMED_RESPONSE when data is undefined', async () => {
    const { unwrapApiResponse } = await import('@/utils/api/client');
    const promise = Promise.resolve({
      data: { data: undefined, error: null } as any,
    });
    await expect(unwrapApiResponse(promise)).rejects.toMatchObject({
      code: 'MALFORMED_RESPONSE',
    });
  });

  it('accepts null as a valid T (e.g. delete endpoints returning data: null)', async () => {
    // 审计 #4 收紧点:`data === null` 是合法的 success (空响应),只有 undefined 才视为契约违反
    const { unwrapApiResponse } = await import('@/utils/api/client');
    const promise = Promise.resolve({ data: { data: null, error: null } as any });
    await expect(unwrapApiResponse(promise)).resolves.toBeNull();
  });
});


describe('refreshAccessToken — 单飞行防抖 (审计 Frontend #5)', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('concurrent calls reuse the in-flight refresh promise', async () => {
    const postSpy = vi.fn().mockResolvedValue({ data: { ok: true } });
    vi.doMock('axios', () => ({ default: { post: postSpy } }));

    const { refreshAccessToken } = await import('@/utils/auth');
    const [r1, r2, r3] = await Promise.all([
      refreshAccessToken(),
      refreshAccessToken(),
      refreshAccessToken(),
    ]);

    expect(postSpy).toHaveBeenCalledTimes(1);
    expect(postSpy).toHaveBeenCalledWith(
      '/api/v1/auth/refresh',
      undefined,
      expect.objectContaining({ withCredentials: true }),
    );
    expect(r1).toBe(true);
    expect(r2).toBe(true);
    expect(r3).toBe(true);
  });

  it('subsequent call after in-flight resolves issues a fresh POST', async () => {
    const postSpy = vi
      .fn()
      .mockResolvedValueOnce({ data: { ok: true } })
      .mockResolvedValueOnce({ data: { ok: true } });
    vi.doMock('axios', () => ({ default: { post: postSpy } }));

    const { refreshAccessToken } = await import('@/utils/auth');
    await refreshAccessToken();
    await refreshAccessToken();

    expect(postSpy).toHaveBeenCalledTimes(2);
  });

  it('refresh 请求带应用层超时（#1199）', async () => {
    const postSpy = vi.fn().mockResolvedValue({ data: { ok: true } });
    vi.doMock('axios', () => ({ default: { post: postSpy } }));

    const { refreshAccessToken } = await import('@/utils/auth');
    await refreshAccessToken();

    expect(postSpy).toHaveBeenCalledWith(
      '/api/v1/auth/refresh',
      undefined,
      expect.objectContaining({ timeout: AUTH_REFRESH_TIMEOUT_MS }),
    );
  });

  it('refresh 超时失败后 in-flight 释放，后续调用可重试（#1199）', async () => {
    const postSpy = vi
      .fn()
      .mockRejectedValueOnce(
        Object.assign(new Error('timeout of 10000ms exceeded'), { code: 'ECONNABORTED' }),
      )
      .mockResolvedValueOnce({ data: { ok: true } });
    vi.doMock('axios', () => ({ default: { post: postSpy } }));

    const { refreshAccessToken } = await import('@/utils/auth');
    expect(await refreshAccessToken()).toBe(false); // 超时 → 失败
    expect(await refreshAccessToken()).toBe(true); // 已释放 → 可立即重试
    expect(postSpy).toHaveBeenCalledTimes(2);
  });

  it('returns false when cookie refresh fails', async () => {
    const postSpy = vi.fn().mockRejectedValue(new Error('401'));
    vi.doMock('axios', () => ({ default: { post: postSpy } }));
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { pathname: '/login', href: '/login' },
    });

    const { refreshAccessToken } = await import('@/utils/auth');
    const result = await refreshAccessToken();

    expect(result).toBe(false);
    expect(postSpy).toHaveBeenCalledTimes(1);
  });
});

describe('refreshAccessToken — 跨标签锁 (#1039)', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('waits for the cross-tab lock before POSTing when navigator.locks exists', async () => {
    const postSpy = vi.fn().mockResolvedValue({ data: { ok: true } });
    vi.doMock('axios', () => ({ default: { post: postSpy } }));

    // 模拟另一标签正持有 refresh 锁：拿到锁之前不得发出 POST。
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const request = vi.fn(async (_name: string, cb: () => Promise<boolean>) => {
      await gate;
      return cb();
    });
    Object.defineProperty(navigator, 'locks', {
      value: { request },
      configurable: true,
    });

    try {
      const { refreshAccessToken } = await import('@/utils/auth');
      const pending = refreshAccessToken();

      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(postSpy).not.toHaveBeenCalled();

      release();
      await expect(pending).resolves.toBe(true);
      expect(postSpy).toHaveBeenCalledTimes(1);
      expect(request).toHaveBeenCalledWith('stp:auth:refresh', expect.any(Function));
    } finally {
      delete (navigator as unknown as { locks?: unknown }).locks;
    }
  });

  it('keeps single-tab debounce on top of the lock (one POST for concurrent calls)', async () => {
    const postSpy = vi.fn().mockResolvedValue({ data: { ok: true } });
    vi.doMock('axios', () => ({ default: { post: postSpy } }));

    const request = vi.fn((_name: string, cb: () => Promise<boolean>) => cb());
    Object.defineProperty(navigator, 'locks', {
      value: { request },
      configurable: true,
    });

    try {
      const { refreshAccessToken } = await import('@/utils/auth');
      const [r1, r2] = await Promise.all([refreshAccessToken(), refreshAccessToken()]);

      expect(r1).toBe(true);
      expect(r2).toBe(true);
      expect(postSpy).toHaveBeenCalledTimes(1);
      expect(request).toHaveBeenCalledTimes(1);
    } finally {
      delete (navigator as unknown as { locks?: unknown }).locks;
    }
  });
});
