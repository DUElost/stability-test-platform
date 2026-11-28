import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { DeviceMatrixItem } from '@/utils/api/types';

// Mock api.planRuns sub-queries（CrashArtifactsBlock / JobTimelineBlock / JobWatcherSignalsBlock）
vi.mock('@/utils/api', () => ({
  api: {
    planRuns: {
      listJobArtifacts: vi.fn().mockResolvedValue([]),
      getJobTimeline: vi.fn().mockResolvedValue({
        plan_run_id: 1,
        job_id: 3001,
        total: 0,
        skip: 0,
        limit: 50,
        items: [],
      }),
      getJobWatcherSignals: vi.fn().mockResolvedValue({
        plan_run_id: 1,
        job_id: 3001,
        total: 0,
        skip: 0,
        limit: 50,
        items: [],
      }),
    },
  },
}));

import DeviceDetailDrawer from './DeviceDetailDrawer';
import { api } from '@/utils/api';

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

/**
 * #2420（第 5 项）：抽屉里的时间曾直接铺后端裸 ISO（`2026-05-08T12:00:00Z`），
 * 而**同一页面页头**显示 `05/08 20:00` —— 一屏两套口径、差 8 小时（同族缺陷
 * 见 #2265 / #2358）。判据：走页头同一个 formatDateTimeShort，且空值仍是「—」。
 */
