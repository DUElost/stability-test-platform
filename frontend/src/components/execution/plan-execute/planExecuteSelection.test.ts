import { describe, expect, it } from 'vitest';
import { rangeSelectIds, sortDevicesStable } from './planExecuteSelection';
import type { ReadinessDevice } from '@/utils/planExecuteReadiness';

const hosts = new Map([
  ['h2', { ip: '172.21.8.192' }],
  ['h1', { ip: '172.21.8.143' }],
]);

function d(partial: Partial<ReadinessDevice> & { id: number; serial: string }): ReadinessDevice {
  return { status: 'ONLINE', ...partial };
}

describe('planExecuteSelection', () => {
  it('sorts by host IP then serial then id', () => {
    const ordered = sortDevicesStable([
      d({ id: 3, serial: 'b', host_id: 'h2' }),
      d({ id: 1, serial: 'a', host_id: 'h1' }),
      d({ id: 2, serial: 'B', host_id: 'h1' }),
      d({ id: 4, serial: 'z', host_id: 'unassigned' }),
    ], hosts);
    expect(ordered.map((x) => x.id)).toEqual([1, 2, 3, 4]);
  });

  it('builds inclusive shift range ids', () => {
    const ordered = [
      d({ id: 10, serial: 'a', host_id: 'h1' }),
      d({ id: 11, serial: 'b', host_id: 'h1' }),
      d({ id: 12, serial: 'c', host_id: 'h1' }),
    ];
    expect(rangeSelectIds(ordered, 2, 0)).toEqual([10, 11, 12]);
  });
});
