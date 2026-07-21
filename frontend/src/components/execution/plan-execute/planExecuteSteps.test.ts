import { describe, expect, it } from 'vitest';
import { groupPlanStepsByStage, stageChipClass } from './planExecuteSteps';

describe('groupPlanStepsByStage', () => {
  it('groups in init → patrol → teardown order', () => {
    const groups = groupPlanStepsByStage([
      { step_key: 't1', stage: 'teardown', sort_order: 1 },
      { step_key: 'i1', stage: 'init', sort_order: 2 },
      { step_key: 'p1', stage: 'patrol', sort_order: 1 },
      { step_key: 'i0', stage: 'init', sort_order: 1 },
    ]);
    expect(groups.map((g) => g.stage)).toEqual(['init', 'patrol', 'teardown']);
    expect(groups[0].steps.map((s) => s.step_key)).toEqual(['i0', 'i1']);
  });

  it('appends unknown stages after known ones', () => {
    const groups = groupPlanStepsByStage([
      { step_key: 'x', stage: 'custom' },
      { step_key: 'i', stage: 'init' },
    ]);
    expect(groups.map((g) => g.stage)).toEqual(['init', 'custom']);
  });

  it('returns empty for missing steps', () => {
    expect(groupPlanStepsByStage(undefined)).toEqual([]);
    expect(groupPlanStepsByStage([])).toEqual([]);
  });

  it('exposes chip classes for known stages', () => {
    expect(stageChipClass('init')).toContain('bg-info');
    expect(stageChipClass('patrol')).toContain('bg-warning');
    expect(stageChipClass('teardown')).toContain('bg-muted');
  });
});
