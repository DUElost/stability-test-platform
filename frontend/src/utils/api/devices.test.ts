import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    patch: vi.fn(),
  },
}));

import apiClient from './client';
import { fetchAllDevices } from './devices';

const device = (id: number) => ({ id, serial: `SN-${id}`, status: 'ONLINE' });

describe('fetchAllDevices', () => {
  beforeEach(() => {
    vi.mocked(apiClient.get).mockReset();
  });

  it('returns a single page when total fits in one request', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [device(1), device(2)], total: 2, skip: 0, limit: 1200 },
    });

    await expect(fetchAllDevices()).resolves.toHaveLength(2);
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    expect(apiClient.get).toHaveBeenCalledWith('/devices', {
      params: { skip: 0, limit: 1200 },
    });
  });

  it('keeps paging until total is reached', async () => {
    const pageOne = Array.from({ length: 1200 }, (_, i) => device(i + 1));
    const pageTwo = Array.from({ length: 300 }, (_, i) => device(1200 + i + 1));
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: { items: pageOne, total: 1500, skip: 0, limit: 1200 } })
      .mockResolvedValueOnce({ data: { items: pageTwo, total: 1500, skip: 1200, limit: 1200 } });

    const all = await fetchAllDevices();
    expect(all).toHaveLength(1500);
    expect(all[1499].id).toBe(1500);
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    expect(apiClient.get).toHaveBeenLastCalledWith('/devices', {
      params: { skip: 1200, limit: 1200 },
    });
  });

  it('stops on an empty page to avoid looping on inconsistent totals', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [], total: 10, skip: 0, limit: 1200 },
    });

    await expect(fetchAllDevices()).resolves.toEqual([]);
    expect(apiClient.get).toHaveBeenCalledTimes(1);
  });
});
