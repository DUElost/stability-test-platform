import { describe, expect, it } from 'vitest';
import type { InventoryModel } from '@/utils/api/types';
import {
  backfillLabelKeys,
  coverageSummary,
  formatModelLabel,
  hasManualMapping,
  unassignedDeviceCount,
} from './inventoryDisplay';

function row(overrides: Partial<InventoryModel> = {}): InventoryModel {
  return {
    model: 'MLD_LX2',
    device_count: 10,
    platforms: ['MTK'],
    backfill_project_keys: ['HONOR-MLD'],
    mapped_project_keys: [],
    legacy_device_count: 0,
    null_device_count: 0,
    ...overrides,
  };
}

describe('inventoryDisplay', () => {
  it('formats blank models', () => {
    expect(formatModelLabel('MLD_LX2')).toBe('MLD_LX2');
    expect(formatModelLabel(null)).toBe('（无型号）');
  });

  it('treats P1 backfill keys as informal labels, not mapping', () => {
    const item = row({
      backfill_project_keys: ['HONOR-MLD', 'LEGACY'],
      mapped_project_keys: [],
      legacy_device_count: 2,
    });
    expect(backfillLabelKeys(item)).toEqual(['HONOR-MLD']);
    expect(hasManualMapping(item)).toBe(false);
    expect(unassignedDeviceCount(item)).toBe(2);
  });

  it('summarizes hanging models without calling them a mapping', () => {
    expect(
      coverageSummary([
        { model: 'MLD_LX2', device_count: 260, platforms: ['MTK'] },
        { model: 'MLD_LX3', device_count: 32, platforms: ['MTK'] },
      ]),
    ).toBe('MLD_LX2 (260) · MLD_LX3 (32)');
  });
});
