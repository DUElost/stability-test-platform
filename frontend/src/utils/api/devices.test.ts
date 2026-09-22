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
import { fetchAllDevicePages, fetchAllDevices } from './devices';

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

describe('fetchAllDevicePages', () => {
  beforeEach(() => {
    vi.mocked(apiClient.get).mockReset();
  });

  it('returns the server total alongside the accumulated items', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [device(1), device(2)], total: 2, skip: 0, limit: 1200 },
    });

    // total 必须原样带出：判「有没有拿全」只能靠它，不能用 items.length（#3131）
    await expect(fetchAllDevicePages()).resolves.toEqual({
      items: [device(1), device(2)],
      total: 2,
    });
  });

  it('reports a short read when the server total cannot be reached', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: { items: [device(1)], total: 9, skip: 0, limit: 1200 } })
      .mockResolvedValueOnce({ data: { items: [], total: 9, skip: 1, limit: 1200 } });

    // total 与实际不符时出口，不让调用方死循环；此时 items.length < total 就是
    // 「没拿全」的信号，页面据此显示横幅而不是假装拿齐了
    await expect(fetchAllDevicePages()).resolves.toEqual({ items: [device(1)], total: 9 });
    expect(apiClient.get).toHaveBeenCalledTimes(2);
  });

  it('carries the project filter through every page', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({
        data: { items: Array.from({ length: 1200 }, (_, i) => device(i + 1)), total: 1300, skip: 0, limit: 1200 },
      })
      .mockResolvedValueOnce({
        data: { items: Array.from({ length: 100 }, (_, i) => device(1201 + i)), total: 1300, skip: 1200, limit: 1200 },
      });

    const { items, total } = await fetchAllDevicePages({ projectKey: 'proj-a' });

    expect(items).toHaveLength(1300);
    expect(total).toBe(1300);
    expect(apiClient.get).toHaveBeenNthCalledWith(1, '/devices', {
      params: { skip: 0, limit: 1200, project_key: 'proj-a' },
    });
    expect(apiClient.get).toHaveBeenNthCalledWith(2, '/devices', {
      params: { skip: 1200, limit: 1200, project_key: 'proj-a' },
    });
  });

  it('sends the unassigned sentinel instead of project_key', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [], total: 0, skip: 0, limit: 1200 },
    });

    await fetchAllDevicePages({ unassigned: true });

    expect(apiClient.get).toHaveBeenCalledWith('/devices', {
      params: { skip: 0, limit: 1200, unassigned: true },
    });
  });
});