describe('DeviceDetailDrawer 时间口径（#2420）', () => {
  function valueOf(label: string): string {
    const dt = screen.getByText(label);
    return (dt.parentElement as HTMLElement).textContent ?? '';
  }

  it('开始时间/最近心跳不再是裸 ISO UTC 串', async () => {
    render_(<DeviceDetailDrawer device={makeDevice()} {...handlers} />);
    await screen.findByText('开始时间');
    for (const label of ['开始时间', '最近心跳', '结束时间', '下次重试']) {
      const text = valueOf(label);
      expect(text).not.toMatch(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
    }
    expect(valueOf('开始时间')).toMatch(/\d{2}\/\d{2}\s+\d{2}:\d{2}/);
  });

  it('空时间仍显示占位「—」，不显示 1970 之类的误解析', async () => {
    render_(<DeviceDetailDrawer device={makeDevice({ ended_at: null, next_retry_at: null })} {...handlers} />);
    await screen.findByText('结束时间');
    expect(valueOf('结束时间')).toContain('—');
    expect(valueOf('下次重试')).toContain('—');
  });

  // #3350（ADR-0023 D3）：脚本 KV 行显示 script@version；快照查不到 → —
  it('脚本行显示 name@version（快照派生）', async () => {
    render_(
      <DeviceDetailDrawer
        device={makeDevice({ current_script_name: 'monkey_test', current_script_version: '5.2.0' })}
        {...handlers}
      />,
    );
    await screen.findByText('脚本');
    expect(valueOf('脚本')).toContain('monkey_test@5.2.0');
  });

  it('脚本行在旧 PlanRun（无身份）上显示 —', async () => {
    render_(<DeviceDetailDrawer device={makeDevice()} {...handlers} />);
    await screen.findByText('脚本');
    expect(valueOf('脚本')).toContain('—');
  });
});

describe('DeviceDetailDrawer — 解除结果（ADR-0061 D1.2 / U6）', () => {
  it('shows 无解除记录 for null and empty disarm_results', () => {
    const { unmount } = render_(
      <DeviceDetailDrawer device={makeDevice({ disarm_results: null })} {...handlers} />,
    );
    expect(screen.getByTestId('device-drawer-disarm-empty')).toHaveTextContent('无解除记录');
    unmount();
    render_(
      <DeviceDetailDrawer device={makeDevice({ disarm_results: {} })} {...handlers} />,
    );
    expect(screen.getByTestId('device-drawer-disarm-empty')).toHaveTextContent('无解除记录');
  });

  it('maps all six machine values and shows the reason', () => {
    render_(
      <DeviceDetailDrawer
        device={makeDevice({
          disarm_results: {
            'power-cycle': { result: 'confirmed', reason: 'readback clean' },
            sleep: { result: 'executed_unverified' },
            mtbf: { result: 'failed', reason: 'stop returned 1' },
            gpu: { result: 'readback_failed', reason: 'permission denied' },
            monkey: { result: 'not_executed', reason: 'lease_lost' },
            leftovers: { result: 'incomplete' },
          },
        })}
        {...handlers}
      />,
    );
    const block = screen.getByTestId('device-drawer-disarm-results');
    expect(block).toHaveTextContent('开关机');
    expect(block).toHaveTextContent('已确认');
    expect(block).toHaveTextContent('已执行未验证');
    expect(block).toHaveTextContent('失败');
    expect(block).toHaveTextContent('回读失败');
    expect(block).toHaveTextContent('未执行');
    expect(block).toHaveTextContent('未完成');
    expect(screen.getByTestId('device-drawer-disarm-power-cycle')).toHaveTextContent(
      '原因：readback clean',
    );
    expect(screen.getByTestId('device-drawer-disarm-monkey')).toHaveTextContent('原因：lease_lost');
    expect(screen.getByTestId('device-drawer-disarm-leftovers')).toHaveTextContent('leftovers');
  });

  it('renders unknown result values verbatim without crashing', () => {
    render_(
      <DeviceDetailDrawer
        device={makeDevice({ disarm_results: { 'power-cycle': { result: 'future_value' } } })}
        {...handlers}
      />,
    );
    expect(screen.getByTestId('device-drawer-disarm-power-cycle')).toHaveTextContent('future_value');
  });

  it('U5-3：支持上一台/下一台切机、复制串号、AI 诊断与单机日志深链', () => {
    const onPrevDevice = vi.fn();
    const onNextDevice = vi.fn();
    render_(
      <DeviceDetailDrawer
        device={makeDevice({ log_signal_count: 3 })}
        {...handlers}
        runId={1594}
        devicePosition={2}
        totalDevices={5}
        onPrevDevice={onPrevDevice}
        onNextDevice={onNextDevice}
      />,
    );

    expect(screen.getByText('2/5')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('device-drawer-prev'));
    expect(onPrevDevice).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId('device-drawer-next'));
    expect(onNextDevice).toHaveBeenCalledTimes(1);

    expect(screen.getByTestId('device-drawer-copy-serial')).toBeInTheDocument();
    expect(screen.getByTestId('device-drawer-logs-btn')).toHaveAttribute(
      'href',
      '/execution/plan-runs/1594/logs?q=DEV-AAAA',
    );
    expect(screen.getByTestId('device-drawer-ai-btn').getAttribute('href')).toContain(
      '/assistant?prompt=',
    );
    expect(screen.queryByText(/上方"业务流时间线"/)).toBeNull();
  });

  it('过滤 AEE 非问题元数据/锁文件，仅展示真正的 Crash 产物', async () => {
    vi.mocked(api.planRuns.listJobArtifacts).mockResolvedValueOnce([
      {
        id: 1,
        job_id: 3009,
        filename: '1795192503706_aee.lck',
        artifact_type: 'aee_crash',
        size_bytes: 16,
        checksum: null,
        created_at: '2026-11-24T05:00:00Z',
      },
      {
        id: 2,
        job_id: 3009,
        filename: '1795192503709_db_history',
        artifact_type: 'aee_crash',
        size_bytes: 512,
        checksum: null,
        created_at: '2026-11-24T05:00:01Z',
      },
      {
        id: 3,
        job_id: 3009,
        filename: '1795192503731_file_tree.txt',
        artifact_type: 'vendor_aee_crash',
        size_bytes: 1024,
        checksum: null,
        created_at: '2026-11-24T05:00:02Z',
      },
      {
        id: 4,
        job_id: 3009,
        filename: '1795322515947_SWT_JBT_TRACES',
        artifact_type: 'aee_crash',
        size_bytes: 2048,
        checksum: null,
        created_at: '2026-11-24T05:00:03Z',
      },
      {
        id: 5,
        job_id: 3009,
        filename: '1795322515999_db.01.NE.dbg',
        artifact_type: 'aee_crash',
        size_bytes: 4096,
        checksum: null,
        created_at: '2026-11-24T05:00:04Z',
      },
    ]);

    render_(<DeviceDetailDrawer device={makeDevice({ job_id: 3009 })} {...handlers} />);

    const block = await screen.findByTestId('crash-artifacts-block');
    expect(block).toHaveTextContent('Crash 产物（1）');
    expect(block).toHaveTextContent('1795322515999_db.01.NE.dbg');
    expect(block).not.toHaveTextContent('aee.lck');
    expect(block).not.toHaveTextContent('db_history');
    expect(block).not.toHaveTextContent('file_tree.txt');
    expect(block).not.toHaveTextContent('SWT_JBT_TRACES');
  });

  it('B3-3: 阶段时间线与 Watcher 异常明细默认折叠不请求，展开时按 (runId, jobId) 延迟加载有上限子查询', async () => {
    vi.mocked(api.planRuns.getJobTimeline).mockClear();
    vi.mocked(api.planRuns.getJobWatcherSignals).mockClear();
    vi.mocked(api.planRuns.getJobTimeline).mockResolvedValueOnce({
      plan_run_id: 1,
      job_id: 3011,
      total: 1,
      skip: 0,
      limit: 50,
      items: [
        {
          id: 901,
          stage: 'patrol',
          step_id: 'monkey_check',
          event_type: 'STEP_END',
          status: 'FAILED',
          error_message: 'exit code 1',
          original_ts: '2026-11-25T08:00:00Z',
          created_at: '2026-11-25T08:00:01Z',
        },
      ],
    });
    vi.mocked(api.planRuns.getJobWatcherSignals).mockResolvedValueOnce({
      plan_run_id: 1,
      job_id: 3011,
      total: 2,
      skip: 0,
      limit: 50,
      items: [
        {
          id: 701,
          seq_no: 1,
          category: 'AEE',
          source: 'watcher',
          detected_at: '2026-11-25T08:01:00Z',
          received_at: '2026-11-25T08:01:01Z',
          path_on_device: '/data/aee_exp/db.01.NE',
        },
      ],
    });

    render_(
      <DeviceDetailDrawer
        device={makeDevice({ job_id: 3011, log_signal_count: 2 })}
        navScopeLabel="本页"
        devicePosition={1}
        totalDevices={50}
        onNextDevice={vi.fn()}
        {...handlers}
      />,
    );

    // 默认折叠：绝不预先请求时间线或异常明细
    expect(api.planRuns.getJobTimeline).not.toHaveBeenCalled();
    expect(api.planRuns.getJobWatcherSignals).not.toHaveBeenCalled();

    // 页边界标注「本页」
    expect(screen.getByTestId('device-drawer-position')).toHaveTextContent('本页 1/50');
    expect(screen.getByTestId('device-drawer-prev')).toBeDisabled();
    expect(screen.getByTestId('device-drawer-prev')).toHaveAttribute('aria-label', '上一台设备（本页）');

    // 展开时间线
    fireEvent.click(screen.getByTestId('device-drawer-timeline-toggle'));
    expect(await screen.findByText(/exit code 1/)).toBeInTheDocument();
    expect(api.planRuns.getJobTimeline).toHaveBeenCalledWith(1, 3011, { skip: 0, limit: 50 });

    // 展开异常明细
    fireEvent.click(screen.getByTestId('device-drawer-signals-toggle'));
    expect(await screen.findByText('/data/aee_exp/db.01.NE')).toBeInTheDocument();
    expect(api.planRuns.getJobWatcherSignals).toHaveBeenCalledWith(1, 3011, { skip: 0, limit: 50 });
  });
});

