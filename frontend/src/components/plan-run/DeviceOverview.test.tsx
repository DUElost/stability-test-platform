import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ComponentProps } from 'react';
import DeviceOverview from './DeviceOverview';
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
      status_reason: 'patrol_step_failed: monkey_launch',
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

// DeviceOverview defaults to the grid (minimap) view; the table that
// DeviceMatrixCard used to own now lives behind the table-view toggle.
function renderInTableView(
  props: Partial<ComponentProps<typeof DeviceOverview>> = {},
) {
  const result = render(<DeviceOverview data={fixture} {...props} />);
  fireEvent.click(screen.getByTestId('device-overview-table-btn'));
  return result;
}

describe('DeviceOverview', () => {
  it('defaults to grid (minimap) view and switches to table on toggle', () => {
    render(<DeviceOverview data={fixture} />);
    expect(screen.getByTestId('minimap-cell-3001')).toBeInTheDocument();
    expect(screen.queryByTestId('device-row-3001')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('device-overview-table-btn'));
    expect(screen.getByTestId('device-row-3001')).toBeInTheDocument();
    expect(screen.queryByTestId('minimap-cell-3001')).not.toBeInTheDocument();
  });

  it('renders all devices in table view with status pills + facet counts', () => {
    renderInTableView();
    expect(screen.getByTestId('device-row-3001')).toHaveTextContent('DEV-AAAA');
    expect(screen.getByTestId('device-row-3002')).toHaveTextContent('退避');
    // failure streak 4 → highlight red and shown as `× 4`
    expect(screen.getByTestId('device-row-3002')).toHaveTextContent('× 4');
    expect(screen.getByTestId('device-row-3003')).toHaveTextContent('失败');
    // exit_requested job shows "退出待执行"
    expect(screen.getByTestId('device-row-3004')).toHaveTextContent('退出待执行');
    // facet counts on filter buttons (filter bar is shared across both views)
    expect(screen.getByTestId('device-status-filter-running')).toHaveTextContent('2');
    expect(screen.getByTestId('device-status-filter-backoff')).toHaveTextContent('1');
    expect(screen.queryByTestId('device-status-filter-risk')).not.toBeInTheDocument();
  });

  it('forwards filter changes to parent', () => {
    const onStatus = vi.fn();
    const onHost = vi.fn();
    render(
      <DeviceOverview
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

  it('triggers onSelectDevice when a table row is clicked', () => {
    const onSelect = vi.fn();
    renderInTableView({ onSelectDevice: onSelect });
    fireEvent.click(screen.getByTestId('device-row-3002'));
    expect(onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ job_id: 3002, ui_status: 'backoff' }),
    );
  });

  it('shows empty state when no devices', () => {
    render(
      <DeviceOverview
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

  it('exposes status_reason as title tooltip on the status pill for failed devices', () => {
    renderInTableView();
    const failedRow = screen.getByTestId('device-row-3003');
    // pill is the first <span> with a title; assert it carries the full reason
    const pill = failedRow.querySelector('span[title]') as HTMLElement | null;
    expect(pill).not.toBeNull();
    expect(pill!.getAttribute('title')).toBe('patrol_step_failed: monkey_launch');
    // Inline truncated text should NOT be rendered (reason moved to tooltip + drawer)
    expect(failedRow).not.toHaveTextContent('patrol_step_failed: monkey_launch');
  });

  it('renders unknown status pill distinct from failed', () => {
    const data: PlanRunDevicesPayload = {
      ...fixture,
      by_status: { all: 1, unknown: 1 },
      devices: [
        {
          device_id: 9,
          device_serial: 'DEV-UNKN',
          device_model: 'Pixel 8',
          host_id: 'host-101',
          job_id: 3009,
          job_status: 'UNKNOWN',
          ui_status: 'unknown',
          current_stage: 'unknown',
          current_step: null,
          patrol_cycle_count: 3,
          patrol_success_cycle_count: 2,
          patrol_failed_cycle_count: 1,
          current_failure_streak: 0,
          next_retry_at: null,
          manual_action: null,
          log_signal_count: 0,
          last_heartbeat_at: null,
          started_at: '2026-05-08T11:00:00Z',
          ended_at: null,
          status_reason: 'lease_expired',
        },
      ],
    };
    renderInTableView({ data });
    expect(screen.getByTestId('device-row-3009')).toHaveTextContent('已断开');
    const pill = screen.getByTestId('device-row-3009').querySelector('span[title]');
    expect(pill?.getAttribute('title')).toMatch(/lease_expired/);
    expect(pill?.getAttribute('title')).toMatch(/grace/);
  });

  it('keeps a running device as 运行中 even when anomaly count is non-zero', () => {
    const data: PlanRunDevicesPayload = {
      ...fixture,
      by_status: { all: 1, running: 1 },
      devices: [
        {
          ...fixture.devices[0],
          device_id: 11,
          device_serial: 'DEV-RUN-LOG',
          job_id: 3011,
          ui_status: 'running',
          log_signal_count: 7,
        },
      ],
    };
    renderInTableView({ data });
    const row = screen.getByTestId('device-row-3011');
    expect(row).toHaveTextContent('运行中');
    expect(row).toHaveTextContent('7');
    expect(row).not.toHaveTextContent('风险');
  });

  it('shows grace countdown in wait column for unknown devices', () => {
    const data: PlanRunDevicesPayload = {
      ...fixture,
      by_status: { all: 1, unknown: 1 },
      devices: [
        {
          device_id: 9,
          device_serial: 'DEV-UNKN',
          device_model: 'Pixel 8',
          host_id: 'host-101',
          job_id: 3009,
          job_status: 'UNKNOWN',
          ui_status: 'unknown',
          current_stage: 'unknown',
          current_step: null,
          patrol_cycle_count: 3,
          patrol_success_cycle_count: 2,
          patrol_failed_cycle_count: 1,
          current_failure_streak: 0,
          next_retry_at: null,
          manual_action: null,
          log_signal_count: 0,
          last_heartbeat_at: null,
          started_at: '2026-05-08T11:00:00Z',
          ended_at: '2026-05-08T11:30:00Z',
          status_reason: 'lease_expired',
          grace_remaining_seconds: 180,
        },
      ],
    };
    renderInTableView({ data });
    expect(screen.getByTestId('device-wait-3009')).toHaveTextContent('grace 180s');
  });

  it('shows pending claim SLA in wait column', () => {
    const data: PlanRunDevicesPayload = {
      ...fixture,
      by_status: { all: 1, pending: 1 },
      devices: [
        {
          device_id: 10,
          device_serial: 'DEV-PEND',
          device_model: 'Pixel 8',
          host_id: 'host-101',
          job_id: 3010,
          job_status: 'PENDING',
          ui_status: 'pending',
          current_stage: 'pending',
          current_step: null,
          patrol_cycle_count: 0,
          patrol_success_cycle_count: 0,
          patrol_failed_cycle_count: 0,
          current_failure_streak: 0,
          next_retry_at: null,
          manual_action: null,
          log_signal_count: 0,
          last_heartbeat_at: null,
          started_at: null,
          created_at: new Date(Date.now() - 30_000).toISOString(),
          ended_at: null,
          pending_claim_remaining_seconds: 90,
        },
      ],
    };
    renderInTableView({ data });
    expect(screen.getByTestId('device-wait-3010')).toHaveTextContent('认领 90s');
  });

  it('shows pending claim SLA countdown in status tooltip', () => {
    const createdAt = new Date(Date.now() - 30_000).toISOString();
    const data: PlanRunDevicesPayload = {
      ...fixture,
      by_status: { all: 1, pending: 1 },
      devices: [
        {
          device_id: 10,
          device_serial: 'DEV-PEND',
          device_model: 'Pixel 8',
          host_id: 'host-101',
          job_id: 3010,
          job_status: 'PENDING',
          ui_status: 'pending',
          current_stage: 'pending',
          current_step: null,
          patrol_cycle_count: 0,
          patrol_success_cycle_count: 0,
          patrol_failed_cycle_count: 0,
          current_failure_streak: 0,
          next_retry_at: null,
          manual_action: null,
          log_signal_count: 0,
          last_heartbeat_at: null,
          started_at: null,
          created_at: createdAt,
          ended_at: null,
        },
      ],
    };
    renderInTableView({ data });
    const pill = screen.getByTestId('device-row-3010').querySelector('span[title]');
    expect(pill?.getAttribute('title')).toMatch(/90s 内未认领/);
    expect(pill?.getAttribute('title')).toMatch(/120s SLA/);
  });

  it('selects device on Enter key (keyboard access)', () => {
    const onSelect = vi.fn();
    renderInTableView({ onSelectDevice: onSelect });
    fireEvent.keyDown(screen.getByTestId('device-row-3002'), { key: 'Enter' });
    expect(onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ job_id: 3002 }),
    );
  });

  it('respects controlled viewMode and emits onViewModeChange', () => {
    const onViewModeChange = vi.fn();
    const { rerender } = render(
      <DeviceOverview
        data={fixture}
        viewMode="grid"
        onViewModeChange={onViewModeChange}
      />,
    );
    // controlled grid → minimap cells, no table rows
    expect(screen.getByTestId('minimap-cell-3001')).toBeInTheDocument();
    expect(screen.queryByTestId('device-row-3001')).not.toBeInTheDocument();
    // clicking table-btn emits change but does NOT self-switch (controlled)
    fireEvent.click(screen.getByTestId('device-overview-table-btn'));
    expect(onViewModeChange).toHaveBeenCalledWith('table');
    expect(screen.queryByTestId('device-row-3001')).not.toBeInTheDocument();
    // parent flips the prop → table renders
    rerender(
      <DeviceOverview
        data={fixture}
        viewMode="table"
        onViewModeChange={onViewModeChange}
      />,
    );
    expect(screen.getByTestId('device-row-3001')).toBeInTheDocument();
  });
});
