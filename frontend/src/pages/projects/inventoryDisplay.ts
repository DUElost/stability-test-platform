import type { InventoryModel, ProjectModelCoverage } from '@/utils/api/types';

export function formatModelLabel(model: string | null | undefined): string {
  return model?.trim() ? model : '（无型号）';
}

export function isMapped(row: InventoryModel): boolean {
  return row.mapped_project_keys.length > 0;
}

export function selectableModel(row: InventoryModel): string | null {
  const model = row.model?.trim();
  return model || null;
}

export function coverageSummary(rows: ProjectModelCoverage[]): string {
  if (rows.length === 0) return '';
  return rows
    .map((row) => `${formatModelLabel(row.model)} (${row.device_count})`)
    .join(' · ');
}
