import type { InventoryModel, ProjectModelCoverage } from '@/utils/api/types';

export const LEGACY_PROJECT_KEY = 'LEGACY';

export function formatModelLabel(model: string | null | undefined): string {
  return model?.trim() ? model : '（无型号）';
}

export function unassignedDeviceCount(row: InventoryModel): number {
  return row.legacy_device_count + row.null_device_count;
}

/** 回填标签里的真实 key（不含 LEGACY）；不是人工映射。 */
export function backfillLabelKeys(row: InventoryModel): string[] {
  return row.backfill_project_keys.filter((key) => key !== LEGACY_PROJECT_KEY);
}

export function hasManualMapping(row: InventoryModel): boolean {
  return row.mapped_project_keys.length > 0;
}

export function coverageSummary(rows: ProjectModelCoverage[]): string {
  if (rows.length === 0) return '';
  return rows
    .map((row) => `${formatModelLabel(row.model)} (${row.device_count})`)
    .join(' · ');
}
