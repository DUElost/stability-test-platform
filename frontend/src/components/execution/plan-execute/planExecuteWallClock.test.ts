import { describe, expect, it } from 'vitest';
import type { PlanRun } from '@/utils/api';
import { estimatePlanWallClock } from './planExecuteWallClock';

function run(overrides: Partial<PlanRun>): PlanRun {
  return {
    id: 1,
    plan_id: 7,
    status: 'SUCCESS',
    failure_threshold: 0.05,
    run_type: 'MANUAL',
    started_at: '2026-07-20T00:00:00Z',
    ended_at: '2026-07-20T02:00:00Z',
    ...overrides,
  };
}

describe('estimatePlanWallClock', () => {
  it('averages up to five positive terminal run durations', () => {
    const result = estimatePlanWallClock([
      run({ id: 1, ended_at: '2026-07-20T02:00:00Z' }),
      run({ id: 2, status: 'FAILED', ended_at: '2026-07-20T04:00:00Z' }),
      run({ id: 3, status: 'RUNNING', ended_at: null }),
    ]);

    expect(result).toEqual({ averageSeconds: 10_800, sampleCount: 2 });
  });

  it('returns no estimate when fewer than two valid samples exist', () => {
    expect(estimatePlanWallClock([
      run({ id: 1 }),
      run({ id: 2, ended_at: '2026-07-19T23:00:00Z' }),
      run({ id: 3, status: 'RUNNING', ended_at: null }),
    ])).toEqual({ averageSeconds: null, sampleCount: 1 });
  });
});
