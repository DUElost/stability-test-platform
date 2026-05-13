import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import DeviceDetailDrawer from './DeviceDetailDrawer';
import type { DeviceMatrixItem } from '@/utils/api/types';

function makeDevice(overrides: Partial<DeviceMatrixItem> = {}): DeviceMatrixItem {
  return {
    device_id: 1,
    device_serial: 'DEV-AAAA',
    device_model: 'Pixel 8',
    host_id: 'host-101',
    job_id: 3001,
    job_status: 'RUNNING',
    ui_status: 'running',
    current_stage: 'patrol',
    current_step: 'monkey_check',
    patrol_cycle_count: 12,
    patrol_success_cycle_count: 12,
    patrol_failed_cycle_count: 0,
    current_failure_streak: 0,
    next_retry_at: null,
    manual_action: null,
    log_signal_count: 0,
    last_heartbeat_at: '2026-05-08T12:30:00Z',
    started_at: '2026-05-08T12:00:00Z',
    ended_at: null,
    status_reason: null,
    ...overrides,
  };
}

const handlers = {
  onClose: vi.fn(),
  onManualRetry: vi.fn(),
  onManualExit: vi.fn(),
  onOpenReport: vi.fn(),
};

describe('DeviceDetailDrawer — status_reason 展示', () => {
  it('does NOT render 状态原因 row when status_reason is null', () => {
    render(<DeviceDetailDrawer device={makeDevice()} {...handlers} />);
    expect(screen.queryByText('状态原因')).toBeNull();
  });

  it('renders 状态原因 row in red when device is failed with reason', () => {
    const device = makeDevice({
      ui_status: 'failed',
      job_status: 'FAILED',
      current_stage: 'failed',
      status_reason: 'patrol_step_failed: monkey_launch',
    });
    render(<DeviceDetailDrawer device={device} {...handlers} />);
    const label = screen.getByText('状态原因');
    expect(label).toBeInTheDocument();
    // label uses extraCls = text-red-600 font-semibold
    expect(label.className).toMatch(/text-red-600/);
    // value cell carries same red highlight + full reason text
    const value = screen.getByText('patrol_step_failed: monkey_launch');
    expect(value.className).toMatch(/text-red-600/);
    expect(value.className).toMatch(/font-semibold/);
  });

  it('renders 状态原因 row in amber when device is in non-failed state (e.g. backoff)', () => {
    const device = makeDevice({
      ui_status: 'backoff',
      job_status: 'RUNNING',
      current_stage: 'patrol',
      status_reason: 'awaiting_retry: backoff window',
    });
    render(<DeviceDetailDrawer device={device} {...handlers} />);
    const label = screen.getByText('状态原因');
    expect(label).toBeInTheDocument();
    // non-failed → amber, not red
    expect(label.className).toMatch(/text-amber-700/);
    expect(label.className).not.toMatch(/text-red-600/);
    expect(screen.getByText('awaiting_retry: backoff window')).toBeInTheDocument();
  });
});
