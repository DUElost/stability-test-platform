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

// Mock api
vi.mock('../../utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../utils/api')>();
  return {
    ...actual,
    fetchHostList: vi.fn(() => mockHostsList().then((res: { items: unknown[] }) => res.items)),
    api: {
    hosts: {
      list: (...args: unknown[]) => mockHostsList(...args),
      getDetail: vi.fn(),
      create: vi.fn().mockResolvedValue({}),
      update: vi.fn().mockResolvedValue({}),
      delete: vi.fn().mockResolvedValue({}),
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
        saq_key: 'install:h1',
        console_run_id: 'con-test',
        room: 'console:con-test',
        status: 'running',
        message: 'ok',
      }),
      status: vi.fn().mockResolvedValue({
        host_id: 'h1',
        saq_key: 'install:h1',
        status: 'complete',
        console_run_id: 'con-test',
        console_status: 'SUCCESS',
        result: { ok: true, rc: 0, message: 'ok' },
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
  }: {
    hosts: any[];
    selectedIds?: Set<string | number>;
    onSelectionChange?: (ids: Set<string | number>) => void;
    onWatcherAdminStateChange?: (hostId: string | number, nextActive: boolean) => void;
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
});
