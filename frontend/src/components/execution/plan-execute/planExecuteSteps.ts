/** Group Plan steps by stage for the plan-select phase UI. */

export type PlanStepStage = 'init' | 'patrol' | 'teardown' | string;

export interface PlanStepLike {
  id?: number;
  step_key?: string;
  script_name?: string;
  script_version?: string;
  stage?: PlanStepStage | null;
  sort_order?: number | null;
  enabled?: boolean | null;
}

export interface PlanStepStageGroup {
  stage: PlanStepStage;
  label: string;
  steps: PlanStepLike[];
}

export const PLAN_STAGE_ORDER: PlanStepStage[] = ['init', 'patrol', 'teardown'];

export const PLAN_STAGE_LABEL: Record<string, string> = {
  init: 'init',
  patrol: 'patrol',
  teardown: 'teardown',
};

/** Chip classes aligned with EVENT_STAGE_CHIP / mockup stage tags. */
export const PLAN_STAGE_CHIP_CLASS: Record<string, string> = {
  init: 'border-info/20 bg-info/10 text-info',
  patrol: 'border-warning/20 bg-warning/10 text-warning',
  teardown: 'border-border bg-muted text-muted-foreground',
};

export function stageChipClass(stage: string): string {
  return PLAN_STAGE_CHIP_CLASS[stage] ?? 'border-border bg-muted/80 text-muted-foreground';
}

/**
 * Group steps by stage. Known stages keep init→patrol→teardown order;
 * unknown stages append alphabetically. Within a group, prefer sort_order then original index.
 */
export function groupPlanStepsByStage(steps: PlanStepLike[] | null | undefined): PlanStepStageGroup[] {
  if (!steps?.length) return [];

  const indexed = steps.map((step, index) => ({ step, index }));
  const map = new Map<string, typeof indexed>();

  for (const entry of indexed) {
    const stage = (entry.step.stage && String(entry.step.stage).trim()) || 'unknown';
    const bucket = map.get(stage) ?? [];
    bucket.push(entry);
    map.set(stage, bucket);
  }

  const known = PLAN_STAGE_ORDER.filter((s) => map.has(s));
  const unknown = Array.from(map.keys())
    .filter((s) => !PLAN_STAGE_ORDER.includes(s))
    .sort((a, b) => a.localeCompare(b));

  return [...known, ...unknown].map((stage) => {
    const entries = map.get(stage) ?? [];
    entries.sort((a, b) => {
      const ao = a.step.sort_order;
      const bo = b.step.sort_order;
      if (typeof ao === 'number' && typeof bo === 'number' && ao !== bo) return ao - bo;
      if (typeof ao === 'number' && typeof bo !== 'number') return -1;
      if (typeof ao !== 'number' && typeof bo === 'number') return 1;
      return a.index - b.index;
    });
    return {
      stage,
      label: PLAN_STAGE_LABEL[stage] ?? stage,
      steps: entries.map((e) => e.step),
    };
  });
}
