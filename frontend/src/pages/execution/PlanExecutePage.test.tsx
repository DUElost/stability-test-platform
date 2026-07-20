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
  getHost,
}: {
  plans?: any[];
  devices?: any[];
  plansFailure?: Error;
  devicesFailure?: Error;
  initialEntry?: string;
  hosts?: any[];
  hostDetail?: any;
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
    sessionStorage.clear();
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
    // 第 0 步不显示设备计数；进入样机选择后计数为 0
    expect(screen.queryByText('已选 0 台')).not.toBeInTheDocument();
    await goToDeviceStep();
    expect(await screen.findByText('已选 0 台')).toBeInTheDocument();
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

  it('shows occupancy links in all-nodes view via parallel host detail fetch', async () => {
    renderPage({
      hosts: [
        { id: 'h1', ip: '172.21.8.143', status: 'ONLINE' },
        { id: 'h2', ip: '172.21.8.192', status: 'ONLINE' },
      ],
      devices: [
        { id: 1, serial: 'DEV-FREE', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'DEV-BUSY', host_id: 'h2', status: 'BUSY' },
      ],
      getHost: (id: string) => {
        if (id === 'h2') {
          return { id: 'h2', status: 'ONLINE', active_jobs: [{ id: 11, device_id: 2, plan_run_id: 77, status: 'RUNNING' }] };
        }
        return { id, status: 'ONLINE', active_jobs: [] };
      },
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

    fireEvent.click(screen.getByRole('button', { name: /确认节点与版本/ }));
    fireEvent.click(await screen.findByRole('button', { name: /进入执行前确认/ }));
    expect(await screen.findByText(/超出剩余可派发槽位 1 个/)).toBeInTheDocument();
    expect(screen.getByText(/心跳数据，仅供参考/)).toBeInTheDocument();
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
    // C2 降级：run 级覆盖不可行，只读展示 + 跳编辑 Plan
    expect(screen.getAllByText('继承 Plan，本次不可覆盖').length).toBeGreaterThanOrEqual(3);

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

    // 清空选择需二次确认
    fireEvent.click(screen.getByRole('button', { name: '清空选择' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '确认清空' }));

    await waitFor(() => {
      expect(screen.getByText('已选样机 Minimap')).toBeInTheDocument();
    });
    expect(mocks.toast.info).toHaveBeenCalledWith('已无选中样机，已返回样机选择');
  });

  it('offers undo when removing a device from the minimap', async () => {
    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });

    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    expect(screen.getByText(/已选 1 台/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /已选设备方块 1/ }));
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

  it('hides device counters on the plan step and shows them from device step', async () => {
    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });

    await screen.findByText(/启用步骤/);
    expect(screen.queryByText('已选 0 台')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '清空选择' })).not.toBeInTheDocument();

    await goToDeviceStep();
    expect(await screen.findByText('已选 0 台')).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole('button', { name: /数量与版本/ }));
    expect(await screen.findByText('节点数量与版本一致性确认')).toBeInTheDocument();

    // 等防抖写入落盘
    await waitFor(() => {
      const raw = sessionStorage.getItem('stp.planExecute.draft.v1');
      expect(raw).toBeTruthy();
      expect(JSON.parse(raw as string).deviceIds).toEqual([1]);
    });
    first.unmount();

    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });
    // 设备集与步骤均恢复：直接进入「数量与版本确认」且计数为 1
    expect(await screen.findByText('节点数量与版本一致性确认')).toBeInTheDocument();
    expect(screen.getByText(/已选 1 台/)).toBeInTheDocument();
  });

  it('prefers URL devices over the draft and overwrites it', async () => {
    sessionStorage.setItem('stp.planExecute.draft.v1', JSON.stringify({
      planId: 7,
      deviceIds: [2],
      currentStep: 1,
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

    // URL 预填生效：仅 DEV-1 被恢复并跳到版本确认
    expect(await screen.findByText('节点数量与版本一致性确认')).toBeInTheDocument();
    expect(screen.getByText(/已选 1 台/)).toBeInTheDocument();

    // 回到样机选择核对：DEV-1 选中、DEV-2 未选（草稿 [2] 被忽略）
    fireEvent.click(screen.getByRole('button', { name: /先定位节点/ }));
    expect(await screen.findByLabelText(/DEV-1/)).toBeChecked();
    expect(screen.getByLabelText(/DEV-2/)).not.toBeChecked();
  });

  it('keeps URL plan authoritative while restoring draft devices', async () => {
    sessionStorage.setItem('stp.planExecute.draft.v1', JSON.stringify({
      planId: 99,
      deviceIds: [1],
      currentStep: 1,
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
    fireEvent.click(screen.getByRole('button', { name: /选择并核对测试计划/ }));
    expect(await screen.findByText(/启用步骤/)).toBeInTheDocument();
  });

  it('clears the draft after a successful launch and on cancel', async () => {
    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });
    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    fireEvent.click(screen.getByRole('button', { name: /前置项、参数/ }));
    fireEvent.click(screen.getByRole('button', { name: /预览并发起/ }));
    await screen.findByText('确认执行');
    fireEvent.click(screen.getByRole('button', { name: /确认发起/ }));

    await waitFor(() => expect(api.plans.run).toHaveBeenCalled());
    expect(sessionStorage.getItem('stp.planExecute.draft.v1')).toBeNull();
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
      const raw = sessionStorage.getItem('stp.planExecute.draft.v1');
      expect(raw).toBeTruthy();
      expect(JSON.parse(raw as string).deviceIds).toEqual([1]);
    });
    fireEvent.click(screen.getByRole('button', { name: /前置项、参数/ }));
    fireEvent.click(screen.getByRole('button', { name: /预览并发起/ }));
    fireEvent.click(await screen.findByRole('button', { name: /确认发起/ }));

    expect(await screen.findByText('PlanRun #91 派发失败')).toBeInTheDocument();
    const raw = sessionStorage.getItem('stp.planExecute.draft.v1');
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
    expect(screen.getByRole('button', { name: /全选筛选结果 \(1\)/ })).toBeInTheDocument();
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

  it('passes optional run note into launch payload and preview', async () => {
    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });

    await goToDeviceStep();
    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    fireEvent.click(screen.getByRole('button', { name: /前置项、参数/ }));

    const note = await screen.findByLabelText(/执行备注/);
    fireEvent.change(note, { target: { value: '  sprint4 smoke  ' } });

    fireEvent.click(screen.getByRole('button', { name: /预览并发起/ }));
    expect(await screen.findByText('sprint4 smoke')).toBeInTheDocument();

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
    fireEvent.click(screen.getByRole('button', { name: /前置项、参数/ }));
    fireEvent.click(screen.getByRole('button', { name: /预览并发起/ }));

    expect(await screen.findByRole('button', { name: /预览中/ })).toBeDisabled();
    resolvePreview({
      plan_name: 'Smoke Plan',
      device_count: 1,
      job_count: 1,
      total_steps: 1,
      device_ids: [1],
    });
    expect(await screen.findByText('确认执行')).toBeInTheDocument();
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
});
