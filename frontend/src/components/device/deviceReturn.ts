/** ADR-0061 D1/D5 展示口径（#3723 U6）。
 *
 * 机器值 → 界面用语集中在这里：解除结果六值、专项归还状态、来源、
 * 设备级聚合。未知值一律原样返回，调用方不得抛错（激活前的兼容空态）。
 */

export type ReturnStateEntry = {
  state: string;
  source?: string;
  updated_at?: string;
  [key: string]: unknown;
};

export const DISARM_RESULT_LABELS: Record<string, string> = {
  confirmed: '已确认',
  executed_unverified: '已执行未验证',
  failed: '失败',
  readback_failed: '回读失败',
  not_executed: '未执行',
  incomplete: '未完成',
};

export function disarmResultLabel(value: string): string {
  return DISARM_RESULT_LABELS[value] ?? value;
}

/** 已登记专项（ADR-0061 D1.7 当前五个）；未知 family 保留机器值展示。 */
export const REGISTERED_FAMILIES = ['power-cycle', 'sleep', 'mtbf', 'gpu', 'monkey'] as const;

export const FAMILY_LABELS: Record<string, string> = {
  'power-cycle': '开关机',
  sleep: '休眠',
  mtbf: 'MTBF',
  gpu: 'GPU',
  monkey: 'Monkey',
};

export function familyLabel(family: string): string {
  return FAMILY_LABELS[family] ?? family;
}

export const FAMILY_RETURN_STATE_LABELS: Record<string, string> = {
  returned: '已确认',
  debt: '欠账',
  unverified: '未验证',
};

export function familyReturnStateLabel(value: string): string {
  return FAMILY_RETURN_STATE_LABELS[value] ?? value;
}

/** 专项归还状态的来源（ADR-0061 D1.5）。 */
export const RETURN_SOURCE_LABELS: Record<string, string> = {
  job: 'job',
  init_check: 'init 检查',
  discharge: '清偿',
  override: '人工放行',
  stock: '存量初始化',
};

export function returnSourceLabel(value: string | null | undefined): string {
  if (!value) return '—';
  return RETURN_SOURCE_LABELS[value] ?? value;
}

/** 欠账条目的来源类型：job 或阶段 B 存量初始化。 */
export const DEBT_SOURCE_KIND_LABELS: Record<string, string> = {
  job: 'job',
  stock: '存量初始化',
};

export function debtSourceKindLabel(value: string): string {
  return DEBT_SOURCE_KIND_LABELS[value] ?? value;
}

export type DeviceReturnAggregate = 'uninitialized' | 'debt' | 'unverified' | 'returned';

/**
 * 设备级归还状态：欠账 > 未验证 > 已归还（ADR-0061 D1.4）。
 *
 * return_state 为空 = 未初始化（阶段 B 激活前），不得推断为已归还；
 * 任一已登记专项没有记录时按未验证处理（后端 device_aggregate_return_state 同口径）。
 */
export function deviceReturnAggregate(
  returnState?: Record<string, ReturnStateEntry> | null,
): DeviceReturnAggregate {
  if (!returnState || Object.keys(returnState).length === 0) return 'uninitialized';
  const states = REGISTERED_FAMILIES.map((family) => returnState[family]?.state ?? 'unverified');
  if (states.includes('debt')) return 'debt';
  if (states.some((state) => state !== 'returned')) return 'unverified';
  return 'returned';
}

export const DEVICE_RETURN_AGGREGATE_LABELS: Record<DeviceReturnAggregate, string> = {
  uninitialized: '未初始化',
  debt: '欠账',
  unverified: '未验证',
  returned: '已归还',
};

/** 人工放行原因类别（下拉固定四值，ADR-0061 D5.7 / #3723 U6）。 */
export const OVERRIDE_REASON_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'flashed', label: '已刷机' },
  { value: 'factory_reset', label: '已恢复出厂' },
  { value: 'manual_verified', label: '人工确认已停止' },
  { value: 'other', label: '其它' },
];
