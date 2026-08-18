import { describe, expect, it } from 'vitest';
import {
  DEFAULT_STEP_WALL_CLOCK_SECONDS,
  formatStepTimeout,
  stepTimeoutHint,
} from './stepTiming';

// 这三种取值在引擎里行为完全不同（0 = 不限 / null = 回落默认 / n = n 秒），
// 而它们此前在 UI 上两个方向都反了。用例按契约钉死，别再靠直觉读。

describe('formatStepTimeout', () => {
  it('0 是「不限」而不是「零秒」', () => {
    expect(formatStepTimeout(0)).toBe('∞');
  });

  it('null / undefined 是「回落默认」而不是「永不超时」', () => {
    expect(formatStepTimeout(null)).toBe('默认');
    expect(formatStepTimeout(undefined)).toBe('默认');
  });

  it('正数按秒展示', () => {
    expect(formatStepTimeout(30)).toBe('30s');
    expect(formatStepTimeout(600)).toBe('600s');
  });
});

describe('stepTimeoutHint', () => {
  it('0 的说明点出它要配套停滞钟', () => {
    expect(stepTimeoutHint(0)).toContain('不限');
    expect(stepTimeoutHint(0)).toContain('stall_seconds');
  });

  it('缺省的说明点出回落链与兜底秒数', () => {
    const hint = stepTimeoutHint(null);
    expect(hint).toContain('STP_STEP_WALL_CLOCK_SECONDS');
    expect(hint).toContain(String(DEFAULT_STEP_WALL_CLOCK_SECONDS));
  });

  it('正数的说明给出超时后果', () => {
    expect(stepTimeoutHint(600)).toContain('600s');
    expect(stepTimeoutHint(600)).toContain('失败');
  });
});
