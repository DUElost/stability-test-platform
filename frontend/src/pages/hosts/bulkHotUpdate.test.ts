import { describe, expect, it, vi } from 'vitest';
import type { Host } from '@/utils/api/types';
import { executeBulkHotUpdate, precheckBulkHotUpdate } from './bulkHotUpdate';

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

  it('treats a 409 race as a safe skip during execution', async () => {
    const trigger = vi.fn(async (id: string | number) => {
      if (id === 2) throw { response: { status: 409 } };
      if (id === 3) throw new Error('ssh failed');
      return { ok: true, host_id: Number(id), message: 'ok' };
    });

    const result = await executeBulkHotUpdate([target(1), target(2), target(3)], trigger);

    expect(result.succeeded.map((item) => item.id)).toEqual([1]);
    expect(result.skipped).toEqual([{ ...target(2), reason: 'state_changed' }]);
    expect(result.failed).toEqual([target(3)]);
  });
});
