import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
    action: vi.fn(),
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
        list: vi.fn().mockResolvedValue([]),
        get: vi.fn(),
        retryDispatch: vi.fn(),
      },
      scripts: {
        list: vi.fn().mockResolvedValue([]),
      },
      devices: {
        list: vi.fn(),
      },
      hosts: {
        list: vi.fn(),
        get: vi.fn(),
      },
      jobs: {
        activeByDevice: vi.fn().mockResolvedValue([]),
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
  activeJobs = undefined as any[] | undefined,
  getHost,
}: {
  plans?: any[];
  devices?: any[];
  plansFailure?: Error;
  devicesFailure?: Error;
  initialEntry?: string;
  hosts?: any[];
  hostDetail?: any;
  activeJobs?: any[];
  getHost?: (id: string) => any | Promise<any>;
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
  if (getHost) {
    (api.hosts.get as any).mockImplementation(async (id: string) => getHost(id));
  } else {
    (api.hosts.get as any).mockResolvedValue(hostDetail);
  }
  const derivedActiveJobs =
    activeJobs ??
    (Array.isArray(hostDetail?.active_jobs) ? hostDetail.active_jobs : []);
  (api.jobs.activeByDevice as any).mockResolvedValue(derivedActiveJobs);
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
  // 等 plans query 落定后再进选机；默认切到表格以覆盖占用列/预检文案等既有断言。
  await screen.findByText(/启用步骤/);
  fireEvent.click(screen.getByRole('button', { name: /进入选机/ }));
  fireEvent.click(await screen.findByRole('button', { name: '表格' }));
}

function selectFirstNode() {
  const node = screen.getAllByRole('button').find(button => /auto-|h1/.test(button.textContent ?? ''));
  if (node) fireEvent.click(node);
}