describe('DeviceDetailDrawer — finishing 投影 (#4206 U3)', () => {
  it('展示收尾中徽章与子类型，并渲染截止倒计时', () => {
    const deadline = new Date(Date.now() + 120_000).toISOString();
    render_(
      <DeviceDetailDrawer
        device={makeDevice({
          ui_status: 'finishing',
          job_exec_status: 'finishing',
          finishing_kind: 'teardown_disarm',
          finishing_trigger: 'manual_abort',
          finishing_deadline_at: deadline,
        })}
        {...handlers}
      />,
    );
    expect(screen.getByText('收尾中')).toBeInTheDocument();
    expect(screen.getByTestId('device-drawer-finishing-kind')).toHaveTextContent('解除配置中');
    expect(screen.getByText('收尾触发')).toBeInTheDocument();
    expect(screen.getByText('手动中止')).toBeInTheDocument();
    expect(screen.getByText('收尾截止')).toBeInTheDocument();
  });

  it('finishing_deadline_at=null 时不渲染假倒计时（纯采集 teardown）', () => {
    render_(
      <DeviceDetailDrawer
        device={makeDevice({
          ui_status: 'finishing',
          job_exec_status: 'finishing',
          finishing_kind: 'teardown_disarm',
          finishing_trigger: 'natural_end',
          finishing_deadline_at: null,
        })}
        {...handlers}
      />,
    );
    expect(screen.getByText('收尾中')).toBeInTheDocument();
    expect(screen.getByTestId('device-drawer-finishing-kind')).toHaveTextContent('解除配置中');
    expect(screen.queryByText('收尾截止')).toBeNull();
  });
});
