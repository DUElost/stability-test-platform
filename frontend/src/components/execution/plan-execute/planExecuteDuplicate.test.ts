import { describe, expect, it } from 'vitest';
import type { PlanRun } from '@/utils/api';
import {
  computeOverlap,
  extractDispatchDeviceIds,
  estimateDeviceCount,
  findDuplicateMatch,
  isStrongDuplicate,
  isWeakDeviceCountMatch,
  isWithinWindow,
  pickRunsNeedingDeviceFetch,
  DUPLICATE_WINDOW_MS,
} from './planExecuteDuplicate';

function run(partial: Partial<PlanRun> & { id: number; started_at: string }): PlanRun {
  return {
    plan_id: 1,
    status: 'RUNNING',
    failure_threshold: 0.05,
    run_type: 'MANUAL',
    ...partial,
  };
}

describe('planExecuteDuplicate', () => {
  const now = Date.parse('2026-07-21T10:00:00.000Z');

  it('detects window membership by started_at', () => {
    expect(isWithinWindow('2026-07-21T09:45:00.000Z', now)).toBe(true);
    expect(isWithinWindow('2026-07-21T09:29:59.000Z', now)).toBe(false);
    expect(isWithinWindow('2026-07-21T10:01:00.000Z', now)).toBe(false);
    expect(isWithinWindow(null, now)).toBe(false);
  });

  it('extracts dispatch device ids and estimates count', () => {
    const withIds = run({
      id: 1,
      started_at: '2026-07-21T09:50:00.000Z',
      run_context: { dispatch_device_ids: [1, 2, 3] },
    });
    expect(extractDispatchDeviceIds(withIds)).toEqual([1, 2, 3]);
    expect(estimateDeviceCount(withIds)).toBe(3);

    const withSummary = run({
      id: 2,
      started_at: '2026-07-21T09:50:00.000Z',
      result_summary: { total: 12 },
    });
    expect(extractDispatchDeviceIds(withSummary)).toBeNull();
    expect(estimateDeviceCount(withSummary)).toBe(12);
  });

  it('computes overlap ratio against selected set', () => {
    expect(computeOverlap([1, 2, 3, 4], [2, 3, 9])).toEqual({ intersection: 2, ratio: 0.5 });
    expect(computeOverlap([], [1])).toEqual({ intersection: 0, ratio: 0 });
  });

  it('applies strong threshold ≥0.5 and intersection ≥3', () => {
    expect(isStrongDuplicate(6, 3, 0.5)).toBe(true);
    expect(isStrongDuplicate(6, 2, 0.5)).toBe(false);
    expect(isStrongDuplicate(10, 4, 0.4)).toBe(false);
  });

  it('matches weak device-count within ±20%', () => {
    expect(isWeakDeviceCountMatch(10, 8)).toBe(true);
    expect(isWeakDeviceCountMatch(10, 12)).toBe(true);
    expect(isWeakDeviceCountMatch(10, 7)).toBe(false);
  });

  it('prefers strong overlap over weak fallback', () => {
    const selected = [1, 2, 3, 4, 5, 6];
    const match = findDuplicateMatch(selected, [
      {
        run: run({
          id: 10,
          started_at: '2026-07-21T09:55:00.000Z',
          status: 'RUNNING',
          result_summary: { total: 6 },
        }),
        deviceIds: null,
      },
      {
        run: run({
          id: 11,
          started_at: '2026-07-21T09:40:00.000Z',
          status: 'SUCCESS',
        }),
        deviceIds: [1, 2, 3, 9],
      },
    ], now);
    expect(match?.kind).toBe('overlap');
    expect(match?.runId).toBe(11);
    expect(match?.overlapCount).toBe(3);
  });

  it('falls back to weak tip when device ids missing', () => {
    const match = findDuplicateMatch([1, 2, 3, 4, 5], [
      {
        run: run({
          id: 20,
          started_at: '2026-07-21T09:50:00.000Z',
          status: 'RUNNING',
          result_summary: { total: 5 },
        }),
        deviceIds: null,
      },
    ], now);
    expect(match).toEqual({
      kind: 'weak',
      runId: 20,
      startedAt: '2026-07-21T09:50:00.000Z',
      status: 'RUNNING',
      deviceCount: 5,
    });
  });

  it('ignores candidates outside the 30min window', () => {
    const old = new Date(now - DUPLICATE_WINDOW_MS - 1000).toISOString();
    const match = findDuplicateMatch([1, 2, 3, 4], [
      {
        run: run({ id: 30, started_at: old, run_context: { dispatch_device_ids: [1, 2, 3, 4] } }),
        deviceIds: [1, 2, 3, 4],
      },
    ], now);
    expect(match).toBeNull();
  });

  it('picks up to 3 recent window runs missing device ids', () => {
    const ids = pickRunsNeedingDeviceFetch([
      run({ id: 1, started_at: '2026-07-21T09:59:00.000Z' }),
      run({ id: 2, started_at: '2026-07-21T09:58:00.000Z', run_context: { dispatch_device_ids: [1] } }),
      run({ id: 3, started_at: '2026-07-21T09:57:00.000Z' }),
      run({ id: 4, started_at: '2026-07-21T09:56:00.000Z' }),
      run({ id: 5, started_at: '2026-07-21T09:55:00.000Z' }),
      run({ id: 6, started_at: '2026-07-20T09:00:00.000Z' }),
    ], now);
    expect(ids).toEqual([1, 3, 4]);
  });
});
