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
import { fetchAllPlans } from './plans';

const plan = (id: number) => ({ id, name: `Plan ${id}` });

describe('fetchAllPlans', () => {
  beforeEach(() => {
    vi.mocked(apiClient.get).mockReset();
  });

  it('returns a single page when total fits in one request', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [plan(1), plan(2)], total: 2, skip: 0, limit: 200 },
    });

    await expect(fetchAllPlans()).resolves.toHaveLength(2);
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    expect(apiClient.get).toHaveBeenCalledWith('/plans', {
      params: { skip: 0, limit: 200 },
    });
  });

  it('keeps paging until total is reached (200-per-page cap)', async () => {
    // #3147：`GET /plans` 的 `le=200`；超过 200 个计划时必须翻页，否则静默少计划
    const pageOne = Array.from({ length: 200 }, (_, i) => plan(i + 1));
    const pageTwo = Array.from({ length: 50 }, (_, i) => plan(201 + i));
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: { items: pageOne, total: 250, skip: 0, limit: 200 } })
      .mockResolvedValueOnce({ data: { items: pageTwo, total: 250, skip: 200, limit: 200 } });

    const all = await fetchAllPlans();

    expect(all).toHaveLength(250);
    expect(all[249].id).toBe(250);
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    expect(apiClient.get).toHaveBeenLastCalledWith('/plans', {
      params: { skip: 200, limit: 200 },
    });
  });

  it('stops on an empty page to avoid looping on inconsistent totals', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [], total: 9, skip: 0, limit: 200 },
    });

    await expect(fetchAllPlans()).resolves.toEqual([]);
    expect(apiClient.get).toHaveBeenCalledTimes(1);
  });
});
