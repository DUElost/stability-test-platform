import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import PlanExecutePage from './PlanExecutePage';
import { api, ApiError, fetchAllDevices, fetchHostList } from '@/utils/api';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
  };
});

vi.mock('@/hooks/useToast', () => ({
  useToast: () => mocks.toast,
}));

vi.mock('@/utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return {
    ...actual,
    fetchHostList: vi.fn().mockResolvedValue([]),
    fetchAllDevices: vi.fn().mockResolvedValue([]),
    api: {
      plans: {
        list: vi.fn(),
        previewRun: vi.fn(),
        run: vi.fn(),
      },
      planRuns: {
        retryDispatch: vi.fn(),
      },
      devices: {
        list: vi.fn(),
      },
      hosts: {
        list: vi.fn(),
        get: vi.fn(),
      },
    },
  };
});

function renderPage({
  plans = [
    {
      id: 7,
      name: 'Smoke Plan',
      description: null,
      steps: [{ step_key: 'check_device' }],
      failure_threshold: 0.05,
      patrol_interval_seconds: 3600,
      timeout_seconds: 7200,
    },
  ],
  devices = [],
  plansFailure,
  devicesFailure,
  initialEntry = '/execution/plan-execute?plan=7',
  hosts = [],
  hostDetail = { id: 'h1', status: 'ONLINE', active_jobs: [] },
}: {
  plans?: any[];
  devices?: any[];
  plansFailure?: Error;
  devicesFailure?: Error;
  initialEntry?: string;
  hosts?: any[];
  hostDetail?: any;
} = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  if (plansFailure) (api.plans.list as any).mockRejectedValue(plansFailure);
  else (api.plans.list as any).mockResolvedValue(plans);
  if (devicesFailure) (fetchAllDevices as any).mockRejectedValue(devicesFailure);
  else (fetchAllDevices as any).mockResolvedValue(devices);
  (api.plans.previewRun as any).mockResolvedValue({
    plan_name: 'Smoke Plan',
    device_count: 1,
    job_count: 1,
    total_steps: 1,
  });
  (api.plans.run as any).mockResolvedValue({ id: 88 });
  (fetchHostList as any).mockResolvedValue(hosts);
  (api.hosts.get as any).mockResolvedValue(hostDetail);
  (api.planRuns.retryDispatch as any).mockResolvedValue({ plan_run_id: 88, status: 'RUNNING' });

  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <QueryClientProvider client={queryClient}>
        <PlanExecutePage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

async function goToDeviceStep() {
  // 等 plans query 落定（选中 Plan 概览卡渲染后）再点步骤条，
  // 否则 handleStepChange 守卫（executableStepCount === 0）会把向导弹回第 0 步
  await screen.findByText(/启用步骤/);
  fireEvent.click(screen.getByRole('button', { name: /先定位节点/ }));
}

function selectFirstNode() {
  const node = screen.getAllByRole('button').find(button => /auto-|h1/.test(button.textContent ?? ''));
  if (node) fireEvent.click(node);
}

describe('PlanExecutePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('disables BUSY devices and excludes them from available count', async () => {
    renderPage({
      devices: [
        {
          id: 2429,
          serial: '457854125444LMKJ',
          model: 'Infinix_X6851',
          host_id: 'auto-fdaf1d55e319',
          status: 'BUSY',
        },
        {
          id: 2430,
          serial: 'FREE123',
          model: 'Infinix_X6851',
          host_id: 'auto-fdaf1d55e319',
          status: 'ONLINE',
        },
      ],
    });

    await goToDeviceStep();

    const busyCheckbox = await screen.findByLabelText(/457854125444LMKJ/i);
    const freeCheckbox = screen.getByLabelText(/FREE123/i);

    expect(busyCheckbox).toBeDisabled();
    expect(screen.getByText(/已选 0 \/ 1 台可用/)).toBeInTheDocument();

    fireEvent.click(busyCheckbox);
    expect(screen.getByText(/已选 0 \/ 1 台可用/)).toBeInTheDocument();

    fireEvent.click(freeCheckbox);
    expect(screen.getByText(/已选 1 \/ 1 台可用/)).toBeInTheDocument();
  });

  it('prefers backend schedulable over raw device status', async () => {
    renderPage({
      devices: [
        { id: 1, serial: 'ONLINE-BLOCKED', host_id: 'h1', status: 'ONLINE', schedulable: false },
        { id: 2, serial: 'BUSY-ADMITTED', host_id: 'h1', status: 'BUSY', schedulable: true },
      ],
    });

    await goToDeviceStep();

    expect(await screen.findByLabelText(/ONLINE-BLOCKED/)).toBeDisabled();
    expect(screen.getByLabelText(/BUSY-ADMITTED/)).not.toBeDisabled();
    expect(screen.getByText(/已选 0 \/ 1 台可用/)).toBeInTheDocument();
  });

  it('renders Plan query failure instead of an empty selector', async () => {
    renderPage({ plansFailure: new Error('plans unavailable') });

    expect(await screen.findByText('加载 Plan 失败')).toBeInTheDocument();
    expect(screen.getByText('plans unavailable')).toBeInTheDocument();
  });

  it('rejects zero-step plans before preview', async () => {
    renderPage({
      plans: [{
        id: 7,
        name: 'Empty Plan',
        description: null,
        steps: [],
        failure_threshold: 0.05,
      }],
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });

    expect(screen.queryByRole('button', { name: /预览并发起/ })).not.toBeInTheDocument();
    expect(await screen.findByText(/没有已启用步骤/)).toBeInTheDocument();
    expect(api.plans.previewRun).not.toHaveBeenCalled();
  });

  it('freezes preview device IDs for the confirmed run', async () => {
    renderPage({
      devices: [
        { id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'DEV-2', host_id: 'h1', status: 'ONLINE' },
      ],
    });

    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    fireEvent.click(screen.getByRole('button', { name: /前置项、参数/ }));
    fireEvent.click(screen.getByRole('button', { name: /预览并发起/ }));
    await screen.findByText('确认执行');

    fireEvent.click(screen.getByRole('button', { name: /确认发起/ }));

    await waitFor(() => {
      expect(api.plans.run).toHaveBeenCalledWith(7, { device_ids: [1] });
    });
  });

  it('offers detail navigation and retry for a 503 with plan_run_id', async () => {
    const error = new ApiError('DISPATCH_QUEUE_UNAVAILABLE', 'SAQ unavailable', {
      status: 503,
      details: {
        code: 'DISPATCH_QUEUE_UNAVAILABLE',
        message: 'SAQ unavailable',
        retryable: true,
        plan_run_id: 91,
      },
    });
    (api.plans.run as any).mockRejectedValueOnce(error);
    (api.planRuns.retryDispatch as any).mockResolvedValueOnce({
      plan_run_id: 91,
      status: 'RUNNING',
    });
    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });

    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    fireEvent.click(screen.getByRole('button', { name: /前置项、参数/ }));
    fireEvent.click(screen.getByRole('button', { name: /预览并发起/ }));
    fireEvent.click(await screen.findByRole('button', { name: /确认发起/ }));

    expect(await screen.findByText('PlanRun #91 派发失败')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /重试派发/ }));

    await waitFor(() => expect(api.planRuns.retryDispatch).toHaveBeenCalledWith(91));
    expect(mocks.navigate).toHaveBeenCalledWith('/execution/plan-runs/91');
  });

  it('prefills rerun devices from ?devices= and reports unavailable ones', async () => {
    renderPage({
      initialEntry: '/execution/plan-execute?plan=7&devices=1,2,999',
      devices: [
        { id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'DEV-2', host_id: 'h1', status: 'BUSY' },
      ],
    });

    // 恢复 1 台可调度样机后自动跳到「数量与版本确认」
    expect(await screen.findByText('节点数量与版本一致性确认')).toBeInTheDocument();
    expect(screen.getByText(/已选 1 台/)).toBeInTheDocument();
    expect(mocks.toast.info).toHaveBeenCalledWith(
      expect.stringContaining('DEV-2'),
    );
    expect(mocks.toast.info).toHaveBeenCalledWith(
      expect.stringContaining('#999'),
    );
  });

  it('stays on selection flow when no rerun device is available', async () => {
    renderPage({
      initialEntry: '/execution/plan-execute?plan=7&devices=2',
      devices: [{ id: 2, serial: 'DEV-2', host_id: 'h1', status: 'BUSY' }],
    });

    expect(await screen.findByText(/Plan 配置/)).toBeInTheDocument();
    await waitFor(() => {
      expect(mocks.toast.info).toHaveBeenCalledWith(expect.stringContaining('DEV-2'));
    });
    expect(screen.getByText(/已选 0 台/)).toBeInTheDocument();
  });

  it('surfaces host capacity and per-device occupancy for the selected node', async () => {
    renderPage({
      hosts: [{
        id: 'h1',
        name: null,
        ip: null,
        status: 'ONLINE',
        capacity: { active_jobs: 3, active_devices: 3, online_healthy_devices: 5 },
        health: { status: 'DEGRADED', reasons: ['disk 90%'] },
      }],
      devices: [
        { id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'DEV-2', host_id: 'h1', status: 'BUSY' },
      ],
      hostDetail: {
        id: 'h1',
        status: 'ONLINE',
        active_jobs: [{ id: 9, device_id: 2, plan_run_id: 55, status: 'RUNNING' }],
      },
    });

    await goToDeviceStep();
    selectFirstNode();

    expect(await screen.findByText(/忙 3/)).toBeInTheDocument();
    const occupancyLink = await screen.findByText('执行中 · PlanRun #55');
    expect(occupancyLink).toHaveAttribute('href', '/execution/plan-runs/55');
  });

  it('shows all-nodes device table and selects across hosts', async () => {
    renderPage({
      devices: [
        { id: 1, serial: 'A-1', host_id: 'h1', status: 'ONLINE', model: 'M1', build_display_id: 'V1' },
        { id: 2, serial: 'B-1', host_id: 'h2', status: 'ONLINE', model: 'M2', build_display_id: 'V2' },
        { id: 3, serial: 'B-BUSY', host_id: 'h2', status: 'BUSY', model: 'M2', build_display_id: 'V2' },
      ],
    });

    await goToDeviceStep();

    expect(await screen.findByRole('button', { name: /全部节点/ })).toBeInTheDocument();
    expect(screen.getByLabelText(/A-1/)).toBeInTheDocument();
    expect(screen.getByLabelText(/B-1/)).toBeInTheDocument();
    expect(screen.queryByText('请先从左侧选择一个节点')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /全选筛选结果 \(2\)/ }));
    expect(screen.getByText(/已选 2 \/ 2 台可用/)).toBeInTheDocument();
    expect(screen.getByLabelText(/A-1/)).toBeChecked();
    expect(screen.getByLabelText(/B-1/)).toBeChecked();
  });

  it('formats duration and shows unset failure threshold without masking as 5%', async () => {
    renderPage({
      plans: [{
        id: 7,
        name: 'Smoke Plan',
        description: null,
        steps: [{ step_key: 'check_device' }],
        failure_threshold: null,
        patrol_interval_seconds: 3600,
        timeout_seconds: 125,
      }],
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });

    expect(await screen.findByText('未设置（按默认 5% 生效）')).toBeInTheDocument();
    expect(screen.getByText(/巡检周期：1h 0m/)).toBeInTheDocument();
    expect(screen.getByText(/超时：2m 5s/)).toBeInTheDocument();

    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    fireEvent.click(screen.getByRole('button', { name: /前置项、参数/ }));

    expect(await screen.findByRole('button', { name: '编辑 Plan' })).toBeInTheDocument();
    expect(screen.getAllByText('未设置（按默认 5% 生效）').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('1h 0m')).toBeInTheDocument();
    expect(screen.getByText('2m 5s')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '编辑 Plan' }));
    expect(mocks.navigate).toHaveBeenCalledWith('/orchestration/plans/7');
  });

  it('falls back to device step when selection is cleared on later steps', async () => {
    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });

    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    fireEvent.click(screen.getByRole('button', { name: /数量与版本/ }));
    expect(await screen.findByText('节点数量与版本一致性确认')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '清空选择' }));

    await waitFor(() => {
      expect(screen.getByText('设备编排')).toBeInTheDocument();
    });
    expect(mocks.toast.info).toHaveBeenCalledWith('已无选中样机，已返回样机选择');
  });

  it('merges device and job counts in preview dialog', async () => {
    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });

    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    fireEvent.click(screen.getByRole('button', { name: /前置项、参数/ }));
    fireEvent.click(screen.getByRole('button', { name: /预览并发起/ }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('设备数（= Job 数）')).toBeInTheDocument();
    expect(within(dialog).queryByText('Job 数')).not.toBeInTheDocument();
  });

  it('matches serial search case-insensitively', async () => {
    renderPage({
      devices: [
        { id: 1, serial: 'AbC-123', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'OTHER', host_id: 'h1', status: 'ONLINE' },
      ],
    });

    await goToDeviceStep();
    const search = await screen.findByPlaceholderText('搜索 Serial / 型号');
    fireEvent.change(search, { target: { value: 'abc' } });

    expect(await screen.findByLabelText(/AbC-123/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/OTHER/)).not.toBeInTheDocument();
  });
});
