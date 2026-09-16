import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./client')>();
  return {
    ...actual,
    default: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
      patch: vi.fn(),
    },
  };
});

import apiClient from './client';
import { planRuns } from './planRuns';

/**
 * #2085：`cleanParams` 的 `all` 规则只对**枚举**参数成立。
 *
 * 反例（修复前）：自由文本 `search` 也走同一条规则——搜索词恰为 `all` 时被静默
 * 剥离，请求退回未过滤数据，而 UI（输入框回显、查询键）与 CSV 导出都按
 * 「已应用搜索」呈现。
 */
describe('planRuns.cleanParams（#2085）', () => {
  beforeEach(() => {
    vi.mocked(apiClient.get).mockReset();
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { data: { plan_run_id: 12, total: 0, events: [] } },
    } as never);
  });

  it('自由文本 search 原样保留：搜索词恰为 all 时不得被剥离', async () => {
    await planRuns.getEvents(12, { search: 'all' });

    expect(apiClient.get).toHaveBeenCalledWith('/plan-runs/12/events', {
      params: { search: 'all' },
    });
  });

  it('枚举参数的 all 仍等价于「不筛选」（不发给后端）', async () => {
    await planRuns.getEvents(12, { stage: 'all', severity: 'all', search: 'crash' });

    expect(apiClient.get).toHaveBeenCalledWith('/plan-runs/12/events', {
      params: { search: 'crash' },
    });
  });

  it('devices：枚举 status/link_status/host_id 的 all 照旧剥离，非 all 值原样带出', async () => {
    await planRuns.getDevices(12, { status: 'all', link_status: 'online', host_id: 'all' });

    expect(apiClient.get).toHaveBeenCalledWith('/plan-runs/12/devices', {
      params: { link_status: 'online' },
    });
  });

  it('空串/null/undefined 仍按「未提供」处理', async () => {
    await planRuns.getEvents(12, {
      search: '',
      stage: undefined,
      severity: null as never,
      limit: 50,
    });

    expect(apiClient.get).toHaveBeenCalledWith('/plan-runs/12/events', {
      params: { limit: 50 },
    });
  });
});
