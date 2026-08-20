import { describe, expect, it } from 'vitest';
import type { InventoryModel } from '@/utils/api/types';
import {
  coverageSummary,
  formatModelLabel,
  isMapped,
  selectableModel,
} from './inventoryDisplay';

function row(overrides: Partial<InventoryModel> = {}): InventoryModel {
  return {
    model: 'MLD_LX2',
    device_count: 10,
    platforms: ['MTK'],
    mapped_project_keys: [],
    unassigned_device_count: 10,
    ...overrides,
  };
}

describe('inventoryDisplay', () => {
  it('formats blank models', () => {
    expect(formatModelLabel('MLD_LX2')).toBe('MLD_LX2');
    expect(formatModelLabel(null)).toBe('（无型号）');
  });

  it('treats empty mapped keys as unmapped', () => {
    expect(isMapped(row())).toBe(false);
    expect(isMapped(row({ mapped_project_keys: ['HONOR-CAMERA'] }))).toBe(true);
  });

  it('does not select blank models for mapping', () => {
    expect(selectableModel(row({ model: null }))).toBeNull();
    expect(selectableModel(row({ model: '  ' }))).toBeNull();
    expect(selectableModel(row())).toBe('MLD_LX2');
  });

  it('summarizes hanging models', () => {
    expect(
      coverageSummary([
        { model: 'MLD_LX2', device_count: 260, platforms: ['MTK'] },
        { model: 'MLD_LX3', device_count: 32, platforms: ['MTK'] },
      ]),
    ).toBe('MLD_LX2 (260) · MLD_LX3 (32)');
  });
});
