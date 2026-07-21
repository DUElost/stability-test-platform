export type ExecutePhase = 'plan' | 'select' | 'dispatch';
export type DeviceViewMode = 'matrix' | 'table';
export type DeviceTileStatus = 'ready' | 'blocked' | 'busy' | 'offline';

export interface PlanExecuteDraftV2 {
  planId: number | null;
  deviceIds: number[];
  phase: ExecutePhase;
  view: DeviceViewMode;
  deviceFilter: string;
  deviceVersionFilter: string;
  deviceHostFilter: string;
  deviceModelFilter: string;
  deviceTagFilter: string[];
  readyOnly?: boolean;
}

export const EXECUTE_PHASES: Array<{ id: ExecutePhase; title: string; description: string }> = [
  { id: 'plan', title: '计划', description: '选择并核对测试计划' },
  { id: 'select', title: '选机', description: '先定位节点，再选择设备' },
  { id: 'dispatch', title: '发起', description: '前置项、参数与最终预检' },
];

export const PHASE_ORDER: ExecutePhase[] = ['plan', 'select', 'dispatch'];

export function phaseIndex(phase: ExecutePhase): number {
  return PHASE_ORDER.indexOf(phase);
}

export function migrateStepToPhase(currentStep: number): ExecutePhase {
  if (currentStep <= 0) return 'plan';
  if (currentStep === 1 || currentStep === 2) return 'select';
  return 'dispatch';
}
