import { describe, expect, it } from 'vitest';
import {
  isJobStuck,
  normalizeDispatchStateForRun,
  shouldShowDispatchGate,
} from './planRunDetailUtils';
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
});
