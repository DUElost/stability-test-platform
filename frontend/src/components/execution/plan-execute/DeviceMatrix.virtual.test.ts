import { describe, expect, it } from 'vitest';
import { buildMatrixVirtualRows } from './DeviceMatrix';
import type { ReadinessDevice } from '@/utils/planExecuteReadiness';

function device(partial: Partial<ReadinessDevice> & { id: number; serial: string }): ReadinessDevice {
  return {
    model: null,
    build_display_id: null,
    host_id: 'h1',
    status: 'ONLINE',
    tags: [],
    ...partial,
  } as ReadinessDevice;
}

describe('buildMatrixVirtualRows', () => {
  it('inserts band headers and chunks tiles by column count', () => {
    const hostMap = new Map([
      ['h1', { ip: '10.0.0.1' }],
      ['h2', { ip: '10.0.0.2' }],
    ]);
    const devices = [
      device({ id: 1, serial: 'a', host_id: 'h1' }),
      device({ id: 2, serial: 'b', host_id: 'h1' }),
      device({ id: 3, serial: 'c', host_id: 'h1' }),
      device({ id: 4, serial: 'd', host_id: 'h2' }),
    ];
    const rows = buildMatrixVirtualRows(devices, hostMap, new Set([1]), 2);
    expect(rows[0]).toMatchObject({ type: 'band', hostId: 'h1', total: 3, selected: 1 });
    expect(rows[1]).toMatchObject({ type: 'tiles' });
    expect((rows[1] as { devices: ReadinessDevice[] }).devices.map((d) => d.id)).toEqual([1, 2]);
    expect((rows[2] as { devices: ReadinessDevice[] }).devices.map((d) => d.id)).toEqual([3]);
    expect(rows[3]).toMatchObject({ type: 'band', hostId: 'h2', total: 1, selected: 0 });
  });
});
