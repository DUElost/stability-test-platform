import { describe, expect, it } from 'vitest';

import { formatNaiveLocalDateTime } from './time';

/**
 * #2358：后端 naive 时间戳是**本地墙上时间**（PG 会话时区落库），必须原样展示。
 *
 * 反例（修复前）：用户管理页走 `formatDateTimeFull` → `parseIsoToDate` 把 naive 当
 * UTC（`${value}Z`）再转本地 → 平白 +8，显示成「未来时间」。本用例与运行环境的 TZ
 * 无关——正确实现不做任何时区换算。
 */
describe('formatNaiveLocalDateTime', () => {
  it('naive 值原样展示（不做时区换算）', () => {
    expect(formatNaiveLocalDateTime('2026-09-16T12:10:36')).toBe('2026-09-16 12:10:36');
    expect(formatNaiveLocalDateTime('2026-08-18T13:48:36.401178')).toBe('2026-08-18 13:48:36');
  });

  it('空值回落占位符', () => {
    expect(formatNaiveLocalDateTime(null)).toBe('-');
    expect(formatNaiveLocalDateTime(undefined)).toBe('-');
    expect(formatNaiveLocalDateTime('')).toBe('-');
    expect(formatNaiveLocalDateTime(null, '—')).toBe('—');
  });

  it('守卫：带时区的值不得走这条路径（本函数不平移时区）', () => {
    // 说明性用例：本函数按字面截断，绝不用于 aware 值（那种情形用 formatLocalDateTime）
    expect(formatNaiveLocalDateTime('2026-09-16T12:10:36Z')).toBe('2026-09-16 12:10:36');
  });
});
