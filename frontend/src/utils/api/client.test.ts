import { describe, expect, it } from 'vitest';
import { ApiError, toApiError, unwrapApiResponse } from './client';

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
});
