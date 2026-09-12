import { describe, it, expect } from 'vitest';
import { formatDateTimeLocale, formatDurationSeconds, formatUnixSeconds } from './format';
import { datetimeLocalInputToIso } from './time';

describe('formatDateTimeLocale', () => {
  it('合法 ISO 输出 zh-CN 24h 本地串', () => {
    expect(formatDateTimeLocale('2026-08-20T02:03:04Z', '')).toMatch(/2026/);
  });

  it('空值返回自定义 empty（通知流空时间戳显示空串而非 -）', () => {
    expect(formatDateTimeLocale(null, '')).toBe('');
    expect(formatDateTimeLocale(undefined, '-')).toBe('-');
  });
});

describe('formatDurationSeconds', () => {
  it('includes seconds in precise style when hours are present', () => {
    expect(formatDurationSeconds(3723, 'precise')).toBe('1h 2m 3s');
  });

  it('omits trailing seconds when zero at hour granularity', () => {
    expect(formatDurationSeconds(3600, 'precise')).toBe('1h 0m');
  });
});

describe('datetimeLocalInputToIso', () => {
  it('converts datetime-local values to UTC ISO strings', () => {
    const iso = datetimeLocalInputToIso('2026-06-01T10:00');
    expect(iso).toMatch(/2026-06-01T\d{2}:00:00\.000Z/);
  });
});

describe('formatUnixSeconds', () => {
  it('Unix 秒转本地串（Recharts labelFormatter 场景）', () => {
    expect(formatUnixSeconds(1760000000)).toMatch(/2025/);
  });

  it('非法/空输入返回 empty', () => {
    expect(formatUnixSeconds(null)).toBe('');
    expect(formatUnixSeconds(NaN)).toBe('');
    expect(formatUnixSeconds(undefined, '—')).toBe('—');
  });
});
