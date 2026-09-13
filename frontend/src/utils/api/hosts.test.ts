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
import { coerceHostList, fetchHostList } from './hosts';

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
