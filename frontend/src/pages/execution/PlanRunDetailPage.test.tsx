import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import PlanRunDetailPage from './PlanRunDetailPage';
import { HeaderSlotProvider, useHeaderSlot } from '@/contexts/HeaderSlotContext';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getRun: vi.fn(),
  getTimeline: vi.fn(),
  getEvents: vi.fn(),
  getDevices: vi.fn(),
  getWatcherSummary: vi.fn(),
  getChain: vi.fn(),
  abort: vi.fn(),
  manualRetryJob: vi.fn(),
  manualExitJob: vi.fn(),
  exportReport: vi.fn(),
  retryDispatch: vi.fn(),
  getDedupStatus: vi.fn().mockResolvedValue({ plan_run_id: 12, artifacts: [] }),
  listJobArtifacts: vi.fn().mockResolvedValue([]),
  triggerExtract: vi.fn().mockResolvedValue({ plan_run_id: 12, extracted_count: 0 }),
  triggerScan: vi.fn().mockResolvedValue({ plan_run_id: 12, triggered_hosts: [], skipped_offline: [] }),
  triggerMerge: vi.fn().mockResolvedValue({ status: 'ok', plan_run_id: 12 }),
  socketCallback: { current: undefined as undefined | ((msg: any) => void) },
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    promise: vi.fn(),
  },
}));

vi.mock('@/hooks/useToast', () => ({
  useToast: () => mocks.toast,
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
    useParams: () => ({ runId: '12' }),
  };
});

vi.mock('@/utils/api', () => ({
  api: {
    planRuns: {
      get: mocks.getRun,
      getTimeline: mocks.getTimeline,
      getEvents: mocks.getEvents,
      getDevices: mocks.getDevices,
      getWatcherSummary: mocks.getWatcherSummary,
      getChain: mocks.getChain,
      abort: mocks.abort,
      manualRetryJob: mocks.manualRetryJob,
      manualExitJob: mocks.manualExitJob,
      exportReport: mocks.exportReport,
      retryDispatch: mocks.retryDispatch,
      getDedupStatus: mocks.getDedupStatus,
      listJobArtifacts: mocks.listJobArtifacts,
      triggerExtract: mocks.triggerExtract,
      triggerScan: mocks.triggerScan,
      triggerMerge: mocks.triggerMerge,
    },
  },
}));

vi.mock('@/hooks/useSocketIO', () => ({
  useSocketIO: (_url: string, opts?: { onMessage?: (m: any) => void }) => {
    if (opts?.onMessage) mocks.socketCallback.current = opts.onMessage;
    return {
      isConnected: true,
      connectionStatus: 'connected',
      lastMessage: null,
      sendMessage: vi.fn(),
      reconnectAttempt: 0,
      connect: vi.fn(),
      disconnect: vi.fn(),
    };
  },
}));

// Mock AnomalyDashboard with backward-compatible testids so existing assertions still pass.
vi.mock('@/components/plan-run/AnomalyDashboard', () => ({
  default: () => <div data-testid="watcher-summary" />,
}));

