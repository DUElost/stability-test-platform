import { describe, expect, it, vi } from 'vitest';
import type { Host } from '@/utils/api/types';
import { BULK_HOT_UPDATE_SKIP_LABEL, precheckBulkHotUpdate } from './bulkHotUpdate';

const target = (id: number) => ({ id, label: `host-${id}` });
const detail = (overrides: Partial<Host> = {}): Host => ({
  id: '1',
  name: 'host',
  ip: '127.0.0.1',
  ssh_user: null,
  status: 'ONLINE',
  last_heartbeat: null,
  extra: {},
  mount_status: {},
  agent_installed: true,
  active_job_count: 0,
  ...overrides,
});

describe('bulk hot update helpers', () => {
  it('exposes Chinese skip labels for the progress panel', () => {
    expect(BULK_HOT_UPDATE_SKIP_LABEL.active_jobs).toBe('存在活跃 Job');
    expect(BULK_HOT_UPDATE_SKIP_LABEL.offline).toBe('主机离线');
  });

  it('only marks online installed hosts without active jobs as eligible', async () => {
    const getDetail = vi.fn(async (id: string | number) => {
      if (id === 2) return detail({ active_job_count: 1 });
      if (id === 3) return detail({ status: 'OFFLINE' });
      if (id === 4) throw new Error('network');
      return detail();
    });

    const result = await precheckBulkHotUpdate([target(1), target(2), target(3), target(4)], getDetail);

    expect(result.eligible.map((item) => item.id)).toEqual([1]);
    expect(result.skipped.map((item) => item.reason).sort()).toEqual([
      'active_jobs',
      'offline',
      'precheck_failed',
    ]);
  });

});

describe('ADR-0038 退役判据（#1807）', () => {
  it('退役主机单独跳过（reason=retired，不与其他原因混报）', async () => {
    const getDetail = vi.fn(async (id: string | number) => {
      if (id === 2) return detail({ retired_at: '2026-09-13T00:00:00Z', active_job_count: 3 });
      return detail();
    });

    const result = await precheckBulkHotUpdate([target(1), target(2)], getDetail);

    expect(result.eligible.map((item) => item.id)).toEqual([1]);
    expect(result.skipped.map((item) => item.reason)).toEqual(['retired']);
    expect(BULK_HOT_UPDATE_SKIP_LABEL.retired).toBe('主机已退役');
  });
});
