import { describe, expect, it } from 'vitest';
import {
  isJobStuck,
  normalizeDispatchStateForRun,
  planRunRefreshKeys,
  shouldShowDispatchGate,
} from './planRunDetailUtils';
import { dedupKeys, planRunKeys } from '@/utils/api/queryKeys';
import type { DeviceMatrixItem, PlanRun } from '@/utils/api/types';

function runningDevice(overrides: Partial<DeviceMatrixItem> = {}): DeviceMatrixItem {
  return {
    device_id: 1,
    job_id: 10,
    job_status: 'RUNNING',
    ui_status: 'running',
    current_stage: 'patrol',
    patrol_cycle_count: 1,
    patrol_success_cycle_count: 1,
    patrol_failed_cycle_count: 0,
    current_failure_streak: 0,
    log_signal_count: 0,
    ...overrides,
  };
}

describe('isJobStuck backend projections', () => {
  const now = new Date('2026-07-13T08:00:00Z').getTime();

  it('prefers authoritative is_stuck over legacy heartbeat math', () => {
    expect(isJobStuck(runningDevice({
      is_stuck: false,
      last_heartbeat_at: '2026-07-13T07:00:00Z',
    }), now)).toBe(false);
  });

  it('uses heartbeat_deadline_at when backend supplies a deadline', () => {
    expect(isJobStuck(runningDevice({
      heartbeat_deadline_at: '2026-07-13T07:59:59Z',
      last_heartbeat_at: '2026-07-13T07:59:50Z',
    }), now)).toBe(true);
  });
});

describe('shouldShowDispatchGate', () => {
  const baseDispatch = {
    status: 'queued' as const,
    enqueued_at: '2026-07-23T08:00:00Z',
    started_at: null,
    completed_at: null,
    last_error: null,
    requeue_attempts: 0,
    enqueue_key: null,
  };

  it('shows gate for RUNNING V2 runs with dispatch_state only', () => {
    const run = {
      id: 96,
      status: 'RUNNING',
      run_context: { dispatch_state: baseDispatch },
    } as PlanRun;
    expect(shouldShowDispatchGate(run)).toBe(true);
  });

  it('hides gate for terminal SUCCESS without precheck', () => {
    const run = {
      id: 12,
      status: 'SUCCESS',
      run_context: null,
    } as PlanRun;
    expect(shouldShowDispatchGate(run)).toBe(false);
  });
});

describe('normalizeDispatchStateForRun', () => {
  it('treats stale queued dispatch_state as completed on RUNNING V2 runs', () => {
    const run = {
      id: 96,
      status: 'RUNNING',
      run_context: {
        dispatch_state: {
          status: 'queued',
          enqueued_at: '2026-07-23T08:00:00Z',
        },
      },
    } as PlanRun;
    expect(normalizeDispatchStateForRun(run, run.run_context?.dispatch_state)?.status).toBe(
      'completed',
    );
  });

  it('#1193 刷新键表覆盖去重状态与逐条用例结果（后处理产物不遗漏）', () => {
    const keys = planRunRefreshKeys(7);
    expect(keys).toContainEqual(planRunKeys.detail(7));
    // #2288：必须是**前缀**键。精确键 `logEvents(7)` 含默认参数对象，React Query 的
    // 部分匹配只能命中仍处默认值的那条缓存——选过平台 chip 或点过「加载更多」之后
    // 该卡片就被「刷新」漏掉。这里同时钉住「任意参数组合都被前缀键覆盖」。
    expect(keys).toContainEqual(planRunKeys.logEventsByRun(7));
    const prefix = planRunKeys.logEventsByRun(7) as readonly unknown[];
    for (const variant of [
      planRunKeys.logEvents(7),
      planRunKeys.logEvents(7, { limit: 500, platform: 'UNISOC' }),
      planRunKeys.logEvents(7, { platform: 'MTK' }),
    ]) {
      const key = variant as readonly unknown[];
      expect(prefix.length).toBeLessThanOrEqual(key.length);
      expect(key.slice(0, prefix.length)).toEqual(prefix);
    }
    expect(keys).toContainEqual(dedupKeys.status(7));
    expect(keys).toContainEqual(planRunKeys.testCaseResults(7));
  });
});
