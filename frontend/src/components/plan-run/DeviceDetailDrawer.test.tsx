import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { DeviceMatrixItem } from '@/utils/api/types';

// Mock api.planRuns.listJobArtifacts（CrashArtifactsBlock 调用）
vi.mock('@/utils/api', () => ({
  api: {
    planRuns: {
      listJobArtifacts: vi.fn().mockResolvedValue([]),
    },
  },
}));

import DeviceDetailDrawer from './DeviceDetailDrawer';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: 0 } },
});
const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
);
const render_ = (ui: React.ReactElement) => render(ui, { wrapper });

function makeDevice(overrides: Partial<DeviceMatrixItem> = {}): DeviceMatrixItem {
  return {
    device_id: 1,
    device_serial: 'DEV-AAAA',
    device_model: 'Pixel 8',
    host_id: 'host-101',
    job_id: 3001,
    job_status: 'RUNNING',
    ui_status: 'running',
    job_exec_status: 'running',
    device_link_status: 'online',
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
  runId: 1,
};

describe('DeviceDetailDrawer — status_reason 展示', () => {
  it('does NOT render 状态原因 row when status_reason is null', () => {
    render_(<DeviceDetailDrawer device={makeDevice()} {...handlers} />);
    expect(screen.queryByText('状态原因')).toBeNull();
  });

  it('renders 状态原因 row in red when device is failed with reason', () => {
    const device = makeDevice({
      ui_status: 'failed',
      job_status: 'FAILED',
      current_stage: 'failed',
      status_reason: 'patrol_step_failed: monkey_launch',
    });
    render_(<DeviceDetailDrawer device={device} {...handlers} />);
    const label = screen.getByText('状态原因');
    expect(label).toBeInTheDocument();
    // label uses extraCls = text-destructive font-semibold
    expect(label.className).toMatch(/text-destructive/);
    // value cell carries same red highlight + full reason text
    const value = screen.getByText('patrol_step_failed: monkey_launch');
    expect(value.className).toMatch(/text-destructive/);
    expect(value.className).toMatch(/font-semibold/);
  });

  it('shows full status_reason without ellipsis truncation', () => {
    const longReason =
      'lifecycle init failed: step failed in init: monkey_resource_push — Device 6R0A57SSAE7000320 not reachable';
    render_(
      <DeviceDetailDrawer
        device={makeDevice({
          ui_status: 'failed',
          job_status: 'FAILED',
          current_stage: 'failed',
          status_reason: longReason,
        })}
        {...handlers}
      />,
    );
    const value = screen.getByTestId('device-drawer-status-reason');
    expect(value).toHaveTextContent(longReason);
    expect(value.className).toMatch(/whitespace-pre-wrap/);
    expect(value.className).toMatch(/break-words/);
    expect(value.className).not.toMatch(/\btruncate\b/);
  });

  it('renders 状态原因 row in amber when device is in non-failed state (e.g. backoff)', () => {
    const device = makeDevice({
      ui_status: 'backoff',
      job_status: 'RUNNING',
      current_stage: 'patrol',
      status_reason: 'awaiting_retry: backoff window',
    });
    render_(<DeviceDetailDrawer device={device} {...handlers} />);
    const label = screen.getByText('状态原因');
    expect(label).toBeInTheDocument();
    // non-failed → warning, not destructive
    expect(label.className).toMatch(/text-warning/);
    expect(label.className).not.toMatch(/text-destructive/);
    expect(screen.getByText('awaiting_retry: backoff window')).toBeInTheDocument();
  });
});

describe('DeviceDetailDrawer — SLA / BUSY 展示', () => {
  it('renders device link + job exec status pills', () => {
    render_(
      <DeviceDetailDrawer
        device={makeDevice({
          grace_remaining_seconds: 240,
          ui_status: 'unknown',
          job_exec_status: 'unknown',
          device_link_status: 'online',
        })}
        {...handlers}
      />,
    );
    const pill = screen.getByTestId('device-drawer-status-pill');
    expect(pill).toHaveTextContent('在线');
    expect(pill).toHaveTextContent('已断开');
  });

  it('shows offline hint and hides retry when device ADB is unreachable', () => {
    render_(
      <DeviceDetailDrawer
        device={makeDevice({
          ui_status: 'unknown',
          job_exec_status: 'backoff',
          device_link_status: 'offline',
          adb_connected: false,
          adb_state: 'offline',
          capabilities: {
            manual_retry: false,
            manual_exit: true,
            manual_retry_blocked_reason: 'device_disconnected',
          },
        })}
        {...handlers}
      />,
    );
    expect(screen.getByTestId('device-drawer-offline-hint')).toBeInTheDocument();
    expect(screen.queryByTestId('device-drawer-retry-btn')).not.toBeInTheDocument();
    expect(screen.getByTestId('device-drawer-exit-btn')).toBeInTheDocument();
  });

  it('renders Grace 剩余 when grace_remaining_seconds is set', () => {
    render_(
      <DeviceDetailDrawer
        device={makeDevice({ grace_remaining_seconds: 240, ui_status: 'unknown' })}
        {...handlers}
      />,
    );
    expect(screen.getByText('Grace 剩余')).toBeInTheDocument();
    expect(screen.getByText('240s')).toBeInTheDocument();
  });

  it('renders 认领 SLA 剩余 when pending_claim_remaining_seconds is set', () => {
    render_(
      <DeviceDetailDrawer
        device={makeDevice({
          ui_status: 'pending',
          job_status: 'PENDING',
          pending_claim_remaining_seconds: 88,
        })}
        {...handlers}
      />,
    );
    expect(screen.getByText('认领 SLA 剩余')).toBeInTheDocument();
    expect(screen.getByText('88s')).toBeInTheDocument();
  });

  it('renders BUSY 来源 and 占用 Job when busy_reason is adb_excluded', () => {
    render_(
      <DeviceDetailDrawer
        device={makeDevice({
          busy_reason: 'adb_excluded',
          busy_lease_job_id: 4002,
        })}
        {...handlers}
      />,
    );
    expect(screen.getByText('BUSY 来源')).toBeInTheDocument();
    expect(screen.getByText('ADB 状态排除')).toBeInTheDocument();
    expect(screen.getByText('占用 Job')).toBeInTheDocument();
    expect(screen.getByText('#4002')).toBeInTheDocument();
  });
});

describe('DeviceDetailDrawer — backend action capabilities', () => {
  it('hides manual actions when backend capabilities deny them', () => {
    render_(
      <DeviceDetailDrawer
        device={makeDevice({
          capabilities: { manual_retry: false, manual_exit: false },
        })}
        {...handlers}
      />,
    );

    expect(screen.queryByTestId('device-drawer-retry-btn')).not.toBeInTheDocument();
    expect(screen.queryByTestId('device-drawer-exit-btn')).not.toBeInTheDocument();
  });

  it('allows a backend capability to expose retry independently of UI status', () => {
    render_(
      <DeviceDetailDrawer
        device={makeDevice({
          ui_status: 'failed',
          job_status: 'FAILED',
          current_stage: 'failed',
          capabilities: { manual_retry: true, manual_exit: false },
        })}
        {...handlers}
      />,
    );

    expect(screen.getByTestId('device-drawer-retry-btn')).toBeInTheDocument();
    expect(screen.queryByTestId('device-drawer-exit-btn')).not.toBeInTheDocument();
  });
});

describe('DeviceDetailDrawer — a11y / 键盘', () => {
  it('exposes dialog role + aria-modal', () => {
    render_(<DeviceDetailDrawer device={makeDevice()} {...handlers} />);
    const drawer = screen.getByTestId('device-drawer');
    expect(drawer).toHaveAttribute('role', 'dialog');
    expect(drawer).toHaveAttribute('aria-modal', 'true');
  });

  it('closes on Escape', () => {
    const onClose = vi.fn();
    render_(
      <DeviceDetailDrawer device={makeDevice()} {...handlers} onClose={onClose} />,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('does not close on Escape while a confirm dialog is open', () => {
    const onClose = vi.fn();
    render_(
      <DeviceDetailDrawer device={makeDevice()} {...handlers} onClose={onClose} />,
    );
    // open the retry confirm dialog → confirmOpen guards the drawer's Esc handler
    fireEvent.click(screen.getByTestId('device-drawer-retry-btn'));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('link 不可达时否决 capabilities.manual_retry，警告条与按钮不并存', () => {
    // 回归:unauthorized 曾让后端 capabilities 放行、前端 link 判 adb_error,
    // 结果「设备 ADB 不可达」警告条和「立即重试」按钮同时渲染。
    render_(
      <DeviceDetailDrawer
        device={makeDevice({
          job_status: 'RUNNING',
          ui_status: 'running',
          job_exec_status: 'running',
          device_link_status: 'adb_error',
          adb_connected: true,
          adb_state: 'unauthorized',
          capabilities: {
            manual_retry: true,          // 旧后端/口径漂移下会是 true
            manual_exit: true,
            manual_retry_blocked_reason: null,
          },
        })}
        {...handlers}
      />,
    );
    expect(screen.getByTestId('device-drawer-offline-hint')).toBeInTheDocument();
    expect(screen.queryByTestId('device-drawer-retry-btn')).not.toBeInTheDocument();
    expect(screen.getByTestId('device-drawer-exit-btn')).toBeInTheDocument();
  });
});
