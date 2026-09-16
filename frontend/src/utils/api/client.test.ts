import { describe, expect, it } from 'vitest';
import { ApiError, classifyApiError, loadErrorCopy, toApiError, unwrapApiResponse } from './client';

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


describe('loadErrorCopy（#2361：404 与网络层分文案）', () => {
  const copy = { notFound: '执行记录不存在或已被清理', fallback: '兜底' };
  const withStatus = (status: number) =>
    new ApiError(`HTTP_${status}`, `失败 ${status}`, { status });

  it('404 用调用方的不存在文案，且不给重试', () => {
    const result = loadErrorCopy(withStatus(404), copy);
    expect(result.description).toBe('执行记录不存在或已被清理');
    expect(result.retryable).toBe(false);
  });

  it('404 不透出服务端英文 detail（本地化）', () => {
    const error = toApiError({
      message: 'Request failed with status code 404',
      response: { status: 404, data: { detail: 'plan run not found' } },
    });
    expect(loadErrorCopy(error, copy).description).not.toContain('not found');
  });

  it('网络层（无 status）提示连接并可重试', () => {
    const result = loadErrorCopy(new ApiError('NETWORK_ERROR', '网络请求失败'), copy);
    expect(result.description).toContain('网络');
    expect(result.retryable).toBe(true);
  });

  it('带 status 的服务端错误用其 message；缺 message 回落调用方兜底', () => {
    // ApiError（已归一）用它自己的 message；原始 axios 错误的兜底文案由 toApiError 负责
    expect(loadErrorCopy(withStatus(500), copy).description).toBe('失败 500');
    expect(
      loadErrorCopy(new ApiError('INTERNAL', '', { status: 500 }), copy).description,
    ).toBe('兜底');
  });
});
