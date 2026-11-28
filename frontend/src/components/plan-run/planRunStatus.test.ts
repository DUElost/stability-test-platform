import { describe, expect, it } from 'vitest';
import {
  finishingDeadlineRemainingSeconds,
  finishingKindLabel,
  finishingTriggerLabel,
  formatFinishingDeadlineCountdown,
  getRunFinishingSummary,
} from './planRunStatus';

describe('planRunStatus finishing helpers (#4206 U3)', () => {
  it('maps finishing_kind / trigger 文案', () => {
    expect(finishingKindLabel('teardown_disarm')).toBe('解除配置中');
    expect(finishingKindLabel('atomic_commit')).toBe('刷机临界区保护中');
    expect(finishingKindLabel(null)).toBeNull();
    expect(finishingTriggerLabel('manual_abort')).toBe('手动中止');
    expect(finishingTriggerLabel('natural_end')).toBe('自然到点');
  });

  it('null deadline 不产生假倒计时', () => {
    expect(finishingDeadlineRemainingSeconds(null)).toBeNull();
    expect(finishingDeadlineRemainingSeconds(undefined)).toBeNull();
    expect(formatFinishingDeadlineCountdown(null)).toBeNull();
    expect(formatFinishingDeadlineCountdown('invalid')).toBeNull();
  });

  it('有效 deadline 按剩余秒格式化', () => {
    const now = Date.parse('2026-11-28T06:00:00.000Z');
    const deadline = '2026-11-28T06:01:30.000Z';
    expect(finishingDeadlineRemainingSeconds(deadline, now)).toBe(90);
    expect(formatFinishingDeadlineCountdown(deadline, now)).toBe('90s');
  });

  it('无 Run 投影时不显示空摘要', () => {
    expect(getRunFinishingSummary(undefined)).toEqual({ counts: null, deadline: null });
  });
});
