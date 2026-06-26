import type {
  Plan,
  PipelineDef,
  PipelinePhase,
  PipelineStep,
  PlanStepCreate,
} from '@/utils/api';

export const EMPTY_LIFECYCLE: PipelineDef = {
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
    teardown: [],
  },
};

export function snapshot(value: unknown): string {
  return JSON.stringify(value);
}

export function findStepInLifecycle(lc: PipelineDef, stepKey: string | null) {
  if (!stepKey) return { phase: null as PipelinePhase | null, index: -1 as number };
  const lifecycle = lc.lifecycle;
  const sources: Array<[PipelinePhase, PipelineStep[]]> = [
    ['init', lifecycle.init ?? []],
    ['patrol', lifecycle.patrol?.steps ?? []],
    ['teardown', lifecycle.teardown ?? []],
  ];
  for (const [phase, steps] of sources) {
    const idx = steps.findIndex((s) => s.step_id === stepKey);
    if (idx >= 0) return { phase, index: idx };
  }
  return { phase: null as PipelinePhase | null, index: -1 };
}

export function rebuildLifecycleFromPlan(plan: Plan): PipelineDef {
  const init: PipelineStep[] = [];
  const patrol: PipelineStep[] = [];
  const teardown: PipelineStep[] = [];

  const sorted = [...(plan.steps || [])].sort(
    (a, b) => a.stage.localeCompare(b.stage) || a.sort_order - b.sort_order,
  );

  for (const s of sorted) {
    const stepDef: PipelineStep = {
      step_id: s.step_key,
      action: `script:${s.script_name}`,
      version: s.script_version,
      params: {},
      timeout_seconds: s.timeout_seconds ?? 30,
      retry: s.retry ?? 0,
      enabled: s.enabled !== false,
    };
    if (s.stage === 'init') init.push(stepDef);
    else if (s.stage === 'patrol') patrol.push(stepDef);
    else teardown.push(stepDef);
  }

  return {
    lifecycle: {
      init,
      patrol: patrol.length
        ? { interval_seconds: plan.patrol_interval_seconds ?? 60, steps: patrol }
        : undefined,
      teardown,
      timeout_seconds: plan.timeout_seconds ?? undefined,
    },
  };
}

export function buildStepsForApi(lifecycle: PipelineDef): PlanStepCreate[] {
  const out: PlanStepCreate[] = [];
  const lc = lifecycle.lifecycle;
  const append = (phase: 'init' | 'patrol' | 'teardown', steps: PipelineStep[]) => {
    steps.forEach((s, i) => {
      const action = s.action || '';
      const scriptName = action.startsWith('script:') ? action.slice(7) : '';
      out.push({
        step_key: s.step_id || `step_${phase}_${i}`,
        script_name: scriptName,
        script_version: s.version || '',
        stage: phase,
        sort_order: i,
        timeout_seconds: s.timeout_seconds ?? null,
        retry: s.retry ?? 0,
        enabled: s.enabled !== false,
      });
    });
  };
  if (lc.init) append('init', lc.init);
  if (lc.patrol?.steps) append('patrol', lc.patrol.steps);
  if (lc.teardown) append('teardown', lc.teardown);
  return out;
}
