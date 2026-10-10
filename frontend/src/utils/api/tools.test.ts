import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  unwrapApiResponse: async <T>(promise: Promise<{ data: { data: T } }>) => (await promise).data.data,
}));

import apiClient from './client';
import { scripts } from './tools';

describe('scripts list', () => {
  beforeEach(() => {
    vi.mocked(apiClient.get).mockReset();
  });

  it('keeps the catalog endpoint and can carry parameter_projection', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        data: [{
          id: 1,
          name: 'check_device',
          default_params: { password: 'SENTINEL_PASSWORD' },
          parameter_projection: { script_name: 'check_device', params: [] },
        }],
      },
    });

    const rows = await scripts.list();
    expect(apiClient.get).toHaveBeenCalledWith('/scripts', { params: {} });
    expect(rows[0].default_params).toEqual({ password: 'SENTINEL_PASSWORD' });
    expect(rows[0].parameter_projection?.script_name).toBe('check_device');
  });
});
