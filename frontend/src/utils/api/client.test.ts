import { describe, expect, it } from 'vitest';
import { ApiError, classifyApiError, toApiError, unwrapApiResponse } from './client';

describe('unwrapApiResponse', () => {
  it('returns null payloads as-is without falling back to the wrapper body', async () => {
    const result = await unwrapApiResponse<null>(
      Promise.resolve({ data: { data: null, error: null } }),
    );

    expect(result).toBeNull();
  });

  it('throws ApiError when the API wrapper contains an error object', async () => {
    await expect(
      unwrapApiResponse(
        Promise.resolve({
          data: { error: { code: 'BAD_REQUEST', message: 'broken' } },
        }),
      ),
    ).rejects.toMatchObject({
      code: 'BAD_REQUEST',
      message: 'broken',
    });
  });

  it('preserves structured error metadata from the API wrapper', async () => {
    const error = await unwrapApiResponse(
      Promise.resolve({
        data: {
          error: {
            code: 'DISPATCH_QUEUE_UNAVAILABLE',
            message: 'queue unavailable',
            retryable: true,
            plan_run_id: 42,
          },
        },
      }),
    ).catch((reason) => reason);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      code: 'DISPATCH_QUEUE_UNAVAILABLE',
      message: 'queue unavailable',
      retryable: true,
      planRunId: 42,
    });
    expect((error as ApiError).details).toMatchObject({
      plan_run_id: 42,
      retryable: true,
    });
  });
});

describe('toApiError', () => {
  it('parses FastAPI structured detail and preserves status/raw payload', () => {
    const responseData = {
      detail: {
        code: 'DISPATCH_QUEUE_UNAVAILABLE',
        message: 'SAQ is unavailable',
        retryable: true,
        plan_run_id: 99,
      },
    };

    const error = toApiError({
      message: 'Request failed with status code 503',
      response: { status: 503, data: responseData },
    });

    expect(error).toMatchObject({
      code: 'DISPATCH_QUEUE_UNAVAILABLE',
      message: 'SAQ is unavailable',
      status: 503,
      retryable: true,
      planRunId: 99,
      responseData,
    });
  });

  it('maps axios timeout to friendly message and TIMEOUT code (#1199)', () => {
    const aborted = toApiError({
      code: 'ECONNABORTED',
      message: 'timeout of 30000ms exceeded',
    });
    expect(aborted.message).toBe('请求超时，请重试');
    expect(aborted.code).toBe('TIMEOUT');

    // message 含 timeout（无 code 的变体）同样可辨识
    const byMessage = toApiError({ message: 'timeout of 8000ms exceeded' });
    expect(byMessage.message).toBe('请求超时，请重试');
    expect(byMessage.code).toBe('TIMEOUT');

    // 非超时错误不受影响
    expect(toApiError(new Error('boom')).message).toBe('boom');
  });
});

describe('classifyApiError（#2359：HTTP 语义 → 可操作分类）', () => {
  const withStatus = (status: number) =>
    new ApiError(`HTTP_${status}`, `失败 ${status}`, { status });

  it('401/403 → permission', () => {
    expect(classifyApiError(withStatus(401))).toBe('permission');
    expect(classifyApiError(withStatus(403))).toBe('permission');
  });

  it('404 → not_found', () => {
    expect(classifyApiError(withStatus(404))).toBe('not_found');
  });

  it('无 status（网络层/超时）→ network', () => {
    expect(classifyApiError(new ApiError('NETWORK_ERROR', '网络请求失败'))).toBe('network');
    expect(classifyApiError(new ApiError('TIMEOUT', '请求超时，请重试'))).toBe('network');
  });

  it('其余带 status → server', () => {
    expect(classifyApiError(withStatus(500))).toBe('server');
    expect(classifyApiError(withStatus(422))).toBe('server');
  });

  it('非 ApiError 入参经 toApiError 归一后再分类', () => {
    expect(classifyApiError({ response: { status: 403 } })).toBe('permission');
  });
});
