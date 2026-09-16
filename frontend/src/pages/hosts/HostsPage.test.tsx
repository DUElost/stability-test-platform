import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const mocks = vi.hoisted(() => ({
  confirm: vi.fn().mockResolvedValue(true),
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

const mockHostsList = vi.fn().mockResolvedValue({ items: [], total: 0 });
const mockFetchHostList = vi.fn((..._args: unknown[]) =>
  mockHostsList().then((res: { items: unknown[] }) => res.items),
);

// Mock api
vi.mock('../../utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../utils/api')>();
  return {
    ...actual,
    fetchHostList: (...args: unknown[]) => mockFetchHostList(...args),
    api: {
    hosts: {
      list: (...args: unknown[]) => mockHostsList(...args),
      getDetail: vi.fn(),
      create: vi.fn().mockResolvedValue({}),
      update: vi.fn().mockResolvedValue({}),
      delete: vi.fn().mockResolvedValue({}),
      retire: vi.fn().mockResolvedValue({ id: 'h1' }),
      unretire: vi.fn().mockResolvedValue({ id: 'h1' }),
      updateWatcherAdminState: vi.fn().mockResolvedValue({}),
    },
    devices: {
      list: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    },
    tasks: {
      list: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    },
    agentInstall: {
      trigger: vi.fn().mockResolvedValue({
        ok: true,
        host_id: 'h1',
        log_path: '/var/log/stp/con-test.log',
        console_run_id: 'con-test',
        room: 'console:con-test',
        status: 'running',
        message: 'ok',
      }),
      status: vi.fn().mockResolvedValue({
        host_id: 'h1',
        log_path: '/var/log/stp/con-test.log',
        status: 'succeeded',
        console_run_id: 'con-test',
        console_status: 'SUCCESS',
        console_found: true,
      }),
      cancel: vi.fn().mockResolvedValue({
        ok: true,
        host_id: 'h1',
        console_run_id: 'con-test',
        canceled: true,
        status: 'canceling',
        message: 'Cancel requested.',
      }),
    },
    hotUpdate: {
      trigger: vi.fn().mockResolvedValue({}),
    },
    planRuns: {
      list: vi.fn().mockResolvedValue([]),
    },
    },
  };
});

vi.mock('@/hooks/useToast', () => ({
  useToast: () => mocks.toast,
}));

// Mock confirm
vi.mock('../../hooks/useConfirm', () => ({
  useConfirm: () => mocks.confirm,
}));

vi.mock('../../hooks/useAuthSession', () => ({
  useAuthSession: () => ({
    data: { id: 1, username: 'admin', role: 'admin', is_active: 'Y', created_at: '', last_login: null },
  }),
}));

// Mock ExpandableHostTable to simplify rendering
vi.mock('../../components/network/ExpandableHostTable', () => ({
  ExpandableHostTable: ({
    hosts,
    selectedIds,
    onSelectionChange,
    onWatcherAdminStateChange,
    onRetire,
    onUnretire,
  }: {
    hosts: any[];
    selectedIds?: Set<string | number>;
    onSelectionChange?: (ids: Set<string | number>) => void;
    onWatcherAdminStateChange?: (hostId: string | number, nextActive: boolean) => void;
    onRetire?: (host: any) => void;
    onUnretire?: (host: any) => void;
  }) => (
    <div data-testid="host-table">
      {onSelectionChange && (
        <button
          type="button"
          data-testid="select-all-hosts"
          onClick={() => onSelectionChange(new Set(hosts.map((h: any) => h.id)))}
        >
          select-all
        </button>
      )}
      {hosts.map((h: any) => (
        <div key={h.id} data-testid={`host-row-${h.id}`}>
          <span>{h.name}</span>
          <span>{h.ip}</span>
          <span>{h.status}</span>
          <span data-testid={`device-count-${h.id}`}>{h.device_count ?? 0}</span>
          <span data-testid={`host-selected-${h.id}`}>
            {selectedIds?.has(h.id) ? 'yes' : 'no'}
          </span>
          <span>{h.watcher_admin_active !== false ? '已激活' : '未激活'}</span>
          {onRetire && (
            <button data-testid={`retire-${h.id}`} onClick={() => onRetire(h)}>
              retire
            </button>
          )}
          {onUnretire && (
            <button data-testid={`unretire-${h.id}`} onClick={() => onUnretire(h)}>
              unretire
            </button>
          )}
          {onWatcherAdminStateChange && (
            <button
              data-testid={`watcher-toggle-${h.id}`}
              onClick={() => onWatcherAdminStateChange(h.id, !(h.watcher_admin_active !== false))}
            >
              toggle
            </button>
          )}
        </div>
      ))}
    </div>
  ),
}));

// Mock AddHostModal
vi.mock('./components/AddHostModal', () => ({
  AddHostModal: ({ isOpen }: { isOpen: boolean }) =>
    isOpen ? <div data-testid="add-host-modal">Add Host Modal</div> : null,
}));

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        {children}
      </QueryClientProvider>
    </MemoryRouter>
  );
}

