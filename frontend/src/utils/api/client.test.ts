import { describe, expect, it } from 'vitest';
import { ApiError, unwrapApiResponse } from './client';

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
    ).rejects.toEqual(new ApiError('BAD_REQUEST', 'broken'));
  });
});
