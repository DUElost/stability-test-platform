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

/** 编辑表单草稿的统一快照形状（isDirty 与远端变更比对共用，键序固定）。 */
export interface PlanFormDraft {
  name: string;
  description: string;
  failureThreshold: number;
  nextPlanId: number | null;
  projectKey: string;
  specialtyKey: string;
  suiteName: string;
  lifecycle: PipelineDef;
}

/**
 * 草稿快照：所有可编辑业务字段（含归属项目/专项/套件绑定，#966）。
 * orig 与 current 必须走同一函数，保证键序一致、脏检查稳定。
 */
export function draftSnapshot(draft: PlanFormDraft): string {
  return snapshot({
    name: draft.name,
    description: draft.description,
    failureThreshold: draft.failureThreshold,
    nextPlanId: draft.nextPlanId,
    projectKey: draft.projectKey,
    specialtyKey: draft.specialtyKey,
    suiteName: draft.suiteName,
    lifecycle: draft.lifecycle,
  });
}

/** 由远端 Plan 行构造同形状草稿快照（远端变更比对，#967）。 */
export function planDraftSnapshot(plan: Plan): string {
  return draftSnapshot({
    name: plan.name,
    description: plan.description || '',
    failureThreshold: plan.failure_threshold,
    nextPlanId: plan.next_plan_id ?? null,
    projectKey: plan.project_key || '',
    specialtyKey: plan.specialty_key || '',
    suiteName: plan.suite_name || '',
    lifecycle: rebuildLifecycleFromPlan(plan),
  });
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
