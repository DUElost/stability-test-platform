/**
 * 审计 Frontend #4/#5 — unwrapApiResponse 严格化 + refreshAccessToken 防抖回归。
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

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
