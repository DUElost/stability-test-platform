import { describe, expect, it } from 'vitest';
import {
  sortDevicesByColumn,
  toggleDeviceTableSort,
} from './planExecuteTableSort';
import type { ReadinessDevice } from '@/utils/planExecuteReadiness';

const hostMap = new Map([
  ['h1', { ip: '10.0.0.1', name: 'rack-a' }],
  ['h2', { ip: '10.0.0.2', name: 'rack-b' }],
]);

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

describe('planExecuteTableSort', () => {
  it('toggles direction on same key and resets on new key', () => {
    expect(toggleDeviceTableSort(null, 'serial')).toEqual({ key: 'serial', dir: 'asc' });
    expect(toggleDeviceTableSort({ key: 'serial', dir: 'asc' }, 'serial')).toEqual({
      key: 'serial',
      dir: 'desc',
    });
    expect(toggleDeviceTableSort({ key: 'serial', dir: 'desc' }, 'model')).toEqual({
      key: 'model',
      dir: 'asc',
    });
  });

  it('sorts by version with numeric awareness', () => {
    const devices = [
      device({ id: 1, serial: 'a', build_display_id: 'V10' }),
      device({ id: 2, serial: 'b', build_display_id: 'V2' }),
      device({ id: 3, serial: 'c', build_display_id: 'V9' }),
    ];
    const asc = sortDevicesByColumn(devices, { key: 'version', dir: 'asc' }, hostMap);
    expect(asc.map((d) => d.build_display_id)).toEqual(['V2', 'V9', 'V10']);
  });

  it('sorts by host label', () => {
    const devices = [
      device({ id: 1, serial: 'z', host_id: 'h2' }),
      device({ id: 2, serial: 'a', host_id: 'h1' }),
    ];
    const asc = sortDevicesByColumn(devices, { key: 'host', dir: 'asc' }, hostMap);
    expect(asc.map((d) => d.host_id)).toEqual(['h1', 'h2']);
  });
});