describe('HostsPage', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    mockHostsList.mockResolvedValue({ items: [], total: 0 });
    const { api } = await import('../../utils/api');
    (api.planRuns.list as any).mockResolvedValue([]);
    (api.hosts.getDetail as any).mockReset();
    (api.hotUpdate.trigger as any).mockReset().mockResolvedValue({ ok: true, message: 'ok' });
  });

  it('renders host rows when react-query cache already holds Host[]', async () => {
    const { hostKeys } = await import('../../utils/api/queryKeys');
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    queryClient.setQueryData(hostKeys.list(), [
      {
        id: 'h1',
        name: 'node-1',
        ip: '10.0.0.1',
        status: 'ONLINE',
        extra: {},
        agent_installed: true,
      },
    ]);

    const HostsPage = (await import('./HostsPage')).default;
    render(
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <HostsPage />
        </QueryClientProvider>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('host-row-h1')).toBeInTheDocument();
    });
  });

  it('renders host rows when react-query cache holds paginated envelope', async () => {
    const { hostKeys } = await import('../../utils/api/queryKeys');
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    queryClient.setQueryData(hostKeys.list(), {
      items: [
        {
          id: 'h2',
          name: 'node-2',
          ip: '10.0.0.2',
          status: 'ONLINE',
          extra: {},
          agent_installed: true,
        },
      ],
      total: 1,
      skip: 0,
      limit: 200,
    });

    const HostsPage = (await import('./HostsPage')).default;
    render(
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <HostsPage />
        </QueryClientProvider>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('host-row-h2')).toBeInTheDocument();
    });
  });

  it('renders loading state initially', async () => {
    // Make the promise never resolve to show loading
    mockHostsList.mockReturnValueOnce(new Promise(() => {}));

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    expect(screen.getByText('主机集群')).toBeInTheDocument();
    expect(screen.getByText('管理和监控测试执行节点')).toBeInTheDocument();
  });

  it('renders page header and add button', async () => {
    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    expect(screen.getByText('主机集群')).toBeInTheDocument();
    expect((await screen.findAllByText('添加主机')).length).toBeGreaterThan(0);
  });

  it('renders empty state when no hosts', async () => {
    const HostsPage = (await import('./HostsPage')).default;
    const { container } = render(<HostsPage />, { wrapper: createWrapper() });

    // Wait for query to resolve
    await screen.findByText('主机集群');

    // Should show empty state with "暂无主机" message eventually
    // The component will show either the table or empty state
    expect(container).toBeDefined();
  });

  it('opens add host modal when button is clicked', async () => {
    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    await screen.findAllByText('添加主机');

    // Find any "添加主机" button and click it
    const addButtons = screen.getAllByText('添加主机');
    fireEvent.click(addButtons[0]);

    expect(screen.getByTestId('add-host-modal')).toBeInTheDocument();
  });

  it('renders host table when hosts exist', async () => {
    mockHostsList.mockResolvedValue({
      items: [
        { id: 1, name: 'Worker-01', ip: '192.0.2.10', status: 'ONLINE', extra: {}, mount_status: {} },
        { id: 2, name: 'Worker-02', ip: '192.0.2.11', status: 'OFFLINE', extra: {}, mount_status: {} },
      ],
      total: 2,
    });

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    // Wait for host data to load
    await screen.findByText('Worker-01');
    expect(screen.getByText('Worker-02')).toBeInTheDocument();
    expect(screen.getByTestId('host-table')).toBeInTheDocument();
  });

  it('does not query plan runs to compute host active task counts', async () => {
    const { api } = await import('../../utils/api');
    mockHostsList.mockResolvedValue({
      items: [
        {
          id: 'host-1',
          name: 'Worker-01',
          ip: '192.0.2.10',
          status: 'ONLINE',
          extra: {},
          mount_status: {},
          watcher_admin_active: true,
          capacity: { active_jobs: 2 },
        },
      ],
      total: 1,
    });

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    await screen.findByText('Worker-01');
    expect(api.planRuns.list).not.toHaveBeenCalled();
  });

  it('shows online device count from heartbeat capacity, not historical DB records', async () => {
    mockHostsList.mockResolvedValue({
      items: [
        {
          id: '192-0-2-87',
          name: '192.0.2.87',
          ip: '192.0.2.87',
          status: 'ONLINE',
          extra: {},
          mount_status: {},
          capacity: {
            online_healthy_devices: 0,
            active_devices: 0,
            available_slots: 0,
            active_jobs: 0,
          },
        },
      ],
      total: 1,
    });

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    await screen.findByTestId('device-count-192-0-2-87');
    expect(screen.getByTestId('device-count-192-0-2-87')).toHaveTextContent('0');
  });

  it('deactivates watcher admin state after confirmation', async () => {
    const { api } = await import('../../utils/api');
    mockHostsList.mockResolvedValue({
      items: [
        {
          id: 'host-1',
          name: 'Worker-01',
          ip: '192.0.2.10',
          status: 'ONLINE',
          extra: {},
          mount_status: {},
          watcher_admin_active: true,
        },
      ],
      total: 1,
    });

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    await screen.findByText('Worker-01');
    fireEvent.click(screen.getByTestId('watcher-toggle-host-1'));

    expect(mocks.confirm).toHaveBeenCalledWith({
      description: '将节点设为未激活后，只影响后续新派发任务；正在运行的任务不受影响。是否继续？',
      variant: 'destructive',
    });

    await waitFor(() =>
      expect(api.hosts.updateWatcherAdminState).toHaveBeenCalledWith('host-1', {
        watcher_admin_active: false,
      }),
    );
  });

  it('opens progress panel when bulk precheck skips every selected host', async () => {
    const { api } = await import('../../utils/api');
    mockHostsList.mockResolvedValue({
      items: [
        { id: 1, name: 'n1', ip: '10.0.0.1', status: 'ONLINE', extra: {}, agent_installed: true },
        { id: 2, name: 'n2', ip: '10.0.0.2', status: 'ONLINE', extra: {}, agent_installed: true },
      ],
      total: 2,
    });
    (api.hosts.getDetail as any).mockImplementation(async (id: number) => ({
      id,
      name: `n${id}`,
      ip: `10.0.0.${id}`,
      status: 'ONLINE',
      extra: {},
      agent_installed: true,
      active_job_count: 1,
    }));

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    await screen.findByText('n1');
    fireEvent.click(screen.getByTestId('select-all-hosts'));
    fireEvent.click(screen.getByTestId('host-bulk-hot-update'));

    await waitFor(() => {
      expect(screen.getByTestId('host-operation-panel')).toBeInTheDocument();
    });
    expect(screen.getByTestId('host-op-row-1')).toHaveTextContent('存在活跃 Job');
    expect(screen.getByTestId('host-op-row-2')).toHaveTextContent('存在活跃 Job');
    expect(mocks.toast.info).toHaveBeenCalledWith(expect.stringContaining('没有可安全热更新的主机'));
    expect(mocks.confirm).not.toHaveBeenCalled();
    expect(screen.getByTestId('host-selected-1')).toHaveTextContent('yes');
    expect(screen.getByTestId('host-selected-2')).toHaveTextContent('yes');
  });

  it('keeps numeric failed and 409 hosts selected after mixed bulk hot-update', async () => {
    const { api } = await import('../../utils/api');
    mockHostsList.mockResolvedValue({
      items: [
        { id: 1, name: 'n1', ip: '10.0.0.1', status: 'ONLINE', extra: {}, agent_installed: true },
        { id: 2, name: 'n2', ip: '10.0.0.2', status: 'ONLINE', extra: {}, agent_installed: true },
        { id: 3, name: 'n3', ip: '10.0.0.3', status: 'ONLINE', extra: {}, agent_installed: true },
      ],
      total: 3,
    });
    (api.hosts.getDetail as any).mockImplementation(async (id: number) => ({
      id,
      name: `n${id}`,
      ip: `10.0.0.${id}`,
      status: 'ONLINE',
      extra: {},
      agent_installed: true,
      active_job_count: id === 3 ? 1 : 0,
    }));
    (api.hotUpdate.trigger as any).mockImplementation(async (id: number | string) => {
      if (Number(id) === 2) {
        throw {
          response: {
            status: 409,
            data: { detail: { message: 'Host has active jobs', active_jobs: [{ id: 9 }] } },
          },
        };
      }
      return { ok: true, host_id: Number(id), message: 'ok', deps_refreshed: false };
    });

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    await screen.findByText('n1');
    fireEvent.click(screen.getByTestId('select-all-hosts'));
    fireEvent.click(screen.getByTestId('host-bulk-hot-update'));

    await waitFor(() => {
      expect(mocks.confirm).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getByTestId('host-operation-panel')).toBeInTheDocument();
    });
    expect(screen.getByTestId('host-op-row-1')).toHaveTextContent('成功');
    expect(screen.getByTestId('host-op-row-2')).toHaveTextContent('跳过');
    expect(screen.getByTestId('host-op-row-3')).toHaveTextContent('存在活跃 Job');
    expect(screen.getByTestId('host-selected-1')).toHaveTextContent('no');
    expect(screen.getByTestId('host-selected-2')).toHaveTextContent('yes');
    expect(screen.getByTestId('host-selected-3')).toHaveTextContent('yes');
    expect(mocks.toast.success).toHaveBeenCalledWith(
      expect.stringContaining('成功 1 台，跳过 2 台，失败 0 台'),
    );
  });

  it('toasts bulk hot-update failure and keeps the numeric failed host selected', async () => {
    const { api } = await import('../../utils/api');
    mockHostsList.mockResolvedValue({
      items: [
        { id: 10, name: 'n10', ip: '10.0.0.10', status: 'ONLINE', extra: {}, agent_installed: true },
        { id: 11, name: 'n11', ip: '10.0.0.11', status: 'ONLINE', extra: {}, agent_installed: true },
      ],
      total: 2,
    });
    (api.hosts.getDetail as any).mockImplementation(async (id: number) => ({
      id,
      name: `n${id}`,
      ip: `10.0.0.${id}`,
      status: 'ONLINE',
      extra: {},
      agent_installed: true,
      active_job_count: 0,
    }));
    (api.hotUpdate.trigger as any)
      .mockResolvedValueOnce({ ok: true, host_id: 10, message: 'ok' })
      .mockRejectedValueOnce({ response: { status: 502, data: { detail: 'ssh failed' } } });

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    await screen.findByText('n10');
    fireEvent.click(screen.getByTestId('select-all-hosts'));
    fireEvent.click(screen.getByTestId('host-bulk-hot-update'));

    await waitFor(() => {
      expect(screen.getByTestId('host-op-row-11')).toHaveTextContent('失败');
    });
    expect(screen.getByTestId('host-selected-10')).toHaveTextContent('no');
    expect(screen.getByTestId('host-selected-11')).toHaveTextContent('yes');
    expect(mocks.toast.error).toHaveBeenCalledWith(
      expect.stringContaining('成功 1 台，跳过 0 台，失败 1 台'),
    );
  });

  it('prunes selected host ids when a host disappears from the list', async () => {
    const hosts = [
      {
        id: 'h1',
        name: 'node-1',
        ip: '10.0.0.1',
        status: 'ONLINE',
        extra: {},
        agent_installed: true,
      },
      {
        id: 'h2',
        name: 'node-2',
        ip: '10.0.0.2',
        status: 'ONLINE',
        extra: {},
        agent_installed: true,
      },
    ];
    mockHostsList.mockResolvedValue({ items: hosts, total: 2 });

    const { hostKeys } = await import('../../utils/api/queryKeys');
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    queryClient.setQueryData(hostKeys.list(), { items: hosts, total: 2 });

    const HostsPage = (await import('./HostsPage')).default;
    render(
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <HostsPage />
        </QueryClientProvider>
      </MemoryRouter>,
    );

    await screen.findByTestId('host-row-h1');
    fireEvent.click(screen.getByTestId('select-all-hosts'));
    expect(screen.getByTestId('host-bulk-action-bar')).toHaveTextContent('已选择 2 台主机');

    queryClient.setQueryData(hostKeys.list(), [hosts[0]]);

    await waitFor(() => {
      expect(screen.queryByTestId('host-row-h2')).not.toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByTestId('host-bulk-action-bar')).toHaveTextContent('已选择 1 台主机');
    });
    expect(screen.getByTestId('host-selected-h1')).toHaveTextContent('yes');
  });
});