/** 模拟 AppShell 消费 HeaderSlotContext,把页面注入的顶栏内容渲染到 DOM。 */
function HeaderSlotOutlet() {
  const { headerSlot } = useHeaderSlot();
  return <>{headerSlot}</>;
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <HeaderSlotProvider>
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <HeaderSlotOutlet />
          <PlanRunDetailPage />
        </QueryClientProvider>
      </MemoryRouter>
    </HeaderSlotProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getRun.mockResolvedValue({
    id: 12,
    plan_id: 7,
    status: 'RUNNING',
    failure_threshold: 0.05,
    run_type: 'MANUAL',
    triggered_by: 'tester@local',
    started_at: new Date(Date.now() - 90_000).toISOString(),
    ended_at: null,
    run_context: {
      precheck: {
        phase: 'syncing',
        started_at: '2026-05-08T11:59:00Z',
        completed_at: null,
        hosts: {
          'host-101': {
            status: 'ok',
            checked_at: '2026-05-08T11:59:10Z',
            synced_at: null,
            scripts: [
              {
                name: 'monkey_check',
                version: '1.0.0',
                expected_sha: 'abcdef0123',
                actual_sha: 'abcdef0123',
                exists: true,
                ok: true,
              },
            ],
            sync_attempts: 0,
            error: null,
          },
          'host-202': {
            status: 'syncing',
            checked_at: '2026-05-08T11:59:11Z',
            synced_at: null,
            scripts: [
              {
                name: 'monkey_check',
                version: '1.0.0',
                expected_sha: 'abcdef0123',
                actual_sha: 'deadbeef99',
                exists: true,
                ok: false,
              },
            ],
            sync_attempts: 1,
            error: null,
          },
        },
        final_result: null,
        errors: [],
      },
    },
  });
  mocks.getTimeline.mockResolvedValue({
    plan_run_id: 12,
    current_stage: 'patrol',
    plan_name: '24h 烧机',
    triggered_at: new Date(Date.now() - 90_000).toISOString(),
    triggered_by: 'tester@local',
    run_type: 'MANUAL',
    stages: [
      {
        stage: 'init',
        status: 'completed',
        device_total: 8,
        device_succeeded: 8,
        device_failed: 0,
        steps: [],
      },
      {
        stage: 'patrol',
        status: 'running',
        device_total: 8,
        device_succeeded: 7,
        device_failed: 1,
        patrol_cycle_index: 142,
        patrol_interval_seconds: 60,
        steps: [],
      },
      {
        stage: 'teardown',
        status: 'pending',
        device_total: 0,
        device_succeeded: 0,
        device_failed: 0,
        steps: [],
      },
    ],
  });
  mocks.getEvents.mockResolvedValue({
    plan_run_id: 12,
    total: 3,
    events: [
      {
        ts: '2026-05-08T12:31:20Z',
        stage: 'patrol',
        severity: 'info',
        category: 'system',
        title: 'PATROL 进行中 · 周期 #12',
        description: '最近 3 分钟内 1 台设备上报心跳',
      },
      {
        ts: '2026-05-08T12:30:00Z',
        stage: 'patrol',
        severity: 'err',
        category: 'step',
        title: 'monkey_check 失败',
        description: 'DEV-3064 连续失败',
        device_serial: 'DEV-3064',
        job_id: 3064,
      },
      {
        ts: '2026-05-08T12:00:00Z',
        stage: 'trigger',
        severity: 'ok',
        category: 'trigger',
        title: 'PlanRun #12 启动',
        description: '触发方式 MANUAL · 用户 tester@local',
      },
    ],
    facets: {
      by_stage: { all: 3, trigger: 1, patrol: 2 },
      by_severity: { all: 3, ok: 1, info: 1, err: 1 },
    },
  });
  mocks.abort.mockResolvedValue({ plan_run_id: 12, status: 'FAILED' });
  mocks.getDevices.mockResolvedValue({
    plan_run_id: 12,
    total: 2,
    by_status: { all: 2, running: 1, backoff: 1 },
    by_host: { 'host-101': 2 },
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
        last_heartbeat_at: null,
        started_at: null,
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
        next_retry_at: new Date(Date.now() + 30_000).toISOString(),
        manual_action: null,
        log_signal_count: 2,
        last_heartbeat_at: null,
        started_at: null,
        ended_at: null,
      },
    ],
  });
  mocks.getChain.mockResolvedValue({
    plan_run_id: 12,
    root_plan_run_id: 12,
    nodes: [],
  });
  mocks.getWatcherSummary.mockResolvedValue({
    plan_run_id: 12,
    time_scope: 'all',
    window_minutes: null,
    window_start_at: '2026-05-08T11:30:00Z',
    window_end_at: '2026-05-08T12:30:00Z',
    categories: [
      {
        category: 'AEE',
        count: 2,
        affected_device_count: 1,
        trend_change: 1,
        latest_device_serial: 'DEV-BBBB',
        latest_detected_at: '2026-05-08T12:25:00Z',
      },
    ],
    total: 2,
    affected_device_count: 1,
    total_devices: 2,
    abnormal_rate: 0.5,
    threshold: 0.05,
    exceeded: true,
    supports_origin_split: true,
    current_run: {
      total_events: 2,
      affected_device_count: 1,
      top_package_name: 'com.runtime.camera',
      top_subtype: 'JE',
      subtype_distribution: [{ subtype: 'JE', group: 'AEE', count: 2, share: 1 }],
      package_ranking: [
        {
          package_name: 'com.runtime.camera',
          total_count: 2,
          affected_device_count: 1,
          latest_detected_at: '2026-05-08T12:25:00Z',
          subtype_breakdown: [{ subtype: 'JE', count: 2 }],
        },
      ],
    },
    preexisting: {
      total_events: 0,
      affected_device_count: 0,
      top_package_name: null,
      top_subtype: null,
      subtype_distribution: [],
      package_ranking: [],
    },
  });
  mocks.manualRetryJob.mockResolvedValue({
    job_id: 3002,
    plan_run_id: 12,
    action: 'manual_retry',
    status: 'RUNNING',
    manual_action: 'RETRY_NOW',
    next_retry_at: new Date().toISOString(),
    current_failure_streak: 4,
  });
  mocks.manualExitJob.mockResolvedValue({
    job_id: 3002,
    plan_run_id: 12,
    action: 'manual_exit',
    status: 'RUNNING',
    manual_action: 'EXIT_REQUESTED',
    current_failure_streak: 4,
  });
  mocks.socketCallback.current = undefined;
});

