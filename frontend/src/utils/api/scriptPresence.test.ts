import { beforeEach, describe, expect, it, vi } from 'vitest';

// 只替换 axios 实例，保留真实 unwrapApiResponse（信封/error 语义也在被测范围内）。
vi.mock('./client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./client')>();
  return {
    ...actual,
    default: {
      get: vi.fn(),
      post: vi.fn(),
    },
  };
});

import apiClient from './client';
import { scriptPresence } from './hosts';

const counts = { present: 3, missing: 1, mismatch: 2, unknown: 1, n_a: 4, maintenance: 0 };

describe('scriptPresence API（#2958 第五道闸）', () => {
  beforeEach(() => {
    vi.mocked(apiClient.get).mockReset();
    vi.mocked(apiClient.post).mockReset();
  });

  it('summary：GET fleet 汇总并解 ApiResponse 信封', async () => {
    const summary = {
      counts,
      fleet_packages: {},
      hosts_total: 48,
      hosts_with_gap: 2,
      full_versions: 51,
      checked_at_min: '2026-09-21T00:00:00Z',
      checked_at_max: '2026-09-21T01:00:00Z',
      stale: false,
    };
    vi.mocked(apiClient.get).mockResolvedValue({ data: { data: summary, error: null } });

    await expect(scriptPresence.summary()).resolves.toEqual(summary);
    expect(apiClient.get).toHaveBeenCalledWith('/script-presence/summary');
  });

  it('host：GET 单机矩阵（path 带 host_id）', async () => {
    const payload = {
      host_id: 'h-1',
      checked_at: null,
      sweep_id: 'sweep-1',
      counts,
      items: [{ name: 'ensure_root', version: '0.3.0', state: 'missing', detail: 'no such file' }],
    };
    vi.mocked(apiClient.get).mockResolvedValue({ data: { data: payload, error: null } });

    await expect(scriptPresence.host('h-1')).resolves.toEqual(payload);
    expect(apiClient.get).toHaveBeenCalledWith('/script-presence/hosts/h-1');
  });

  it('refresh：POST 并以 query 传 host_id（单机按需重核）', async () => {
    const result = { sweep_id: 'sweep-2', host_id: 'h-1', rows: 11, counts };
    vi.mocked(apiClient.post).mockResolvedValue({ data: { data: result, error: null } });

    await expect(scriptPresence.refresh('h-1')).resolves.toEqual(result);
    expect(apiClient.post).toHaveBeenCalledWith('/script-presence/refresh', undefined, {
      params: { host_id: 'h-1' },
    });
  });

  it('error 信封抛出 ApiError（不把失败当空矩阵）', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { data: null, error: { code: 'HOST_NOT_FOUND', message: 'host not found' } },
    });

    await expect(scriptPresence.host('ghost')).rejects.toMatchObject({ code: 'HOST_NOT_FOUND' });
  });
});
