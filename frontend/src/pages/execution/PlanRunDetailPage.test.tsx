import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import PlanRunDetailPage from './PlanRunDetailPage';
import { ToastProvider } from '@/components/ui/toast';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getRun: vi.fn(),
  getChain: vi.fn(),
  getTimeline: vi.fn(),
  getEvents: vi.fn(),
  getDevices: vi.fn(),
  getWatcherSummary: vi.fn(),
  abort: vi.fn(),
  manualRetryJob: vi.fn(),
  manualExitJob: vi.fn(),
  socketCallback: { current: undefined as undefined | ((msg: any) => void) },
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
      getChain: mocks.getChain,
      getTimeline: mocks.getTimeline,
      getEvents: mocks.getEvents,
      getDevices: mocks.getDevices,
      getWatcherSummary: mocks.getWatcherSummary,
      abort: mocks.abort,
      manualRetryJob: mocks.manualRetryJob,
      manualExitJob: mocks.manualExitJob,
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

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <PlanRunDetailPage />
      </ToastProvider>
    </QueryClientProvider>,
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
                expected_sha256: 'abcdef0123',
                actual_sha256: 'abcdef0123',
                matched: true,
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
                expected_sha256: 'abcdef0123',
                actual_sha256: 'deadbeef99',
                matched: false,
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
  mocks.getChain.mockResolvedValue({
    plan_run_id: 12,
    root_plan_run_id: 12,
    nodes: [
      {
        plan_id: 7,
        plan_name: '24h 烧机',
        plan_run_id: 12,
        status: 'RUNNING',
        chain_index: 0,
        failure_threshold: 0.05,
        is_current: true,
        is_blocked: false,
      },
    ],
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
    total: 1,
    events: [
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
    ],
    facets: {
      by_stage: { all: 1, patrol: 1 },
      by_severity: { all: 1, err: 1 },
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
  mocks.getWatcherSummary.mockResolvedValue({
    plan_run_id: 12,
    window_minutes: 60,
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
  it('renders Topbar / PlanChain / DispatchGate / Timeline / DeviceMatrix / Watcher', async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId('plan-run-status-pill')).toHaveTextContent('RUNNING'),
    );
    expect(screen.getByTestId('chain-node-7')).toHaveTextContent('24h 烧机');
    expect(screen.getByTestId('dispatch-gate-card')).toHaveTextContent(
      '同步漂移主机',
    );
    expect(screen.getByTestId('dispatch-gate-host-host-202')).toHaveTextContent(
      '同步中',
    );
    expect(screen.getByTestId('business-flow-timeline')).toBeInTheDocument();
    expect(await screen.findByTestId('device-matrix')).toBeInTheDocument();
    expect(await screen.findByTestId('watcher-summary')).toBeInTheDocument();
    // BACKOFF row visible with red failure streak
    expect(await screen.findByTestId('device-row-3002')).toHaveTextContent('退避');
    // Threshold banner since exceeded=true
    expect(await screen.findByTestId('watcher-threshold-banner')).toBeInTheDocument();
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
    fireEvent.click(await screen.findByTestId('device-row-3002'));
    expect(await screen.findByTestId('device-drawer')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('device-drawer-retry-btn'));
    fireEvent.click(await screen.findByTestId('device-drawer-confirm'));
    await waitFor(() => expect(mocks.manualRetryJob).toHaveBeenCalledWith(12, 3002));
  });

  it('opens device report via the drawer', async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId('device-row-3001'));
    fireEvent.click(await screen.findByTestId('device-drawer-open-report'));
    expect(mocks.navigate).toHaveBeenCalledWith('/runs/3001/report');
  });

  it('invalidates devices+timeline on JOB_STATUS push and watcher on WATCHER_SIGNAL', async () => {
    renderPage();
    await waitFor(() => screen.getByTestId('device-matrix'));
    expect(typeof mocks.socketCallback.current).toBe('function');

    // Reset call counts to isolate post-mount invalidation behaviour.
    mocks.getDevices.mockClear();
    mocks.getTimeline.mockClear();
    mocks.getEvents.mockClear();
    mocks.getWatcherSummary.mockClear();
    mocks.getRun.mockClear();
    mocks.getChain.mockClear();

    // Push a JOB_STATUS event — devices/timeline/events should refetch, but
    // watcher should not (only WATCHER_SIGNAL invalidates watcher).
    mocks.socketCallback.current!({
      type: 'JOB_STATUS',
      payload: { job_id: 3002, status: 'RUNNING' },
    });
    await waitFor(() => expect(mocks.getDevices).toHaveBeenCalled());
    expect(mocks.getTimeline).toHaveBeenCalled();
    expect(mocks.getEvents).toHaveBeenCalled();
    expect(mocks.getWatcherSummary).not.toHaveBeenCalled();

    mocks.getEvents.mockClear();
    mocks.getDevices.mockClear();

    // Push a WATCHER_SIGNAL — watcher + events should refetch, devices should not.
    mocks.socketCallback.current!({
      type: 'WATCHER_SIGNAL',
      payload: { job_id: 3002, category: 'AEE', inserted_count: 1 },
    });
    await waitFor(() => expect(mocks.getWatcherSummary).toHaveBeenCalled());
    expect(mocks.getEvents).toHaveBeenCalled();
    expect(mocks.getDevices).not.toHaveBeenCalled();

    // Reset and push PLAN_RUN_STATUS — should refetch run + chain + timeline + devices.
    mocks.getRun.mockClear();
    mocks.getChain.mockClear();
    mocks.getTimeline.mockClear();
    mocks.getDevices.mockClear();
    mocks.socketCallback.current!({
      type: 'PLAN_RUN_STATUS',
      payload: { status: 'SUCCESS' },
    });
    await waitFor(() => expect(mocks.getRun).toHaveBeenCalled());
    expect(mocks.getChain).toHaveBeenCalled();
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
    expect(screen.queryByTestId('dispatch-gate-card')).not.toBeInTheDocument();
    // Topbar should NOT render the abort button on terminal runs.
    expect(screen.queryByTestId('plan-run-abort-btn')).not.toBeInTheDocument();
  });
});
