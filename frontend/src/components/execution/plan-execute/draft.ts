import { migrateStepToPhase, type DeviceViewMode, type ExecutePhase, type PlanExecuteDraftV2 } from './types';

export const DRAFT_KEY_V1 = 'stp.planExecute.draft.v1';
export const DRAFT_KEY_V2 = 'stp.planExecute.draft.v2';

function parseView(value: unknown): DeviceViewMode {
  return value === 'table' ? 'table' : 'matrix';
}

function parsePhase(value: unknown): ExecutePhase {
  if (value === 'plan' || value === 'select' || value === 'dispatch') return value;
  return 'plan';
}

function normalizeDraft(parsed: Partial<PlanExecuteDraftV2> & { currentStep?: number }): PlanExecuteDraftV2 {
  const phase = parsed.phase
    ? parsePhase(parsed.phase)
    : Number.isInteger(parsed.currentStep)
      ? migrateStepToPhase(Number(parsed.currentStep))
      : 'plan';
  return {
    planId: typeof parsed.planId === 'number' ? parsed.planId : null,
    deviceIds: Array.isArray(parsed.deviceIds)
      ? parsed.deviceIds.filter((id): id is number => Number.isInteger(id) && id > 0)
      : [],
    phase,
    view: parseView(parsed.view),
    deviceFilter: typeof parsed.deviceFilter === 'string' ? parsed.deviceFilter : '',
    deviceVersionFilter: typeof parsed.deviceVersionFilter === 'string' ? parsed.deviceVersionFilter : 'all',
    deviceHostFilter: typeof parsed.deviceHostFilter === 'string' ? parsed.deviceHostFilter : 'all',
    deviceModelFilter: typeof parsed.deviceModelFilter === 'string' ? parsed.deviceModelFilter : 'all',
    deviceTagFilter: Array.isArray(parsed.deviceTagFilter)
      ? parsed.deviceTagFilter.filter((tag): tag is string => typeof tag === 'string')
      : [],
    readyOnly: Boolean(parsed.readyOnly),
  };
}

export function loadPlanExecuteDraft(): PlanExecuteDraftV2 | null {
  try {
    const rawV2 = sessionStorage.getItem(DRAFT_KEY_V2);
    if (rawV2) {
      const parsed = JSON.parse(rawV2) as Partial<PlanExecuteDraftV2> | null;
      if (typeof parsed !== 'object' || parsed === null) return null;
      return normalizeDraft(parsed);
    }
    const rawV1 = sessionStorage.getItem(DRAFT_KEY_V1);
    if (!rawV1) return null;
    const parsed = JSON.parse(rawV1) as (Partial<PlanExecuteDraftV2> & { currentStep?: number }) | null;
    if (typeof parsed !== 'object' || parsed === null) return null;
    const migrated = normalizeDraft(parsed);
    try {
      sessionStorage.setItem(DRAFT_KEY_V2, JSON.stringify(migrated));
      sessionStorage.removeItem(DRAFT_KEY_V1);
    } catch { /* ignore */ }
    return migrated;
  } catch {
    return null;
  }
}

export function savePlanExecuteDraft(draft: PlanExecuteDraftV2): void {
  try {
    sessionStorage.setItem(DRAFT_KEY_V2, JSON.stringify(draft));
    sessionStorage.removeItem(DRAFT_KEY_V1);
  } catch { /* ignore */ }
}

export function removePlanExecuteDraft(): void {
  try {
    sessionStorage.removeItem(DRAFT_KEY_V2);
    sessionStorage.removeItem(DRAFT_KEY_V1);
  } catch { /* ignore */ }
}
