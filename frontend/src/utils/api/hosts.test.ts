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
import { coerceHostList, fetchAllHosts, fetchHostList } from './hosts';

describe('fetchHostList', () => {
  beforeEach(() => {
    vi.mocked(apiClient.get).mockReset();
  });

  it('穿透 include_retired（ADR-0038 D5：显示已退役）', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { items: [], total: 0 } });

    await fetchHostList(0, 200, true);

    expect(apiClient.get).toHaveBeenCalledWith(
      '/hosts',
      { params: { skip: 0, limit: 200, include_retired: true } },
    );
  });

  it('returns the items array for react-query consumers', async () => {
    const host = { id: 'h1', name: 'node-1', ip: '10.0.0.1', status: 'ONLINE' };
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [host], total: 1 },
    });

    await expect(fetchHostList(0, 200)).resolves.toEqual([host]);
    expect(apiClient.get).toHaveBeenCalledWith(
      '/hosts',
      { params: { skip: 0, limit: 200, include_retired: false } },
    );
  });

  it('coerceHostList unwraps paginated cache pollution', () => {
    const host = { id: 'h1', name: 'node-1', ip: '10.0.0.1', status: 'ONLINE' };
    expect(coerceHostList([host])).toEqual([host]);
    expect(coerceHostList({ items: [host], total: 1, skip: 0, limit: 200 })).toEqual([host]);
    expect(coerceHostList(null)).toEqual([]);
  });
});

describe('fetchAllHosts', () => {
  beforeEach(() => {
    vi.mocked(apiClient.get).mockReset();
  });

  it('keeps paging past the 200-per-response cap so the host list is complete (#3152)', async () => {
    const host = (id: number) => ({ id: `h${id}`, name: `node-${id}`, ip: '10.0.0.1', status: 'ONLINE' });
    const pageOne = { items: Array.from({ length: 200 }, (_, i) => host(i + 1)), total: 250, skip: 0, limit: 200 };
    const pageTwo = { items: Array.from({ length: 50 }, (_, i) => host(201 + i)), total: 250, skip: 200, limit: 200 };
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: pageOne })
      .mockResolvedValueOnce({ data: pageTwo });

    const all = await fetchAllHosts(false);

    expect(all).toHaveLength(250);
    expect(all[249].id).toBe('h250');
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    expect(apiClient.get).toHaveBeenLastCalledWith(
      '/hosts',
      { params: { skip: 200, limit: 200, include_retired: false } },
    );
  });

  it('threads include_retired through every page (ADR-0038 D5：退役视图同样会越 200)', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { items: [], total: 0, skip: 0, limit: 200 } });

    await fetchAllHosts(true);

    expect(apiClient.get).toHaveBeenCalledWith(
      '/hosts',
      { params: { skip: 0, limit: 200, include_retired: true } },
    );
  });
});
