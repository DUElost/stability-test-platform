import { describe, expect, it } from 'vitest';
import { groupPlansForSelect } from './planExecutePlanOptions';
import type { Plan } from '@/utils/api';

function plan(partial: Partial<Plan> & { id: number; name: string }): Plan {
  return {
    description: null,
    steps: [],
    failure_threshold: 0.05,
    patrol_interval_seconds: null,
    timeout_seconds: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...partial,
  } as Plan;
}

describe('groupPlansForSelect', () => {
  const plans = [
    plan({ id: 1, name: 'Alpha', updated_at: '2026-07-01T10:00:00Z' }),
    plan({ id: 2, name: 'Beta', updated_at: '2026-07-10T10:00:00Z' }),
    plan({ id: 3, name: 'Gamma', updated_at: '2026-07-05T10:00:00Z' }),
  ];

  it('puts recently executed plans first and excludes them from all', () => {
    const groups = groupPlansForSelect(
      plans,
      [
        { id: 100, plan_id: 3, started_at: '2026-07-20T12:00:00Z' },
        { id: 99, plan_id: 1, started_at: '2026-07-19T12:00:00Z' },
        { id: 98, plan_id: 3, started_at: '2026-07-18T12:00:00Z' },
      ],
      '',
      5,
    );
    expect(groups.recent.map((p) => p.id)).toEqual([3, 1]);
    expect(groups.all.map((p) => p.id)).toEqual([2]);
  });

  it('sorts remaining by updated_at desc', () => {
    const groups = groupPlansForSelect(plans, [], '');
    expect(groups.recent).toEqual([]);
    expect(groups.all.map((p) => p.id)).toEqual([2, 3, 1]);
  });

  it('applies keyword filter before grouping', () => {
    const groups = groupPlansForSelect(
      plans,
      [{ id: 1, plan_id: 1, started_at: '2026-07-20T00:00:00Z' }],
      'gam',
    );
    expect(groups.recent.map((p) => p.id)).toEqual([]);
    expect(groups.all.map((p) => p.name)).toEqual(['Gamma']);
  });
});
