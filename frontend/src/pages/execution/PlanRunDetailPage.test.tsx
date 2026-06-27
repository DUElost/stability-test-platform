import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import PlanRunDetailPage from './PlanRunDetailPage';
import { Toaster } from '@/components/ui/Toaster';

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

// Mock AnomalyDashboard with a stable testid so Signals tab assertions work.
vi.mock('@/components/plan-run/AnomalyDashboard', () => ({
  default: () => <div data-testid="watcher-summary" />,
}));

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <PlanRunDetailPage />
        <Toaster />
      </QueryClientProvider>
    </MemoryRouter>,
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
    plan_name: '24h 烧机',
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
    total: 0,
    events: [],
    facets: { by_stage: { all: 0 }, by_severity: { all: 0 } },
  });
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
  mocks.abort.mockResolvedValue({ plan_run_id: 12, status: 'FAILED' });
  mocks.retryDispatch.mockResolvedValue({
    plan_run_id: 12,
    status: 'RUNNING',
    dispatch_state: { status: 'queued' },
  });
  mocks.socketCallback.current = undefined;
});

describe('PlanRunDetailPage', () => {
  it('renders overview tab by default with status banner and meta', async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId('plan-run-status-banner')).toHaveTextContent('RUNNING'),
    );
    expect(screen.getByText('PlanRun 正在执行中')).toBeInTheDocument();
    expect(screen.getByText('设备数')).toBeInTheDocument();
    expect(screen.getByText('PlanRun 信息')).toBeInTheDocument();
    expect(screen.getByText('Plan ID: 7')).toBeInTheDocument();
  });

  it('switches to devices tab and renders the device table', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole('tab', { name: '设备' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('tab', { name: '设备' }));
    await waitFor(() => expect(screen.getByText('DEV-AAAA')).toBeInTheDocument());
    expect(screen.getByText('DEV-BBBB')).toBeInTheDocument();
  });

  it('switches to signals tab and renders the watcher summary', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Signals' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('tab', { name: 'Signals' }));
    expect(await screen.findByTestId('watcher-summary')).toBeInTheDocument();
  });

  it('switches to timeline tab and renders the business flow stepper', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole('tab', { name: '时间线' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('tab', { name: '时间线' }));
    expect(await screen.findByTestId('business-flow-stepper')).toBeInTheDocument();
  });

  it('aborts the PlanRun via the topbar cancel button', async () => {
    renderPage();
    await waitFor(() => screen.getByTestId('plan-run-abort-btn'));
    fireEvent.click(screen.getByTestId('plan-run-abort-btn'));
    await waitFor(() => expect(mocks.abort).toHaveBeenCalledWith(12, 'aborted_by_user'));
  });

  it('retries dispatch via the topbar retry button', async () => {
    mocks.getRun.mockResolvedValueOnce({
      id: 12,
      plan_id: 7,
      status: 'FAILED',
      failure_threshold: 0.05,
      run_type: 'MANUAL',
      triggered_by: 'tester@local',
      started_at: new Date(Date.now() - 90_000).toISOString(),
      ended_at: new Date(Date.now() - 30_000).toISOString(),
      plan_name: '24h 烧机',
    });
    renderPage();
    await waitFor(() => screen.getByTestId('plan-run-retry-btn'));
    fireEvent.click(screen.getByTestId('plan-run-retry-btn'));
    await waitFor(() => expect(mocks.retryDispatch).toHaveBeenCalledWith(12));
  });

  it('renders breadcrumb link back to the PlanRun list', async () => {
    renderPage();
    const link = (await screen.findByText('Plan Runs')).closest('a');
    expect(link).toHaveAttribute('href', '/execution/plan-runs');
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

    await waitFor(() => {
      expect(mocks.exportReport).toHaveBeenCalledWith(12, 'markdown');
    });
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    clickSpy.mockRestore();
    vi.unstubAllGlobals();
  });

  it('invalidates devices+timeline on JOB_STATUS push and watcher on WATCHER_SIGNAL', async () => {
    renderPage();
    await waitFor(() => screen.getByText('设备数'));
    expect(typeof mocks.socketCallback.current).toBe('function');

    // Reset call counts to isolate post-mount invalidation behaviour.
    mocks.getDevices.mockClear();
    mocks.getTimeline.mockClear();
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
});
