import { describe, expect, it } from 'vitest';
import { applyMatrixSelection } from './DeviceMatrix';
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

describe('applyMatrixSelection', () => {
  const ordered = [
    device({ id: 1, serial: 'a' }),
    device({ id: 2, serial: 'b' }),
    device({ id: 3, serial: 'c', status: 'BUSY' }),
    device({ id: 4, serial: 'd' }),
  ];

  it('toggles a single schedulable device', () => {
    const next = applyMatrixSelection(ordered, new Set(), ordered[0], { shiftKey: false }, null);
    expect(next.has(1)).toBe(true);
    const again = applyMatrixSelection(ordered, next, ordered[0], { shiftKey: false }, null);
    expect(again.has(1)).toBe(false);
  });

  it('shift-selects a range of schedulable devices only', () => {
    const next = applyMatrixSelection(ordered, new Set(), ordered[3], { shiftKey: true }, 0);
    expect(Array.from(next).sort()).toEqual([1, 2, 4]);
  });

  it('ignores unknown devices', () => {
    const unknown = device({ id: 99, serial: 'z' });
    const prev = new Set([1]);
    expect(applyMatrixSelection(ordered, prev, unknown, { shiftKey: false }, null)).toBe(prev);
  });
});
