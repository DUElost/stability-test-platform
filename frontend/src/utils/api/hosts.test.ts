import { describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock('./client', () => ({
  default: {
    get: mocks.get,
  },
}));

import { hosts } from './hosts';

describe('hosts api', () => {
  it('getDetail returns the bare Host response body', async () => {
    const host = {
      id: 'host-101',
      active_job_count: 1,
      active_jobs: [{ id: 3001, device_id: 5, status: 'RUNNING' }],
    };
    mocks.get.mockResolvedValueOnce({ data: host });

    await expect(hosts.getDetail('host-101')).resolves.toEqual(host);
    expect(mocks.get).toHaveBeenCalledWith('/hosts/host-101');
  });
});