describe('PlanRunDetailPage', () => {
  it('renders Hero / Minimap / Stepper / DeviceTable / Watcher', async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId('plan-run-status-pill')).toHaveTextContent('RUNNING'),
    );
    expect(screen.getByTestId('precheck-row')).toHaveTextContent('健康预检');
    expect(screen.getByTestId('precheck-row')).toHaveTextContent('host-202');
    expect(screen.getByTestId('precheck-row')).toHaveTextContent('1/2');
    // BusinessFlowStepper replaces BusinessFlowTimeline
    expect(screen.getByTestId('business-flow-stepper')).toBeInTheDocument();
    expect(await screen.findByTestId('device-overview')).toBeInTheDocument();
    // AnomalyDashboard (mocked) uses watcher-summary testid for backward compat
    expect(await screen.findByTestId('watcher-summary')).toBeInTheDocument();
    // Switch to table view to verify row content
    fireEvent.click(screen.getByTestId('device-overview-table-btn'));
    // BACKOFF row visible with red failure streak
    expect(await screen.findByTestId('device-row-3002')).toHaveTextContent('退避');
    // 概览/日志 tab 存在;逐条事件流已迁至日志页(详情页无 event-list)
    expect(screen.getByTestId('plan-run-tabs')).toBeInTheDocument();
    expect(screen.queryByTestId('event-list')).not.toBeInTheDocument();
  });

  it('aborts the PlanRun via the Topbar confirm dialog', async () => {
    renderPage();
    await waitFor(() => screen.getByTestId('plan-run-abort-btn'));
    fireEvent.click(screen.getByTestId('plan-run-abort-btn'));
    fireEvent.click(await screen.findByTestId('plan-run-abort-confirm'));
    await waitFor(() => expect(mocks.abort).toHaveBeenCalledWith(12, 'aborted_by_user'));
  });

  it('navigates back to the PlanRun list', async () => {
    renderPage();
    fireEvent.click(await screen.findByText(/返回执行列表/));
    expect(mocks.navigate).toHaveBeenCalledWith('/execution/plan-runs');
  });

  it('opens the device drawer and triggers manual retry via confirm dialog', async () => {
    renderPage();
    // Click grid cell to open drawer (default grid view in DeviceOverview)
    fireEvent.click(await screen.findByTestId('minimap-cell-3002'));
    expect(await screen.findByTestId('device-drawer')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('device-drawer-retry-btn'));
    fireEvent.click(await screen.findByTestId('device-drawer-confirm'));
    await waitFor(() => expect(mocks.manualRetryJob).toHaveBeenCalledWith(12, 3002));
  });

  it('opens device report via the drawer', async () => {
    renderPage();
    // Click grid cell to open drawer (default grid view in DeviceOverview)
    fireEvent.click(await screen.findByTestId('minimap-cell-3001'));
    fireEvent.click(await screen.findByTestId('device-drawer-open-report'));
    expect(mocks.navigate).toHaveBeenCalledWith('/runs/3001/report');
  });

  it('renders device overview and business-flow stepper in the details view', async () => {
    renderPage();
    // Overview content is always visible (no inner tab switching)
    expect(await screen.findByTestId('device-overview')).toBeInTheDocument();
    expect(await screen.findByTestId('business-flow-stepper')).toBeInTheDocument();
  });

  it('invalidates devices+timeline on JOB_STATUS push and watcher on WATCHER_SIGNAL', async () => {
    renderPage();
    await waitFor(() => screen.getByTestId('device-overview'));
    expect(typeof mocks.socketCallback.current).toBe('function');

    // Reset call counts to isolate post-mount invalidation behaviour.
    mocks.getDevices.mockClear();
    mocks.getTimeline.mockClear();
    mocks.getEvents.mockClear();
    mocks.getWatcherSummary.mockClear();
    mocks.getRun.mockClear();

    // Push a JOB_STATUS event — devices/timeline should refetch, but
    // watcher should not (only WATCHER_SIGNAL invalidates watcher).
    mocks.socketCallback.current!({
      type: 'JOB_STATUS',
      payload: { job_id: 3002, status: 'RUNNING' },
    });
    await waitFor(() => expect(mocks.getDevices).toHaveBeenCalled());
    expect(mocks.getTimeline).toHaveBeenCalled();
    expect(mocks.getWatcherSummary).not.toHaveBeenCalled();

    mocks.getDevices.mockClear();

    // Push a WATCHER_SIGNAL — watcher should refetch (debounced 2s), devices should not.
    mocks.socketCallback.current!({
      type: 'WATCHER_SIGNAL',
      payload: { job_id: 3002, category: 'AEE', inserted_count: 1 },
    });
    // WATCHER_SIGNAL invalidation is debounced 2s; wait for it to fire.
    await waitFor(() => expect(mocks.getWatcherSummary).toHaveBeenCalled(), { timeout: 4000 });
    expect(mocks.getDevices).not.toHaveBeenCalled();

    // Reset and push PLAN_RUN_STATUS — should refetch run + timeline + devices.
    mocks.getRun.mockClear();
    mocks.getTimeline.mockClear();
    mocks.getDevices.mockClear();
    mocks.socketCallback.current!({
      type: 'PLAN_RUN_STATUS',
      payload: { status: 'SUCCESS' },
    });
    await waitFor(() => expect(mocks.getRun).toHaveBeenCalled());
    expect(mocks.getTimeline).toHaveBeenCalled();
    expect(mocks.getDevices).toHaveBeenCalled();

    // Reset and push PRECHECK_UPDATE — should refetch run + timeline + devices.
    mocks.getRun.mockClear();
    mocks.getTimeline.mockClear();
    mocks.getDevices.mockClear();
    mocks.socketCallback.current!({
      type: 'PRECHECK_UPDATE',
      payload: { phase: 'syncing', dispatch_status: 'running' },
    });
    await waitFor(() => expect(mocks.getRun).toHaveBeenCalled());
    expect(mocks.getTimeline).toHaveBeenCalled();
    expect(mocks.getDevices).toHaveBeenCalled();
  });

  it('hides the dispatch gate card when precheck is absent', async () => {
    mocks.getRun.mockResolvedValueOnce({
      id: 12,
      plan_id: 7,
      status: 'SUCCESS',
      failure_threshold: 0.05,
      run_type: 'MANUAL',
      triggered_by: 'tester@local',
      started_at: '2026-05-08T11:00:00Z',
      ended_at: '2026-05-08T11:30:00Z',
      run_context: null,
    });
    renderPage();
    await waitFor(() => screen.getByTestId('plan-run-status-pill'));
    expect(screen.queryByTestId('precheck-row')).not.toBeInTheDocument();
    // Hero should NOT render the abort button on terminal runs.
    expect(screen.queryByTestId('plan-run-abort-btn')).not.toBeInTheDocument();
  });

  it('keeps precheck summary visible for active runs after precheck ready', async () => {
    mocks.getRun.mockResolvedValueOnce({
      id: 12,
      plan_id: 7,
      status: 'RUNNING',
      failure_threshold: 0.05,
      run_type: 'MANUAL',
      triggered_by: 'tester@local',
      started_at: '2026-05-08T11:00:00Z',
      ended_at: null,
      run_context: {
        precheck: {
          phase: 'ready',
          started_at: '2026-05-08T11:00:05Z',
          completed_at: '2026-05-08T11:01:00Z',
          hosts: {
            'host-101': {
              status: 'ok',
              checked_at: '2026-05-08T11:00:10Z',
              synced_at: null,
              scripts: [],
              sync_attempts: 0,
              error: null,
            },
          },
          final_result: 'ready',
          errors: [],
        },
        dispatch_state: {
          status: 'completed',
          enqueued_at: '2026-05-08T11:01:01Z',
          started_at: '2026-05-08T11:01:05Z',
          completed_at: '2026-05-08T11:01:20Z',
          last_error: null,
        },
      },
    });

    renderPage();

    await waitFor(() => screen.getByTestId('plan-run-status-pill'));
    expect(screen.getByTestId('precheck-row')).toBeInTheDocument();
    expect(screen.getByTestId('precheck-row')).toHaveTextContent('通过');
  });

  it('keeps host details visible while dispatch is still running after precheck ready', async () => {
    mocks.getRun.mockResolvedValueOnce({
      id: 12,
      plan_id: 7,
      status: 'RUNNING',
      failure_threshold: 0.05,
      run_type: 'MANUAL',
      triggered_by: 'tester@local',
      started_at: '2026-05-08T11:00:00Z',
      ended_at: null,
      run_context: {
        precheck: {
          phase: 'syncing',
          started_at: '2026-05-08T11:00:05Z',
          completed_at: null,
          hosts: {
            'host-101': {
              status: 'syncing',
              checked_at: '2026-05-08T11:00:10Z',
              synced_at: null,
              scripts: [],
              sync_attempts: 1,
              error: null,
            },
          },
          final_result: null,
          errors: [],
        },
        dispatch_state: {
          status: 'running',
          enqueued_at: '2026-05-08T11:01:01Z',
          started_at: '2026-05-08T11:01:05Z',
          completed_at: null,
          last_error: null,
        },
      },
    });

    renderPage();

    await waitFor(() => screen.getByTestId('plan-run-status-pill'));
    expect(screen.getByTestId('precheck-row')).toBeInTheDocument();
    expect(screen.getByTestId('precheck-row')).toHaveTextContent('同步中');
    expect(screen.getByTestId('precheck-row')).toHaveTextContent('host-101');
  });

  it('shows chain dispatch failure banner when result_summary records failure', async () => {
    mocks.getRun.mockResolvedValueOnce({
      id: 12,
      plan_id: 7,
      status: 'SUCCESS',
      failure_threshold: 0.05,
      run_type: 'MANUAL',
      triggered_by: 'tester@local',
      started_at: '2026-05-08T11:00:00Z',
      ended_at: '2026-05-08T13:00:00Z',
      result_summary: {
        total: 2,
        completed: 2,
        pass_rate: 1,
        chain_dispatch_failed: {
          at: '2026-05-08T13:00:01Z',
          error: 'Plan 8: scripts unavailable at dispatch',
        },
      },
      run_context: null,
    });
    mocks.getChain.mockResolvedValueOnce({
      plan_run_id: 12,
      root_plan_run_id: 12,
      nodes: [
        {
          plan_id: 7,
          plan_name: '24h 烧机',
          plan_run_id: 12,
          status: 'SUCCESS',
          chain_index: 0,
          failure_threshold: 0.05,
          pass_rate: 1,
          is_current: true,
          is_blocked: false,
        },
        {
          plan_id: 11,
          plan_name: '后置回收',
          plan_run_id: null,
          status: 'pending',
          chain_index: 1,
          failure_threshold: 0.1,
          is_current: false,
          is_blocked: true,
          block_reason: '下游 Plan 派发失败: Plan 8: scripts unavailable at dispatch',
        },
      ],
    });

    renderPage();

    const banner = await screen.findByTestId('chain-dispatch-failed-banner');
    expect(banner).toHaveTextContent('下游 Plan 派发失败');
    expect(banner).toHaveTextContent('scripts unavailable');
    expect(screen.getByTestId('chain-node-11')).toHaveTextContent('暂不触发');
  });

  it('shows stuck-jobs banner when RUNNING job patrol heartbeat is stale', async () => {
    mocks.getDevices.mockResolvedValueOnce({
      plan_run_id: 12,
      total: 1,
      by_status: { all: 1, running: 1 },
      by_host: { 'host-101': 1 },
      devices: [
        {
          device_id: 1,
          device_serial: 'DEV-STALE',
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
          last_heartbeat_at: new Date(Date.now() - 300_000).toISOString(),
          started_at: new Date(Date.now() - 600_000).toISOString(),
          ended_at: null,
        },
      ],
    });

    renderPage();

    const banner = await screen.findByTestId('stuck-jobs-banner');
    expect(banner).toHaveTextContent('1 个 Job 心跳超时');
    expect(banner).toHaveTextContent('可能已断开');
    expect(banner).toHaveTextContent('DEV-STALE');
  });

  it('renders agent_offline precheck failure in dispatch gate', async () => {
    mocks.getRun.mockResolvedValueOnce({
      id: 12,
      plan_id: 7,
      status: 'FAILED',
      failure_threshold: 0.05,
      run_type: 'MANUAL',
      triggered_by: 'tester@local',
      started_at: '2026-05-08T11:00:00Z',
      ended_at: '2026-05-08T11:00:30Z',
      run_context: {
        precheck: {
          phase: 'failed',
          started_at: '2026-05-08T11:00:00Z',
          completed_at: '2026-05-08T11:00:30Z',
          final_result: 'failed',
          errors: ['agent_offline: host-202'],
          sync_max_attempts: 1,
          hosts: {
            'host-202': {
              status: 'failed',
              checked_at: '2026-05-08T11:00:11Z',
              synced_at: null,
              scripts: [],
              sync_attempts: 0,
              error: 'agent_offline',
            },
          },
        },
        dispatch_state: {
          status: 'failed',
          enqueued_at: '2026-05-08T11:00:00Z',
          started_at: '2026-05-08T11:00:05Z',
          completed_at: '2026-05-08T11:00:30Z',
          last_error: 'precheck:agent_offline: host-202',
        },
      },
    });
    renderPage();
    await waitFor(() => screen.getByTestId('dispatch-gate-card'));
    expect(screen.getAllByText(/agent_offline: host-202/).length).toBeGreaterThan(0);
    expect(screen.getByTestId('dispatch-gate-host-host-202')).toHaveTextContent(
      'agent_offline',
    );
  });

  it('surfaces mixed watcher failure in precheck summary row', async () => {
    mocks.getRun.mockResolvedValueOnce({
      id: 12,
      plan_id: 7,
      status: 'FAILED',
      failure_threshold: 0.05,
      run_type: 'MANUAL',
      triggered_by: 'tester@local',
      started_at: '2026-05-08T11:00:00Z',
      ended_at: '2026-05-08T11:00:30Z',
      run_context: {
        precheck: {
          phase: 'failed',
          started_at: '2026-05-08T11:00:00Z',
          completed_at: '2026-05-08T11:00:30Z',
          final_result: 'failed',
          errors: ['watch激活与不激活的节点不能同时在一个计划中'],
          sync_max_attempts: 1,
          gate_failure: {
            code: 'MIXED_WATCHER_ACTIVITY',
            message: 'watch激活与不激活的节点不能同时在一个计划中',
            inactive_host_ids: ['host-202', 'host-303'],
          },
          hosts: {
            'host-101': {
              status: 'pending',
              checked_at: null,
              synced_at: null,
              scripts: [],
              sync_attempts: 0,
              error: null,
            },
            'host-202': {
              status: 'failed',
              checked_at: null,
              synced_at: null,
              scripts: [],
              sync_attempts: 0,
              error: 'watcher_inactive',
            },
            'host-303': {
              status: 'failed',
              checked_at: null,
              synced_at: null,
              scripts: [],
              sync_attempts: 0,
              error: 'watcher_inactive',
            },
          },
        },
        dispatch_state: {
          status: 'failed',
          enqueued_at: '2026-05-08T11:00:00Z',
          started_at: '2026-05-08T11:00:05Z',
          completed_at: '2026-05-08T11:00:30Z',
          last_error: 'precheck:MIXED_WATCHER_ACTIVITY',
        },
      },
    });

    renderPage();

    const row = await screen.findByTestId('precheck-row');
    expect(row).toHaveTextContent('失败');
    expect(row).toHaveTextContent('watch激活与不激活的节点不能同时在一个计划中');
    expect(row).toHaveTextContent('不激活节点ID：host-202, host-303');
  });

  it('exports report via backend API', async () => {
    const blob = new Blob(['# PlanRun #12 Report'], { type: 'text/plain' });
    mocks.exportReport.mockResolvedValueOnce(blob);
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    const createObjectURL = vi.fn(() => 'blob:mock');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL,
      revokeObjectURL,
    });

    renderPage();
    await waitFor(() => screen.getByTestId('plan-run-export-btn'));
    fireEvent.click(screen.getByTestId('plan-run-export-btn'));
    fireEvent.click(await screen.findByTestId('plan-run-export-md'));

    await waitFor(() => {
      expect(mocks.exportReport).toHaveBeenCalledWith(12, 'markdown');
    });
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    clickSpy.mockRestore();
    vi.unstubAllGlobals();
  });
});