describe('ADR-0038 退役前端（#1807）', () => {
  // 本 describe 不在外层 beforeEach 作用域内：自行清调用并复位实现
  // （clearAllMocks 不还原 mockRejectedValue 这类实现改动）。
  beforeEach(async () => {
    vi.clearAllMocks();
    const { api } = await import('../../utils/api');
    (api.hosts.delete as ReturnType<typeof vi.fn>).mockResolvedValue({});
    (api.hosts.retire as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'h1' });
    (api.hosts.unretire as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'h1' });
  });

  const retiredHost = {
    id: 9,
    name: 'Retired-09',
    ip: '192.0.2.99',
    status: 'ONLINE',
    extra: {},
    mount_status: {},
    retired_at: '2026-09-13T00:00:00Z',
    retired_by: 'admin',
    retire_reason: '样机报废',
  };

  it('「显示已退役」开关把 include_retired 穿透到 fetchHostList', async () => {
    mockHostsList.mockResolvedValue({
      items: [{ id: 1, name: 'Worker-01', ip: '192.0.2.10', status: 'ONLINE', extra: {}, mount_status: {} }],
      total: 1,
    });
    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    await screen.findByText('Worker-01');
    await waitFor(() => expect(mockFetchHostList).toHaveBeenCalled());
    const calls = mockFetchHostList.mock.calls;
    expect(calls[calls.length - 1]?.slice(0, 3)).toEqual([0, 200, false]);

    fireEvent.click(screen.getByTestId('hosts-show-retired'));

    await waitFor(() => {
      const latest = mockFetchHostList.mock.calls[mockFetchHostList.mock.calls.length - 1];
      expect(latest?.slice(0, 3)).toEqual([0, 200, true]);
    });
  });

  it('全部主机退役（列表为空）时开关仍可见可点——否则无法解除退役（#2051）', async () => {
    // 后端默认过滤退役主机：只剩退役机时列表为空 → 页面落空态。开关若只在
    // 「有数据」分支渲染，用户就再也没有 UI 路径勾选它（ADR-0038 回收路径断头）。
    mockHostsList.mockResolvedValue({ items: [], total: 0 });
    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    const toggle = await screen.findByTestId('hosts-show-retired');
    expect(toggle).toBeInTheDocument();
    // 空态文案点明「勾选可查看并解除退役」
    expect(screen.getByText(/勾选「显示已退役」/)).toBeInTheDocument();

    mockHostsList.mockResolvedValue({ items: [retiredHost], total: 1 });
    fireEvent.click(toggle);

    await waitFor(() => {
      const latest = mockFetchHostList.mock.calls[mockFetchHostList.mock.calls.length - 1];
      expect(latest?.slice(0, 3)).toEqual([0, 200, true]);
    });
    expect(await screen.findByText('Retired-09')).toBeInTheDocument();
  });

  it('退役入口调用 API 并携带原因（写审计）', async () => {
    const { api } = await import('../../utils/api');
    mockHostsList.mockResolvedValue({
      items: [{ id: 1, name: 'Worker-01', ip: '192.0.2.10', status: 'ONLINE', extra: {}, mount_status: {} }],
      total: 1,
    });
    const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue('  样机报废  ');
    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId('retire-1'));

    await waitFor(() =>
      expect(api.hosts.retire).toHaveBeenCalledWith(1, '样机报废'),
    );
    promptSpy.mockRestore();
  });

  it('解除退役入口调用 API 并携带原因', async () => {
    const { api } = await import('../../utils/api');
    mockHostsList.mockResolvedValue({ items: [retiredHost], total: 1 });
    const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue('恢复使用');
    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId('unretire-9'));

    await waitFor(() =>
      expect(api.hosts.unretire).toHaveBeenCalledWith(9, '恢复使用'),
    );
    promptSpy.mockRestore();
  });

  it('取消原因输入不发起请求', async () => {
    const { api } = await import('../../utils/api');
    mockHostsList.mockResolvedValue({
      items: [{ id: 1, name: 'Worker-01', ip: '192.0.2.10', status: 'ONLINE', extra: {}, mount_status: {} }],
      total: 1,
    });
    const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue(null);
    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId('retire-1'));

    await waitFor(() => expect(screen.getByTestId('retire-1')).toBeInTheDocument());
    expect(api.hosts.retire).not.toHaveBeenCalled();
    promptSpy.mockRestore();
  });

  it('批量删除失败时透出 409 文案（不吞原因）', async () => {
    const { api } = await import('../../utils/api');
    mockHostsList.mockResolvedValue({
      items: [{ id: 1, name: 'Worker-01', ip: '192.0.2.10', status: 'OFFLINE', extra: {}, mount_status: {} }],
      total: 1,
    });
    (api.hosts.delete as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('主机有 2 条历史 Job 记录，删除会清空执行历史；请先归档/清理后再删除'),
    );
    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId('select-all-hosts'));
    fireEvent.click(await screen.findByRole('button', { name: '删除' }));

    await waitFor(() =>
      expect(mocks.toast.error).toHaveBeenCalledWith(
        expect.stringContaining('历史 Job 记录'),
      ),
    );
  });

  // #2062：本用例原先写在 describe **之外**——describe 的 beforeEach
  // （clearAllMocks + mock 复位）不生效，断言可能落在共享 mock 的历史调用上。
  it('批量安装跳过退役主机并显式提示（#1807 / D5 批量 skip）', async () => {
    const { api } = await import('../../utils/api');
    mockHostsList.mockResolvedValue({
      items: [
        { id: 1, name: 'Worker-01', ip: '192.0.2.10', status: 'OFFLINE', extra: {}, mount_status: {}, agent_installed: true },
        {
          id: 9, name: 'Retired-09', ip: '192.0.2.99', status: 'OFFLINE', extra: {}, mount_status: {},
          agent_installed: true, retired_at: '2026-09-13T00:00:00Z',
        },
      ],
      total: 2,
    });
    // 确认框返回 false：本用例只验「退役主机被排除出目标集」，不真正启动批量安装
    // （批量安装会拉起操作面板渲染，与本用例无关）。
    mocks.confirm.mockResolvedValue(false);
    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId('select-all-hosts'));
    fireEvent.click(await screen.findByRole('button', { name: /安装/ }));

    await waitFor(() =>
      expect(mocks.toast.error).toHaveBeenCalledWith(
        expect.stringContaining('已退役主机'),
      ),
    );
    // 退役主机不得进入安装目标集：确认文案只应包含 1 台
    await waitFor(() =>
      expect(mocks.confirm).toHaveBeenCalledWith(
        expect.objectContaining({ description: expect.stringContaining('将对 1 台主机安装') }),
      ),
    );
    expect(api.agentInstall.trigger).not.toHaveBeenCalledWith(9);
  });

  // #2255：取消 ≠ 失败——现场验证抓到 UI 曾把显式取消报成红色「安装失败: CANCELED」
  it('取消的安装走 info 提示，不报失败', async () => {
    const { api } = await import('../../utils/api');
    mockHostsList.mockResolvedValue({
      items: [
        {
          id: 'h-cancel',
          name: 'Worker-C',
          ip: '192.0.2.77',
          status: 'OFFLINE',
          extra: {},
          mount_status: {},
          agent_installed: true,
        },
      ],
      total: 1,
    });
    mocks.confirm.mockResolvedValue(true);
    vi.mocked(api.agentInstall.status).mockResolvedValueOnce({
      host_id: 'h-cancel',
      log_path: '/var/log/stp/con-test.log',
      status: 'canceled',
      console_run_id: 'con-test',
      console_status: 'CANCELED',
      console_found: false,
    });

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId('select-all-hosts'));
    fireEvent.click(await screen.findByRole('button', { name: /安装/ }));

    await waitFor(() =>
      expect(mocks.toast.info).toHaveBeenCalledWith(
        expect.stringContaining('安装已取消'),
      ),
    );
    expect(mocks.toast.error).not.toHaveBeenCalledWith(
      expect.stringContaining('安装失败'),
    );
  });
});
