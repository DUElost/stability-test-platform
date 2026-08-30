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
      // #508 步骤级 params：读回（null/缺省 → 空对象，保持 snapshot() 脏检查稳定）
      params: s.params ?? {},
      timeout_seconds: s.timeout_seconds ?? 30,
      retry: s.retry ?? 0,
      enabled: s.enabled !== false,
      // 编辑器没有停滞钟输入框，但保存是整体替换 PlanStep 行：这里不读回来、
      // buildStepsForApi 不发回去，打开 Plan 点一次保存就把它清成 NULL。
      // 只在有值时写键，保持无停滞钟的 Plan 的 snapshot() 结果不变（脏检查依赖它）。
      ...(s.stall_seconds != null ? { stall_seconds: s.stall_seconds } : {}),
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
        stall_seconds: s.stall_seconds ?? null,
        // #508 步骤级 params：空对象不写键，避免既有 Plan 保存后多出
        // ``params: {}``（后端 NULL 语义 = 纯 default_params）。
        ...(s.params && Object.keys(s.params).length > 0 ? { params: s.params } : {}),
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
