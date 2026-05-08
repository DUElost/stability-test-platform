import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import DeviceMatrixCard from './DeviceMatrixCard';
import type { PlanRunDevicesPayload } from '@/utils/api/types';

const fixture: PlanRunDevicesPayload = {
  plan_run_id: 12,
  total: 4,
  by_status: { all: 4, running: 2, backoff: 1, failed: 1 },
  by_host: { 'host-101': 2, 'host-202': 2 },
  devices: [
    {
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
    },
    {
      device_id: 2,
      device_serial: 'DEV-BBBB',
      device_model: 'Pixel 8',
      host_id: 'host-101',
      job_id: 3002,
      job_status: 'RUNNING',
      ui_status: 'backoff',
      current_stage: 'patrol',
      current_step: 'monkey_check',
      patrol_cycle_count: 12,
      patrol_success_cycle_count: 9,
      patrol_failed_cycle_count: 3,
      current_failure_streak: 4,
      next_retry_at: new Date(Date.now() + 60_000).toISOString(),
      manual_action: null,
      log_signal_count: 2,
      last_heartbeat_at: '2026-05-08T12:30:00Z',
      started_at: '2026-05-08T12:00:00Z',
      ended_at: null,
    },
    {
      device_id: 3,
      device_serial: 'DEV-CCCC',
      device_model: 'Pixel 8',
      host_id: 'host-202',
      job_id: 3003,
      job_status: 'FAILED',
      ui_status: 'failed',
      current_stage: 'failed',
      current_step: null,
      patrol_cycle_count: 5,
      patrol_success_cycle_count: 2,
      patrol_failed_cycle_count: 3,
      current_failure_streak: 0,
      next_retry_at: null,
      manual_action: null,
      log_signal_count: 5,
      last_heartbeat_at: null,
      started_at: '2026-05-08T11:00:00Z',
      ended_at: '2026-05-08T11:30:00Z',
    },
    {
      device_id: 4,
      device_serial: 'DEV-DDDD',
      device_model: 'Pixel 8',
      host_id: 'host-202',
      job_id: 3004,
      job_status: 'RUNNING',
      ui_status: 'running',
      current_stage: 'patrol',
      current_step: 'monkey_check',
      patrol_cycle_count: 12,
      patrol_success_cycle_count: 12,
      patrol_failed_cycle_count: 0,
      current_failure_streak: 0,
      next_retry_at: null,
      manual_action: 'EXIT_REQUESTED',
      log_signal_count: 0,
      last_heartbeat_at: '2026-05-08T12:30:00Z',
      started_at: '2026-05-08T12:00:00Z',
      ended_at: null,
    },
  ],
};

describe('DeviceMatrixCard', () => {
  it('renders all devices in table view by default with status pills + facet counts', () => {
    render(<DeviceMatrixCard data={fixture} />);
    expect(screen.getByTestId('device-row-3001')).toHaveTextContent('DEV-AAAA');
    expect(screen.getByTestId('device-row-3002')).toHaveTextContent('退避');
    // failure streak 4 → highlight red and shown as `× 4`
    expect(screen.getByTestId('device-row-3002')).toHaveTextContent('× 4');
    expect(screen.getByTestId('device-row-3003')).toHaveTextContent('失败');
    // exit_requested job shows "退出待执行"
    expect(screen.getByTestId('device-row-3004')).toHaveTextContent('退出待执行');
    // facet counts on filter buttons
    expect(screen.getByTestId('device-status-filter-running')).toHaveTextContent('2');
    expect(screen.getByTestId('device-status-filter-backoff')).toHaveTextContent('1');
  });

  it('switches to grid view and back', () => {
    render(<DeviceMatrixCard data={fixture} />);
    fireEvent.click(screen.getByTestId('device-view-grid'));
    expect(screen.getByTestId('device-cell-3001')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('device-view-table'));
    expect(screen.getByTestId('device-row-3001')).toBeInTheDocument();
  });

  it('forwards filter changes to parent', () => {
    const onStatus = vi.fn();
    const onHost = vi.fn();
    render(
      <DeviceMatrixCard
        data={fixture}
        onStatusFilterChange={onStatus}
        onHostFilterChange={onHost}
      />,
    );
    fireEvent.click(screen.getByTestId('device-status-filter-backoff'));
    expect(onStatus).toHaveBeenCalledWith('backoff');
    fireEvent.change(screen.getByTestId('device-host-filter'), {
      target: { value: 'host-202' },
    });
    expect(onHost).toHaveBeenCalledWith('host-202');
  });

  it('triggers onSelectDevice when a row is clicked', () => {
    const onSelect = vi.fn();
    render(<DeviceMatrixCard data={fixture} onSelectDevice={onSelect} />);
    fireEvent.click(screen.getByTestId('device-row-3002'));
    expect(onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ job_id: 3002, ui_status: 'backoff' }),
    );
  });

  it('shows empty state when no devices', () => {
    render(
      <DeviceMatrixCard
        data={{
          plan_run_id: 12,
          total: 0,
          by_status: { all: 0 },
          by_host: {},
          devices: [],
        }}
      />,
    );
    expect(screen.getByText('该过滤条件下暂无设备')).toBeInTheDocument();
  });
});
