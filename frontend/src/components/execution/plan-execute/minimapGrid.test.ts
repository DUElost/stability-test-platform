import { describe, expect, it } from 'vitest';
import {
  defaultMinimapColumns,
  measureMinimapColumns,
  MINIMAP_EMBEDDED_RAIL_INNER_WIDTH,
} from './minimapGrid';

describe('measureMinimapColumns', () => {
  it('returns 0 when width is not yet measurable', () => {
    expect(measureMinimapColumns(0, 22)).toBe(0);
  });

  it('fits multiple fixed-width columns in embedded rail width', () => {
    const cols = measureMinimapColumns(MINIMAP_EMBEDDED_RAIL_INNER_WIDTH, 22);
    expect(cols).toBeGreaterThanOrEqual(8);
  });

  it('defaults embedded layout to a multi-column estimate', () => {
    expect(defaultMinimapColumns(true, 22)).toBeGreaterThanOrEqual(8);
  });
});
