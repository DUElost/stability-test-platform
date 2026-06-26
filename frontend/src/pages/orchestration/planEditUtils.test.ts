import { describe, expect, it } from 'vitest';
import type { Plan, PipelineDef } from '@/utils/api';
import {
  EMPTY_LIFECYCLE,
  buildStepsForApi,
  findStepInLifecycle,
  rebuildLifecycleFromPlan,
} from './planEditUtils';

const basePlan: Plan = {
  id: 1,
  name: 'Smoke',
  description: 'desc',
  failure_threshold: 0.1,
  patrol_interval_seconds: 120,
  timeout_seconds: 3600,
  next_plan_id: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  steps: [
    {
      id: 1,
      plan_id: 1,
      step_key: 'step_init_1',
      script_name: 'check_device',
      script_version: '1.0.0',
      stage: 'init',
      sort_order: 0,
      timeout_seconds: 30,
      retry: 0,
      enabled: true,
    },
    {
      id: 2,
      plan_id: 1,
      step_key: 'step_patrol_1',
      script_name: 'monkey',
      script_version: '2.0.0',
      stage: 'patrol',
      sort_order: 0,
      timeout_seconds: 60,
      retry: 1,
      enabled: true,
    },
    {
      id: 3,
      plan_id: 1,
      step_key: 'step_teardown_1',
      script_name: 'cleanup',
      script_version: '1.0.0',
      stage: 'teardown',
      sort_order: 0,
      timeout_seconds: 20,
      retry: 0,
      enabled: false,
    },
  ],
};

describe('planEditUtils', () => {
  it('rebuildLifecycleFromPlan groups steps by stage and preserves patrol interval', () => {
    const lc = rebuildLifecycleFromPlan(basePlan);

    expect(lc.lifecycle.init).toHaveLength(1);
    expect(lc.lifecycle.init![0].action).toBe('script:check_device');
    expect(lc.lifecycle.patrol?.interval_seconds).toBe(120);
    expect(lc.lifecycle.patrol?.steps).toHaveLength(1);
    expect(lc.lifecycle.patrol?.steps![0].action).toBe('script:monkey');
    expect(lc.lifecycle.teardown).toHaveLength(1);
    expect(lc.lifecycle.teardown![0].enabled).toBe(false);
    expect(lc.lifecycle.timeout_seconds).toBe(3600);
  });

  it('buildStepsForApi emits PlanStepCreate rows with script: prefix stripped', () => {
    const lc: PipelineDef = {
      lifecycle: {
        init: [
          {
            step_id: 'step_init_1',
            action: 'script:check_device',
            version: '1.0.0',
            params: {},
            timeout_seconds: 30,
            retry: 0,
            enabled: true,
          },
        ],
        patrol: {
          interval_seconds: 90,
          steps: [
            {
              step_id: 'step_patrol_1',
              action: 'script:monkey',
              version: '2.0.0',
              params: {},
              timeout_seconds: 60,
              retry: 1,
              enabled: true,
            },
          ],
        },
        teardown: [],
      },
    };

    const steps = buildStepsForApi(lc);
    expect(steps).toEqual([
      expect.objectContaining({
        step_key: 'step_init_1',
        script_name: 'check_device',
        stage: 'init',
        sort_order: 0,
      }),
      expect.objectContaining({
        step_key: 'step_patrol_1',
        script_name: 'monkey',
        stage: 'patrol',
        sort_order: 0,
        retry: 1,
      }),
    ]);
  });

  it('findStepInLifecycle locates steps across init, patrol, and teardown', () => {
    const lc = rebuildLifecycleFromPlan(basePlan);

    expect(findStepInLifecycle(lc, 'step_init_1')).toEqual({ phase: 'init', index: 0 });
    expect(findStepInLifecycle(lc, 'step_patrol_1')).toEqual({ phase: 'patrol', index: 0 });
    expect(findStepInLifecycle(lc, 'step_teardown_1')).toEqual({ phase: 'teardown', index: 0 });
    expect(findStepInLifecycle(lc, null)).toEqual({ phase: null, index: -1 });
    expect(findStepInLifecycle(lc, 'missing')).toEqual({ phase: null, index: -1 });
  });

  it('EMPTY_LIFECYCLE seeds a default init step', () => {
    expect(EMPTY_LIFECYCLE.lifecycle.init).toHaveLength(1);
    expect(EMPTY_LIFECYCLE.lifecycle.init![0].action).toBe('script:check_device');
  });
});
