import { describe, expect, it } from 'vitest';
import { isJobStuck } from './planRunDetailUtils';
import type { DeviceMatrixItem } from '@/utils/api/types';

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