describe('PlanExecutePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    localStorage.clear();
    (api.planRuns.list as any).mockResolvedValue([]);
    (api.planRuns.get as any).mockReset();
  });

  it('keeps the page title and view-specific subtitle visible in selection phase', async () => {
    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });

    await screen.findByText(/启用步骤/);
    fireEvent.click(screen.getByRole('button', { name: /进入选机/ }));

    expect(screen.getByRole('heading', { name: '执行测试计划' })).toBeInTheDocument();
    expect(screen.getByText(/中区展示筛选结果候选池/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '表格' }));
    expect(screen.getByText(/表格用于明细核对/)).toBeInTheDocument();
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

    expect(screen.queryByRole('button', { name: /生成执行预览/ })).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByRole('button', { name: /预览发起/ }));
    fireEvent.click(screen.getByRole('button', { name: /生成执行预览/ }));
    await screen.findByText(/预览已生成并冻结 1 台设备/);

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
    fireEvent.click(screen.getByRole('button', { name: /预览发起/ }));
    fireEvent.click(screen.getByRole('button', { name: /生成执行预览/ }));
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

    // 恢复 1 台可调度样机后自动跳到选机工作台
    expect(await screen.findByText('已选样机 Minimap')).toBeInTheDocument();
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

    expect(await screen.findByText('选择测试计划')).toBeInTheDocument();
    await waitFor(() => {
      expect(mocks.toast.info).toHaveBeenCalledWith(expect.stringContaining('DEV-2'));
    });
    // 第 0 步不显示设备计数；进入样机选择后计数为 0
    expect(screen.queryByText(/已选 0 台/)).not.toBeInTheDocument();
    await goToDeviceStep();
    expect(await screen.findByText(/已选 0 台/)).toBeInTheDocument();
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

  it('shows occupancy links in all-nodes view via active-by-device API', async () => {
    renderPage({
      hosts: [
        { id: 'h1', ip: '172.21.8.143', status: 'ONLINE' },
        { id: 'h2', ip: '172.21.8.192', status: 'ONLINE' },
      ],
      devices: [
        { id: 1, serial: 'DEV-FREE', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'DEV-BUSY', host_id: 'h2', status: 'BUSY' },
      ],
      activeJobs: [{ id: 11, device_id: 2, plan_run_id: 77, status: 'RUNNING' }],
    });

    await goToDeviceStep();
    // 全部节点视图：不钻取节点也应看到占用跳转
    expect(await screen.findByText('执行中 · PlanRun #77')).toBeInTheDocument();
    expect(screen.getByLabelText(/DEV-FREE/)).toBeInTheDocument();
  });

  it('warns when selected devices exceed host effective_slots', async () => {
    renderPage({
      hosts: [{
        id: 'h1',
        ip: '172.21.8.143',
        status: 'ONLINE',
        capacity: {
          active_jobs: 1,
          active_devices: 1,
          online_healthy_devices: 3,
          effective_slots: 1,
          available_slots: 2,
        },
      }],
      devices: [
        { id: 1, serial: 'DEV-A', host_id: 'h1', status: 'ONLINE', model: 'M1', build_display_id: 'V1' },
        { id: 2, serial: 'DEV-B', host_id: 'h1', status: 'ONLINE', model: 'M1', build_display_id: 'V1' },
      ],
    });

    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-A/));
    fireEvent.click(await screen.findByLabelText(/DEV-B/));
    expect(await screen.findByText(/1 个节点超选/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /预览发起/ }));
    expect(await screen.findByText(/1 立即 · 1 将排队/)).toBeInTheDocument();
    expect(screen.getByText(/不是 PlanRun 级 QUEUED 准入/)).toBeInTheDocument();
  });

  it('does not warn when effective_slots is missing', async () => {
    renderPage({
      hosts: [{
        id: 'h1',
        ip: '172.21.8.143',
        status: 'ONLINE',
        capacity: { active_jobs: 0, active_devices: 0, online_healthy_devices: 2 },
      }],
      devices: [
        { id: 1, serial: 'DEV-A', host_id: 'h1', status: 'ONLINE', model: 'M1', build_display_id: 'V1' },
        { id: 2, serial: 'DEV-B', host_id: 'h1', status: 'ONLINE', model: 'M1', build_display_id: 'V1' },
      ],
    });

    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-A/));
    fireEvent.click(await screen.findByLabelText(/DEV-B/));
    expect(screen.queryByText(/节点超选/)).not.toBeInTheDocument();
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

    fireEvent.click(screen.getByRole('button', { name: /全选筛选 \(2\)/ }));
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
    fireEvent.click(screen.getByRole('button', { name: /预览发起/ }));

    expect(await screen.findByRole('button', { name: '编辑 Plan' })).toBeInTheDocument();
    expect(screen.getAllByText('未设置（按默认 5% 生效）').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('1h 0m')).toBeInTheDocument();
    expect(screen.getByText('2m 5s')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '巡检周期说明' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '超时说明' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '失败阈值说明' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '编辑 Plan' }));
    expect(mocks.navigate).toHaveBeenCalledWith('/orchestration/plans/7');
  });

  it('falls back to device step when selection is cleared on later steps', async () => {
    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });

    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    fireEvent.click(screen.getByRole('button', { name: /预览发起/ }));
    expect(await screen.findByTestId('dispatch-cockpit')).toBeInTheDocument();

    // 清空选择需二次确认
    fireEvent.click(screen.getByRole('button', { name: '清空选择' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '确认清空' }));

    await waitFor(() => {
      expect(screen.getByText('已选样机 Minimap')).toBeInTheDocument();
    });
    expect(mocks.toast.info).toHaveBeenCalledWith('已无选中样机，已返回样机选择');
  });

  it('offers undo when removing a device via minimap hover remove control', async () => {
    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });

    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    expect(screen.getByText(/已选 1 台/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '移除已选设备 1' }));
    expect(screen.getByText(/已选 0 台/)).toBeInTheDocument();
    expect(mocks.toast.action).toHaveBeenCalledWith(
      '已移除 1 台样机',
      expect.objectContaining({ label: '撤销' }),
    );

    // 撤销后恢复选中
    const [, options] = mocks.toast.action.mock.calls[0];
    options.onClick();
    await waitFor(() => {
      expect(screen.getByText(/已选 1 台/)).toBeInTheDocument();
    });
    expect(screen.getByLabelText(/DEV-1/)).toBeChecked();
  });

  it('locates a selected device from minimap without removing it', async () => {
    renderPage({
      devices: [
        { id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE', build_display_id: 'V104' },
        { id: 2, serial: 'DEV-2', host_id: 'h1', status: 'ONLINE', build_display_id: 'V103' },
      ],
    });

    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    fireEvent.click(screen.getByRole('button', { name: 'V103' }));
    expect(screen.getByTestId('active-filter-chips')).toHaveTextContent('版本:V103');

    fireEvent.click(screen.getByRole('button', { name: /定位已选设备 1/ }));
    await waitFor(() => {
      expect(mocks.toast.info).toHaveBeenCalledWith('已清除筛选以定位该样机');
    });
    expect(screen.getByText(/已选 1 台/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /定位已选设备 1/ })).toBeInTheDocument();
  });

  it('marks blocked minimap tiles with pattern legend and locate aria-label', async () => {
    renderPage({
      hosts: [{ id: 'h1', ip: '172.21.8.143', status: 'OFFLINE' }],
      devices: [{
        id: 1,
        serial: 'DEV-BLOCK',
        host_id: 'h1',
        status: 'ONLINE',
        adb_connected: false,
        adb_state: 'offline',
      }],
    });

    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-BLOCK/));
    expect(screen.getByText(/已选阻塞（斜纹/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /定位已选设备 1 阻塞/ })).toBeInTheDocument();
  });

  it('serializes active filters into URL and clears via chips', async () => {
    renderPage({
      devices: [
        { id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE', build_display_id: 'V104', model: 'ELA', tags: ['回归'] },
        { id: 2, serial: 'DEV-2', host_id: 'h1', status: 'ONLINE', build_display_id: 'V103', model: 'X200' },
      ],
      hosts: [{ id: 'h1', ip: '10.0.0.1', status: 'ONLINE' }],
      initialEntry: '/execution/plan-execute?plan=7&version=V104&ready=1&view=matrix',
    });

    await goToDeviceStep();
    const chips = await screen.findByTestId('active-filter-chips');
    expect(chips).toHaveTextContent('版本:V104');
    expect(chips).toHaveTextContent('仅就绪');

    fireEvent.click(screen.getByRole('button', { name: '清除筛选 版本:V104' }));
    await waitFor(() => {
      expect(screen.getByTestId('active-filter-chips')).not.toHaveTextContent('版本:V104');
    });
    expect(screen.getByTestId('active-filter-chips')).toHaveTextContent('仅就绪');
  });

  it('copies selected serials to clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });

    renderPage({
      devices: [
        { id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'DEV-2', host_id: 'h1', status: 'ONLINE' },
      ],
    });

    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    fireEvent.click(await screen.findByLabelText(/DEV-2/));
    fireEvent.click(screen.getByRole('button', { name: /复制 serials/ }));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith('DEV-1\nDEV-2');
    });
    expect(mocks.toast.success).toHaveBeenCalledWith('已复制 2 个 serial');
  });

  it('keeps preview confirmation inline without reopening a dialog', async () => {
    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });

    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    fireEvent.click(screen.getByRole('button', { name: /预览发起/ }));
    fireEvent.click(screen.getByRole('button', { name: /生成执行预览/ }));

    expect(await screen.findByText(/预览已生成并冻结 1 台设备/)).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /确认发起/ })).toBeEnabled();
  });

  it('hides device counters on the plan step and shows them from device step', async () => {
    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });

    await screen.findByText(/启用步骤/);
    expect(screen.queryByText(/已选 0 台/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '清空选择' })).not.toBeInTheDocument();

    await goToDeviceStep();
    expect(await screen.findByText(/已选 0 台/)).toBeInTheDocument();
  });

  it('sorts node sidebar by numeric IP with unassigned last', async () => {
    renderPage({
      hosts: [
        { id: 'h2', ip: '172.21.9.124', name: null, status: 'ONLINE' },
        { id: 'h1', ip: '172.21.8.103', name: null, status: 'ONLINE' },
      ],
      devices: [
        { id: 1, serial: 'A-1', host_id: 'h2', status: 'ONLINE' },
        { id: 2, serial: 'B-1', host_id: 'h1', status: 'ONLINE' },
        { id: 3, serial: 'C-1', host_id: null, status: 'ONLINE' },
      ],
    });

    await goToDeviceStep();
    await screen.findByLabelText(/A-1/);

    const nodeLabels = screen.getAllByRole('button')
      .map(button => button.querySelector('.font-mono')?.textContent ?? '')
      .filter(Boolean);
    expect(nodeLabels).toEqual(['172.21.8.103', '172.21.9.124', '未分配节点']);
  });

  it('restores draft selection and step after a remount', async () => {
    const first = renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });
    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    expect(await screen.findByText(/已选 1 台/)).toBeInTheDocument();

    // 等防抖写入落盘（停在选机态）
    await waitFor(() => {
      const raw = sessionStorage.getItem('stp.planExecute.draft.v2');
      expect(raw).toBeTruthy();
      const draft = JSON.parse(raw as string);
      expect(draft.deviceIds).toEqual([1]);
      expect(draft.phase).toBe('select');
    });
    first.unmount();

    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });
    // 设备集与步骤均恢复：直接进入选机工作台且计数为 1
    expect(await screen.findByText('已选样机 Minimap')).toBeInTheDocument();
    expect(screen.getByText(/已选 1 台/)).toBeInTheDocument();
  });

  it('prefers URL devices over the draft and overwrites it', async () => {
    sessionStorage.setItem('stp.planExecute.draft.v2', JSON.stringify({
      planId: 7,
      deviceIds: [2],
      phase: 'select',
      view: 'table',
      deviceFilter: '',
      deviceVersionFilter: 'all',
      deviceHostFilter: 'all',
      deviceModelFilter: 'all',
    }));
    renderPage({
      initialEntry: '/execution/plan-execute?plan=7&devices=1',
      devices: [
        { id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'DEV-2', host_id: 'h1', status: 'ONLINE' },
      ],
    });

    // URL 预填生效：仅 DEV-1 被恢复并跳到选机工作台
    expect(await screen.findByText('已选样机 Minimap')).toBeInTheDocument();
    expect(screen.getByText(/已选 1 台/)).toBeInTheDocument();

    // 切到表格核对：DEV-1 选中、DEV-2 未选（草稿 [2] 被忽略）
    fireEvent.click(screen.getByRole('button', { name: '表格' }));
    expect(await screen.findByLabelText(/DEV-1/)).toBeChecked();
    expect(screen.getByLabelText(/DEV-2/)).not.toBeChecked();
  });

  it('keeps URL plan authoritative while restoring draft devices', async () => {
    sessionStorage.setItem('stp.planExecute.draft.v2', JSON.stringify({
      planId: 99,
      deviceIds: [1],
      phase: 'select',
      view: 'table',
      deviceFilter: '',
      deviceVersionFilter: 'all',
      deviceHostFilter: 'all',
      deviceModelFilter: 'all',
    }));
    renderPage({
      initialEntry: '/execution/plan-execute?plan=7',
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });

    // 恢复后直接落在样机选择且 DEV-1 选中（跳转守卫要求 plan=7 有效）
    expect(await screen.findByLabelText(/DEV-1/)).toBeChecked();
    // 回到计划配置确认 URL plan=7 生效（草稿 planId=99 被忽略）
    fireEvent.click(screen.getByRole('button', { name: /Plan ·/ }));
    expect(await screen.findByText(/启用步骤/)).toBeInTheDocument();
  });

  it('clears the draft after a successful launch and on cancel', async () => {
    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });
    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    fireEvent.click(screen.getByRole('button', { name: /预览发起/ }));
    fireEvent.click(screen.getByRole('button', { name: /生成执行预览/ }));
    await screen.findByText(/预览已生成并冻结 1 台设备/);
    fireEvent.click(screen.getByRole('button', { name: /确认发起/ }));

    await waitFor(() => expect(api.plans.run).toHaveBeenCalled());
    expect(sessionStorage.getItem('stp.planExecute.draft.v2')).toBeNull();
  });

  it('keeps the draft when dispatch fails with 503', async () => {
    const error = new ApiError('DISPATCH_QUEUE_UNAVAILABLE', 'SAQ unavailable', {
      status: 503,
      details: { code: 'DISPATCH_QUEUE_UNAVAILABLE', message: 'SAQ unavailable', retryable: true, plan_run_id: 91 },
    });
    (api.plans.run as any).mockRejectedValueOnce(error);
    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });
    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    // 等防抖写入落盘后再发起
    await waitFor(() => {
      const raw = sessionStorage.getItem('stp.planExecute.draft.v2');
      expect(raw).toBeTruthy();
      expect(JSON.parse(raw as string).deviceIds).toEqual([1]);
    });
    fireEvent.click(screen.getByRole('button', { name: /预览发起/ }));
    fireEvent.click(screen.getByRole('button', { name: /生成执行预览/ }));
    fireEvent.click(await screen.findByRole('button', { name: /确认发起/ }));

    expect(await screen.findByText('PlanRun #91 派发失败')).toBeInTheDocument();
    const raw = sessionStorage.getItem('stp.planExecute.draft.v2');
    expect(raw).toBeTruthy();
    expect(JSON.parse(raw as string).deviceIds).toEqual([1]);
  });

  it('shows the host node column in the device table', async () => {
    renderPage({
      hosts: [{ id: 'h1', ip: '172.21.8.103', name: null, status: 'ONLINE' }],
      devices: [
        { id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'DEV-2', host_id: null, status: 'ONLINE' },
      ],
    });

    await goToDeviceStep();
    expect(await screen.findByRole('columnheader', { name: '节点' })).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: '172.21.8.103' })).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: '未分配节点' })).toBeInTheDocument();
  });

  it('shows readiness reasons before any selection', async () => {
    renderPage({
      hosts: [{ id: 'h1', ip: null, name: null, status: 'ONLINE' }],
      devices: [
        { id: 1, serial: 'DEV-OK', host_id: 'h1', status: 'ONLINE', adb_connected: true, adb_state: 'device' },
        { id: 2, serial: 'DEV-ADB', host_id: 'h1', status: 'ONLINE', adb_connected: false, adb_state: 'offline' },
      ],
    });

    await goToDeviceStep();
    // 未勾选即见阻塞原因与就绪状态，「选择后检查」不再出现
    expect(await screen.findByText('ADB offline')).toBeInTheDocument();
    expect(screen.getByText('就绪')).toBeInTheDocument();
    expect(screen.queryByText('选择后检查')).not.toBeInTheDocument();
  });

  it('filters devices by tags for pool-based selection', async () => {
    renderPage({
      devices: [
        { id: 1, serial: 'DEV-SMOKE', host_id: 'h1', status: 'ONLINE', tags: ['smoke'] },
        { id: 2, serial: 'DEV-REG', host_id: 'h1', status: 'ONLINE', tags: ['regression'] },
        { id: 3, serial: 'DEV-NONE', host_id: 'h1', status: 'ONLINE', tags: [] },
      ],
    });

    await goToDeviceStep();
    expect(await screen.findByLabelText(/DEV-SMOKE/)).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /全部标签/ }));
    await user.click(await screen.findByRole('menuitem', { name: 'regression' }));
    // 多选菜单保持展开（preventDefault），关闭后再断言（modal 模式会使主区域 inert）
    await user.keyboard('{Escape}');

    expect(screen.queryByLabelText(/DEV-SMOKE/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/DEV-NONE/)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/DEV-REG/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /全选筛选 \(1\)/ })).toBeInTheDocument();
  });

  it('matches serial search case-insensitively', async () => {
    renderPage({
      devices: [
        { id: 1, serial: 'AbC-123', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'OTHER', host_id: 'h1', status: 'ONLINE' },
      ],
    });

    await goToDeviceStep();
    const search = await screen.findByPlaceholderText('搜索 serial…');
    fireEvent.change(search, { target: { value: 'abc' } });

    expect(await screen.findByLabelText(/AbC-123/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/OTHER/)).not.toBeInTheDocument();
  });

  it('passes optional run note into launch payload and preview', async () => {
    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });

    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    fireEvent.click(screen.getByRole('button', { name: /预览发起/ }));

    const note = await screen.findByLabelText(/执行备注/);
    fireEvent.change(note, { target: { value: '  sprint4 smoke  ' } });

    fireEvent.click(screen.getByRole('button', { name: /生成执行预览/ }));
    expect(await screen.findByText(/预览已生成并冻结 1 台设备/)).toBeInTheDocument();
    expect(note).toHaveValue('  sprint4 smoke  ');

    fireEvent.click(screen.getByRole('button', { name: /确认发起/ }));
    await waitFor(() => {
      expect(api.plans.run).toHaveBeenCalledWith(7, {
        device_ids: [1],
        note: 'sprint4 smoke',
      });
    });
  });

  it('disables preview button while preview request is in flight', async () => {
    let resolvePreview!: (value: unknown) => void;
    (api.plans.previewRun as any).mockReturnValueOnce(
      new Promise((resolve) => {
        resolvePreview = resolve;
      }),
    );
    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });

    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    fireEvent.click(screen.getByRole('button', { name: /预览发起/ }));
    fireEvent.click(screen.getByRole('button', { name: /生成执行预览/ }));

    expect(await screen.findByRole('button', { name: /预览中/ })).toBeDisabled();
    resolvePreview({
      plan_name: 'Smoke Plan',
      device_count: 1,
      job_count: 1,
      total_steps: 1,
      device_ids: [1],
    });
    expect(await screen.findByText(/预览已生成并冻结 1 台设备/)).toBeInTheDocument();
  });

  it('expands step rows to show script default_params', async () => {
    (api.scripts.list as any).mockResolvedValueOnce([
      {
        name: 'check_device',
        version: '1.0.0',
        default_params: { timeout: 30, retries: 1 },
      },
    ]);
    renderPage({
      plans: [
        {
          id: 7,
          name: 'Smoke Plan',
          description: null,
          steps: [
            {
              id: 1,
              step_key: 'check_device',
              script_name: 'check_device',
              script_version: '1.0.0',
              stage: 'init',
              enabled: true,
            },
          ],
          failure_threshold: 0.05,
        },
      ],
      initialEntry: '/execution/plan-execute?plan=7',
    });

    expect(await screen.findByText(/check_device · 1\.0\.0/)).toBeInTheDocument();
    fireEvent.click(screen.getByText(/check_device · 1\.0\.0/));
    expect(await screen.findByText(/"timeout": 30/)).toBeInTheDocument();
    expect(screen.getByText(/"retries": 1/)).toBeInTheDocument();
  });

  it('shows recent plan runs after selecting a plan', async () => {
    const startedAt = new Date().toISOString();
    (api.planRuns.list as any).mockResolvedValue([
      {
        id: 8841,
        plan_id: 7,
        status: 'RUNNING',
        failure_threshold: 0.05,
        run_type: 'MANUAL',
        started_at: startedAt,
        run_context: { dispatch_device_ids: [1, 2, 3] },
      },
      {
        id: 8810,
        plan_id: 7,
        status: 'SUCCESS',
        failure_threshold: 0.05,
        run_type: 'MANUAL',
        started_at: startedAt,
        result_summary: { total: 30 },
      },
    ]);
    renderPage({ initialEntry: '/execution/plan-execute?plan=7' });

    expect(await screen.findByTestId('recent-plan-runs-inline')).toBeInTheDocument();
    expect(screen.getByTestId('recent-plan-run-8841')).toHaveTextContent('3 台');
    expect(screen.getByTestId('recent-plan-run-8810')).toHaveTextContent('30 台');

    fireEvent.click(screen.getByTestId('recent-plan-run-8841'));
    expect(mocks.navigate).toHaveBeenCalledWith('/execution/plan-runs/8841');
  });

  it('keeps duplicate warning visible after inline preview without blocking confirm', async () => {
    const startedAt = new Date().toISOString();
    (api.planRuns.list as any).mockResolvedValue([
      {
        id: 9001,
        plan_id: 7,
        status: 'RUNNING',
        failure_threshold: 0.05,
        run_type: 'MANUAL',
        started_at: startedAt,
        run_context: { dispatch_device_ids: [1, 2, 3, 9] },
      },
    ]);
    renderPage({
      devices: [
        { id: 1, serial: 'D1', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'D2', host_id: 'h1', status: 'ONLINE' },
        { id: 3, serial: 'D3', host_id: 'h1', status: 'ONLINE' },
        { id: 4, serial: 'D4', host_id: 'h1', status: 'ONLINE' },
      ],
    });

    await goToDeviceStep();
    for (const serial of ['D1', 'D2', 'D3', 'D4']) {
      fireEvent.click(await screen.findByLabelText(new RegExp(serial)));
    }
    fireEvent.click(screen.getByRole('button', { name: /预览发起/ }));

    expect(await screen.findByTestId('duplicate-launch-banner')).toHaveTextContent('疑似重复发起');
    expect(screen.getByTestId('duplicate-launch-banner')).toHaveTextContent('#9001');

    fireEvent.click(screen.getByRole('button', { name: /生成执行预览/ }));
    expect(await screen.findByText(/预览已生成并冻结 4 台设备/)).toBeInTheDocument();
    const banners = screen.getAllByTestId('duplicate-launch-banner');
    expect(banners).toHaveLength(1);
    expect(screen.getByRole('button', { name: /确认发起/ })).toBeEnabled();
  });

  it('fetches plan run detail when list lacks dispatch_device_ids and shows weak tip otherwise', async () => {
    const startedAt = new Date().toISOString();
    (api.planRuns.list as any).mockResolvedValue([
      {
        id: 9100,
        plan_id: 7,
        status: 'RUNNING',
        failure_threshold: 0.05,
        run_type: 'MANUAL',
        started_at: startedAt,
        result_summary: { total: 4 },
      },
    ]);
    (api.planRuns.get as any).mockResolvedValue({
      id: 9100,
      plan_id: 7,
      status: 'RUNNING',
      failure_threshold: 0.05,
      run_type: 'MANUAL',
      started_at: startedAt,
      result_summary: { total: 4 },
    });
    renderPage({
      devices: [
        { id: 1, serial: 'D1', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'D2', host_id: 'h1', status: 'ONLINE' },
        { id: 3, serial: 'D3', host_id: 'h1', status: 'ONLINE' },
        { id: 4, serial: 'D4', host_id: 'h1', status: 'ONLINE' },
      ],
    });

    await goToDeviceStep();
    for (const serial of ['D1', 'D2', 'D3', 'D4']) {
      fireEvent.click(await screen.findByLabelText(new RegExp(serial)));
    }
    fireEvent.click(screen.getByRole('button', { name: /预览发起/ }));

    expect(await screen.findByTestId('duplicate-launch-banner')).toHaveTextContent('设备数接近');
    await waitFor(() => expect(api.planRuns.get).toHaveBeenCalledWith(9100));
  });

  it('groups plan steps by stage with colored badges', async () => {
    renderPage({
      plans: [{
        id: 7,
        name: 'Smoke Plan',
        description: null,
        steps: [
          { id: 1, step_key: 'a', script_name: 'init_a', script_version: '1.0.0', stage: 'init', enabled: true, sort_order: 1 },
          { id: 2, step_key: 'b', script_name: 'patrol_b', script_version: '1.0.0', stage: 'patrol', enabled: true, sort_order: 1 },
          { id: 3, step_key: 'c', script_name: 'tear_c', script_version: '1.0.0', stage: 'teardown', enabled: true, sort_order: 1 },
        ],
        failure_threshold: 0.05,
      }],
      initialEntry: '/execution/plan-execute?plan=7',
    });

    const list = await screen.findByTestId('plan-step-list');
    expect(within(list).getByText('init')).toBeInTheDocument();
    expect(within(list).getByText('patrol')).toBeInTheDocument();
    expect(within(list).getByText('teardown')).toBeInTheDocument();
    expect(within(list).getByText(/init_a · 1\.0\.0/)).toBeInTheDocument();
  });

  it('saves a named selection preset from current selection', async () => {
    renderPage({
      devices: [
        { id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'DEV-2', host_id: 'h1', status: 'ONLINE' },
      ],
    });

    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    fireEvent.click(screen.getByLabelText(/DEV-2/));

    fireEvent.click(screen.getByRole('button', { name: /存为方案/ }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByPlaceholderText(/周五回归/), {
      target: { value: '周五回归 · ELA' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }));

    expect(mocks.toast.success).toHaveBeenCalledWith(expect.stringContaining('已保存方案「周五回归 · ELA」'));
    expect(screen.getByText('周五回归 · ELA')).toBeInTheDocument();
  });

  it('applies preset intersection and skips unschedulable devices', async () => {
    const { PRESETS_STORAGE_KEY } = await import('@/components/execution/plan-execute/planExecutePresets');
    localStorage.setItem(PRESETS_STORAGE_KEY, JSON.stringify([{
      id: 'p1',
      name: 'Smoke 8',
      deviceIds: [1, 2, 99],
      createdAt: '2026-07-21T00:00:00Z',
    }]));

    renderPage({
      devices: [
        { id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'DEV-2', host_id: 'h1', status: 'ONLINE' },
      ],
    });

    await goToDeviceStep();
    expect(await screen.findByText('Smoke 8')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '应用' }));

    expect(mocks.toast.info).toHaveBeenCalledWith(expect.stringContaining('1 台已失效并跳过'));
    expect(screen.getByLabelText(/DEV-1/)).toBeChecked();
    expect(screen.getByLabelText(/DEV-2/)).toBeChecked();
  });

  it('renders select workspace as three-column shell (node | stage | selected)', async () => {
    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });

    await goToDeviceStep();

    const layout = document.querySelector('[data-plan-execute-layout="three-column"]');
    expect(layout).toBeTruthy();
    expect(screen.getByTestId('plan-execute-node-rail')).toBeInTheDocument();
    expect(screen.getByTestId('plan-execute-stage')).toBeInTheDocument();
    expect(screen.getByTestId('selected-devices-rail')).toBeInTheDocument();
    expect(screen.getByText('已选集')).toBeInTheDocument();
    expect(screen.getByText('已选样机 Minimap')).toBeInTheDocument();
  });

  it('uses full-bleed workspace shell in select phase (no side gutter clamp)', async () => {
    const { container } = renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });
    await goToDeviceStep();
    const page = container.firstElementChild as HTMLElement | null;
    expect(page?.className ?? '').toMatch(/\bw-full\b/);
    expect(page?.className ?? '').not.toMatch(/max-w-7xl/);
    expect(page?.className ?? '').not.toMatch(/mx-auto/);
    expect(page?.className ?? '').toMatch(/overflow-hidden/);
    expect(screen.getByTestId('device-workspace')).toBeInTheDocument();
    expect(screen.getByTestId('execute-command-bar')).toHaveAttribute('data-compact', 'true');
  });

  it('selects all filtered devices with Ctrl/Meta+A in select phase', async () => {
    renderPage({
      devices: [
        { id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'DEV-2', host_id: 'h1', status: 'ONLINE' },
        { id: 3, serial: 'DEV-BUSY', host_id: 'h1', status: 'BUSY' },
      ],
    });

    await goToDeviceStep();
    const workspace = document.querySelector('[data-plan-execute-workspace]');
    expect(workspace).toBeTruthy();
    (document.activeElement as HTMLElement | null)?.blur?.();

    fireEvent.keyDown(window, { key: 'a', ctrlKey: true });
    expect(screen.getByLabelText(/DEV-1/)).toBeChecked();
    expect(screen.getByLabelText(/DEV-2/)).toBeChecked();
    expect(screen.getByLabelText(/DEV-BUSY/)).toBeDisabled();
  });

  it('advances with Enter from plan phase when primary is enabled', async () => {
    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });
    await screen.findByText(/启用步骤/);
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(await screen.findByLabelText('样机选择')).toBeInTheDocument();
  });

  it('sorts table rows by serial when header is clicked', async () => {
    renderPage({
      hosts: [
        { id: 'h1', ip: '10.0.0.1', name: 'rack-a', status: 'ONLINE' },
      ],
      devices: [
        { id: 2, serial: 'ZZZ-2', host_id: 'h1', status: 'ONLINE', model: 'M', build_display_id: 'V2' },
        { id: 1, serial: 'AAA-1', host_id: 'h1', status: 'ONLINE', model: 'M', build_display_id: 'V1' },
      ],
    });
    await goToDeviceStep();
    fireEvent.click(screen.getByRole('button', { name: '按Serial排序' }));
    const rows = screen.getAllByRole('row').slice(1);
    expect(within(rows[0]).getByText('AAA-1')).toBeInTheDocument();
    expect(within(rows[1]).getByText('ZZZ-2')).toBeInTheDocument();
  });

  it('groups recently executed plans ahead in the plan list', async () => {
    (api.planRuns.list as any).mockImplementation(async (_skip: number, _limit: number, planId?: number) => {
      if (planId == null) {
        return [
          { id: 501, plan_id: 9, started_at: '2026-07-20T12:00:00Z', status: 'COMPLETED' },
        ];
      }
      return [];
    });
    renderPage({
      plans: [
        {
          id: 7,
          name: 'Smoke Plan',
          description: null,
          steps: [{ step_key: 'check_device' }],
          failure_threshold: 0.05,
          updated_at: '2026-07-01T00:00:00Z',
        },
        {
          id: 9,
          name: 'Nightly Plan',
          description: null,
          steps: [{ step_key: 'patrol' }],
          failure_threshold: 0.05,
          updated_at: '2026-06-01T00:00:00Z',
        },
      ],
      initialEntry: '/execution/plan-execute',
    });
    const list = await screen.findByTestId('plan-execute-plan-layout');
    await waitFor(() => {
      expect(screen.getByTestId('plan-select-recent-group')).toBeInTheDocument();
      const names = Array.from(list.querySelectorAll('button'))
        .map((el) => el.textContent ?? '')
        .filter((t) => t.includes('Plan'));
      const nightlyIdx = names.findIndex((t) => t.includes('Nightly Plan'));
      const smokeIdx = names.findIndex((t) => t.includes('Smoke Plan'));
      expect(nightlyIdx).toBeGreaterThanOrEqual(0);
      expect(smokeIdx).toBeGreaterThanOrEqual(0);
      expect(nightlyIdx).toBeLessThan(smokeIdx);
    });
  });
});
