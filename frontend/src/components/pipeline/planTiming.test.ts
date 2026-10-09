import { describe, expect, it } from 'vitest';
import {
  PATROL_DURATION_HINT,
  formatPatrolDuration,
  patrolDurationSummary,
} from './planTiming';

describe('formatPatrolDuration', () => {
  it('没有巡检步骤（间隔为空）时本字段不生效，显示「无巡检」而不是「不限」', () => {
    expect(formatPatrolDuration(3600, null)).toBe('无巡检');
    expect(formatPatrolDuration(null, undefined)).toBe('无巡检');
  });

  it('有巡检、未设时长 = 不限（巡检持续到手动退出或中止）', () => {
    expect(formatPatrolDuration(null, 60)).toBe('不限');
    expect(formatPatrolDuration(undefined, 60)).toBe('不限');
    // 引擎条件是 timeout_seconds > 0；0 同样不限（后端写入边界 ge=1，正常存不进来）
    expect(formatPatrolDuration(0, 60)).toBe('不限');
  });

  it('有巡检、有时长时按样式格式化', () => {
    expect(formatPatrolDuration(125, 60)).toBe('2m 5s');
    expect(formatPatrolDuration(3 * 86400, 60, 'compact')).toBe('3d 0h');
  });
});

describe('patrolDurationSummary', () => {
  it('三种状态各给出一句话结论', () => {
    expect(patrolDurationSummary(3600, false)).toBe('当前没有巡检步骤，巡检时长不生效。');
    expect(patrolDurationSummary(null, true)).toBe('巡检时长不限：巡检会一直进行，直到手动退出或中止。');
    expect(patrolDurationSummary(7200, true)).toContain('巡检满 2h 0m 后在下一轮巡检前正常结束（算成功）');
  });

  it('完整定义写明「自 init 完成起计」与「到点算成功」，不再说成故障超时', () => {
    expect(PATROL_DURATION_HINT).toContain('完成初始化后开始计时');
    expect(PATROL_DURATION_HINT).toContain('算成功');
    expect(PATROL_DURATION_HINT).not.toContain('中止；已完成步骤');
  });
});
