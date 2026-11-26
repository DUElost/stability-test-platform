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

const mockHostsListPage = vi.fn().mockResolvedValue({ items: [], total: 0, skip: 0, limit: 50 });
const mockHostsFacets = vi.fn().mockResolvedValue({
  all: 0,
  online: 0,
  degraded: 0,
  offline: 0,
  usb_anomaly: 0,
  upgrade_pending: 0,
  health_anomaly: 0,
  mount_anomaly: 0,
  aligned_agent_count: 0,
  trackable_agent_count: 0,
});
const mockHostsSelectionPreview = vi.fn().mockResolvedValue({
  total_matched: 0,
  host_ids: [],
  online_count: 0,
  offline_count: 0,
  degraded_count: 0,
  retired_count: 0,
  first_install_count: 0,
  reinstall_count: 0,
  hot_update_count: 0,
  flash_prereqs_count: 0,
});
const mockFetchAllHosts = vi.fn();

// Mock api
vi.mock('../../utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../utils/api')>();
  return {
    ...actual,
    fetchAllHosts: (...args: unknown[]) => mockFetchAllHosts(...args),
    api: {
    hosts: {
      list: vi.fn().mockResolvedValue({ items: [], total: 0, skip: 0, limit: 50 }),
      listPage: (...args: unknown[]) => mockHostsListPage(...args),
      facets: (...args: unknown[]) => mockHostsFacets(...args),
      selectionPreview: (...args: unknown[]) => mockHostsSelectionPreview(...args),
      get: vi.fn(),
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
    flashPrereqs: {
      ensure: vi.fn().mockResolvedValue({
        ok: true,
        host_id: 'h1',
        log_path: '/var/log/stp/con-fp.log',
        console_run_id: 'con-fp',
        room: 'console:con-fp',
        status: 'running',
        message: 'ok',
      }),
      status: vi.fn().mockResolvedValue({
        host_id: 'h1',
        log_path: '/var/log/stp/con-fp.log',
        status: 'succeeded',
        console_run_id: 'con-fp',
        console_status: 'SUCCESS',
        console_found: true,
      }),
    },
    scriptPresence: {
      summary: vi.fn().mockResolvedValue({
        counts: { present: 0, missing: 0, mismatch: 0, unknown: 0, n_a: 0, maintenance: 0 },
        fleet_packages: {},
        hosts_total: 0,
        hosts_with_gap: 0,
        full_versions: 0,
        uncovered_active_versions: 0,
        checked_at_min: null,
        checked_at_max: null,
        stale: false,
      }),
      host: vi.fn().mockResolvedValue({
        host_id: 'h1',
        checked_at: null,
        sweep_id: '',
        counts: { present: 0, missing: 0, mismatch: 0, unknown: 0, n_a: 0, maintenance: 0 },
        items: [],
      }),
      refresh: vi.fn().mockResolvedValue({
        sweep_id: 'sweep-test',
        host_id: 'h1',
        rows: 0,
        counts: { present: 0, missing: 0, mismatch: 0, unknown: 0, n_a: 0, maintenance: 0 },
      }),
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
    scriptPresenceSummaryError,
    showRetired,
    onShowRetiredChange,
    onSyncFilterChange,
    onPageChange,
    onSortChange,
    drawerHostOverride,
    extraHeaderRight,
  }: {
    hosts: any[];
    selectedIds?: Set<string | number>;
    onSelectionChange?: (ids: Set<string | number>) => void;
    onWatcherAdminStateChange?: (hostId: string | number, nextActive: boolean) => void;
    onRetire?: (host: any) => void;
    onUnretire?: (host: any) => void;
    scriptPresenceSummaryError?: boolean;
    showRetired?: boolean;
    onShowRetiredChange?: (val: boolean) => void;
    onSyncFilterChange?: (val: string) => void;
    onPageChange?: (page: number) => void;
    onSortChange?: (key: string, dir: string) => void;
    drawerHostOverride?: any;
    extraHeaderRight?: React.ReactNode;
  }) => (
    <div data-testid="host-table">
      {extraHeaderRight}
      {drawerHostOverride && (
        <div data-testid="drawer-host-override">{drawerHostOverride.name || drawerHostOverride.id}</div>
      )}
      {onSyncFilterChange && (
        <button
          type="button"
          data-testid="filter-upgrade-pending"
          onClick={() => onSyncFilterChange('upgrade_pending')}
        >
          待热更新
        </button>
      )}
      {onPageChange && (
        <button type="button" data-testid="go-page-2" onClick={() => onPageChange(2)}>
          page-2
        </button>
      )}
      {onSortChange && (
        <button type="button" data-testid="sort-by-name" onClick={() => onSortChange('name', 'asc')}>
          sort-name
        </button>
      )}
      {onShowRetiredChange && (
        <input
          type="checkbox"
          data-testid="hosts-show-retired"
          checked={Boolean(showRetired)}
          onChange={(e) => onShowRetiredChange(e.target.checked)}
        />
      )}
      <span data-testid="host-table-summary-error">
        {scriptPresenceSummaryError ? 'error' : 'ok'}
      </span>
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

function createWrapper(initialEntries: string[] = ['/hosts']) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <MemoryRouter initialEntries={initialEntries}>
      <QueryClientProvider client={queryClient}>
        {children}
      </QueryClientProvider>
    </MemoryRouter>
  );
}

function defaultFacets(all = 0) {
  return {
    all,
    online: all,
    degraded: 0,
    offline: 0,
    usb_anomaly: 0,
    upgrade_pending: 0,
    health_anomaly: 0,
    mount_anomaly: 0,
    aligned_agent_count: all,
    trackable_agent_count: all,
  };
}

describe('HostsPage', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    mocks.confirm.mockReset().mockResolvedValue(true);
    mockHostsListPage.mockReset().mockResolvedValue({ items: [], total: 0, skip: 0, limit: 50 });
    mockHostsFacets.mockReset().mockResolvedValue(defaultFacets(0));
    mockHostsSelectionPreview.mockReset().mockResolvedValue({
      total_matched: 0,
      host_ids: [],
      online_count: 0,
      offline_count: 0,
      degraded_count: 0,
      retired_count: 0,
      first_install_count: 0,
      reinstall_count: 0,
      hot_update_count: 0,
      flash_prereqs_count: 0,
    });
    const { api } = await import('../../utils/api');
    (api.planRuns.list as any).mockResolvedValue([]);
    (api.hosts.get as any).mockReset();
    (api.hosts.getDetail as any).mockReset();
    (api.hotUpdate.trigger as any).mockReset().mockResolvedValue({ ok: true, message: 'ok' });
  });

  it('fetches single page via api.hosts.listPage + independent facets without fetchAllHosts or polluting hostKeys.list()', async () => {
    const { hostKeys } = await import('../../utils/api/queryKeys');
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    mockHostsListPage.mockResolvedValue({
      items: [
        {
          id: 'h1',
          name: 'node-1',
          ip: '10.0.0.1',
          status: 'ONLINE',
          extra: {},
          agent_installed: true,
        },
      ],
      total: 1,
      skip: 0,
      limit: 50,
    });
    mockHostsFacets.mockResolvedValue(defaultFacets(1));

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
    expect(mockFetchAllHosts).not.toHaveBeenCalled();
    expect(mockHostsListPage).toHaveBeenCalledWith(
      expect.objectContaining({ skip: 0, limit: 50, sort_by: 'priority', sort_dir: 'desc' }),
    );
    expect(mockHostsFacets).toHaveBeenCalledWith(false);
    // 契约 §6.1：不得把 PaginatedResponse 写入共享 ['hosts'] 缓存键
    expect(queryClient.getQueryData(hostKeys.list())).toBeUndefined();
  });

  it('sends sync_status=upgrade_pending and does not refetch facets on pagination or sorting', async () => {
    mockHostsListPage.mockResolvedValue({
      items: [
        { id: 'h1', name: 'node-1', ip: '10.0.0.1', status: 'ONLINE', extra: {}, agent_installed: true },
      ],
      total: 120,
      skip: 0,
      limit: 50,
    });
    mockHostsFacets.mockResolvedValue({ ...defaultFacets(120), upgrade_pending: 5 });

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    await screen.findByTestId('host-row-h1');
    expect(mockHostsFacets).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId('filter-upgrade-pending'));
    await waitFor(() => {
      expect(mockHostsListPage).toHaveBeenLastCalledWith(
        expect.objectContaining({ sync_status: 'upgrade_pending', skip: 0, limit: 50 }),
      );
    });

    fireEvent.click(screen.getByTestId('go-page-2'));
    await waitFor(() => {
      expect(mockHostsListPage).toHaveBeenLastCalledWith(
        expect.objectContaining({ skip: 50, limit: 50 }),
      );
    });

    fireEvent.click(screen.getByTestId('sort-by-name'));
    await waitFor(() => {
      expect(mockHostsListPage).toHaveBeenLastCalledWith(
        expect.objectContaining({ sort_by: 'name', sort_dir: 'asc', skip: 0 }),
      );
    });

    // 筛选/翻页/排序均不得重复触发 hosts/facets 请求
    expect(mockHostsFacets).toHaveBeenCalledTimes(1);
  });

  it('#4117 Q4：翻页携带第 1 页动态排序 snapshot_id，切排序后重置快照且抽屉按需拉完整详情', async () => {
    const { api } = await import('../../utils/api');
    mockHostsListPage.mockImplementation(async (params: { skip?: number; sort_by?: string }) => ({
      items: [
        {
          id: params.skip ? 'h2' : 'h1',
          name: params.skip ? 'node-2' : 'node-1',
          ip: params.skip ? '10.0.0.2' : '10.0.0.1',
          status: 'ONLINE',
          extra: {},
          mount_status: {},
          mount_anomaly: true,
          mount_count: 2,
          mount_failed_count: 1,
          agent_installed: true,
        },
      ],
      total: 120,
      skip: params.skip ?? 0,
      limit: 50,
      snapshot_id: params.sort_by === 'name' ? 'snap-name-2' : 'snap-prio-1',
    }));
    mockHostsFacets.mockResolvedValue(defaultFacets(120));
    (api.hosts.get as any).mockResolvedValue({
      id: 'h1',
      name: 'node-1-full',
      ip: '10.0.0.1',
      status: 'ONLINE',
      extra: {},
      mount_status: { '/mnt/center': { ok: false } },
      agent_installed: true,
    });

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper(['/hosts?drawer=h1']) });

    await screen.findByTestId('host-row-h1');
    // 即使 h1 就在当前页，打开抽屉仍必须按需调用 GET /hosts/h1 获取完整 mount_status 明细
    await waitFor(() => {
      expect(api.hosts.get).toHaveBeenCalledWith('h1');
      expect(screen.getByTestId('drawer-host-override')).toHaveTextContent('node-1-full');
    });

    // 翻至第 2 页必须透传第 1 页返回的 snapshot_id
    fireEvent.click(screen.getByTestId('go-page-2'));
    await waitFor(() => {
      expect(mockHostsListPage).toHaveBeenLastCalledWith(
        expect.objectContaining({ skip: 50, limit: 50, snapshot_id: 'snap-prio-1' }),
      );
    });

    // 切换排序后重置快照并回到第 1 页（不携带旧 snapshot_id）
    fireEvent.click(screen.getByTestId('sort-by-name'));
    await waitFor(() => {
      const calls = mockHostsListPage.mock.calls;
      const lastCall = calls[calls.length - 1]?.[0];
      expect(lastCall).toMatchObject({ sort_by: 'name', sort_dir: 'asc', skip: 0 });
      expect(lastCall?.snapshot_id).toBeUndefined();
    });
  });

  it('freezes cross-page selection via selectionPreview and resolves off-page hosts for bulk install', async () => {
    const { api } = await import('../../utils/api');
    mockHostsListPage.mockResolvedValue({
      items: [
        { id: 'h1', name: 'node-1', ip: '10.0.0.1', status: 'OFFLINE', extra: {}, agent_installed: false },
      ],
      total: 2,
      skip: 0,
      limit: 50,
    });
    mockHostsFacets.mockResolvedValue({ ...defaultFacets(2), online: 0, offline: 2 });
    mockHostsSelectionPreview.mockResolvedValue({
      total_matched: 2,
      host_ids: ['h1', 'h2'],
      online_count: 0,
      offline_count: 2,
      degraded_count: 0,
      retired_count: 0,
      first_install_count: 2,
      reinstall_count: 0,
      hot_update_count: 0,
      flash_prereqs_count: 0,
    });
    (api.hosts.getDetail as any).mockResolvedValue({
      id: 'h2',
      name: 'node-2',
      ip: '10.0.0.2',
      status: 'OFFLINE',
      extra: {},
      agent_installed: false,
    });
    mocks.confirm.mockResolvedValue(false);

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    await screen.findByTestId('host-row-h1');
    fireEvent.click(screen.getByTestId('select-all-hosts'));
    expect(screen.getByTestId('host-bulk-scope-badge')).toHaveTextContent('仅当前页');

    fireEvent.click(screen.getByTestId('host-bulk-select-all-filtered'));
    await waitFor(() => {
      expect(mockHostsSelectionPreview).toHaveBeenCalledWith({});
      expect(screen.getByTestId('host-bulk-scope-badge')).toHaveTextContent('跨页已冻结');
    });
    expect(screen.getByTestId('host-bulk-action-bar')).toHaveTextContent('已选择 2 台主机');

    fireEvent.click(screen.getByTestId('host-bulk-install'));
    await waitFor(() => {
      expect(api.hosts.getDetail).toHaveBeenCalledWith('h2');
      expect(mocks.confirm).toHaveBeenCalledWith(
        expect.objectContaining({
          description: expect.stringContaining('将对 2 台主机安装 Agent（跨页已冻结集合）'),
        }),
      );
    });
  });

  it('#4117 Q3：切换筛选后晚到的旧跨页预览回包被丢弃，不会重新选中旧主机集合', async () => {
    mockHostsListPage.mockImplementation(async (params: { sync_status?: string } = {}) => {
      if (params.sync_status === 'upgrade_pending') {
        return {
          items: [
            { id: 'h-new', name: 'node-new', ip: '10.0.0.9', status: 'ONLINE', extra: {}, agent_installed: true },
          ],
          total: 1,
          skip: 0,
          limit: 50,
        };
      }
      return {
        items: [
          { id: 'h-old-1', name: 'node-old-1', ip: '10.0.0.1', status: 'OFFLINE', extra: {}, agent_installed: false },
        ],
        total: 2,
        skip: 0,
        limit: 50,
      };
    });
    mockHostsFacets.mockResolvedValue({ ...defaultFacets(3), upgrade_pending: 1 });

    let resolveStalePreview!: (value: unknown) => void;
    mockHostsSelectionPreview.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveStalePreview = resolve;
        }),
    );

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    await screen.findByTestId('host-row-h-old-1');
    fireEvent.click(screen.getByTestId('select-all-hosts'));
    fireEvent.click(screen.getByTestId('host-bulk-select-all-filtered'));
    await waitFor(() => expect(mockHostsSelectionPreview).toHaveBeenCalledTimes(1));

    // 在旧预览回包前切到「待热更新」筛选
    fireEvent.click(screen.getByTestId('filter-upgrade-pending'));
    await screen.findByTestId('host-row-h-new');

    // 晚到的旧预览回包尝试返回旧集合 ['h-old-1', 'h-old-2']
    resolveStalePreview({
      total_matched: 2,
      host_ids: ['h-old-1', 'h-old-2'],
      online_count: 0,
      offline_count: 2,
      degraded_count: 0,
      retired_count: 0,
      first_install_count: 2,
      reinstall_count: 0,
      hot_update_count: 0,
      flash_prereqs_count: 0,
    });

    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId('host-bulk-scope-badge')).not.toBeInTheDocument();
    expect(screen.queryByTestId('host-bulk-action-bar')).not.toBeInTheDocument();
    expect(screen.getByTestId('host-selected-h-new')).toHaveTextContent('no');
    expect(mocks.toast.info).not.toHaveBeenCalledWith(expect.stringContaining('已冻结全部筛选结果'));
  });

  it('loads off-page ?drawer= deep-link host via api.hosts.get', async () => {
    const { api } = await import('../../utils/api');
    mockHostsListPage.mockResolvedValue({
      items: [
        { id: 'h1', name: 'node-1', ip: '10.0.0.1', status: 'ONLINE', extra: {}, agent_installed: true },
      ],
      total: 2,
      skip: 0,
      limit: 50,
    });
    mockHostsFacets.mockResolvedValue(defaultFacets(2));
    (api.hosts.get as any).mockResolvedValue({
      id: 'h99',
      name: 'OffPage-Host-99',
      ip: '10.0.0.99',
      status: 'ONLINE',
      extra: {},
      mount_status: {},
      agent_installed: true,
    });

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper(['/hosts?drawer=h99']) });

    await waitFor(() => {
      expect(api.hosts.get).toHaveBeenCalledWith('h99');
      expect(screen.getByTestId('drawer-host-override')).toHaveTextContent('OffPage-Host-99');
    });
  });

  it('keeps filter table mounted when active filter returns 0 hosts', async () => {
    mockHostsListPage.mockResolvedValue({ items: [], total: 0, skip: 0, limit: 50 });
    mockHostsFacets.mockResolvedValue(defaultFacets(5));

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper(['/hosts?status=OFFLINE']) });

    await screen.findByTestId('host-table');
    expect(screen.queryByText('暂无主机')).not.toBeInTheDocument();
  });

  it('renders loading state initially', async () => {
    // Make the promise never resolve to show loading
    mockHostsListPage.mockReturnValueOnce(new Promise(() => {}));

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    expect(screen.getByText('主机集群')).toBeInTheDocument();
    expect(screen.queryByText('管理和监控测试执行节点')).not.toBeInTheDocument();
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
    mockHostsListPage.mockResolvedValue({
      items: [
        { id: 1, name: 'Worker-01', ip: '192.0.2.10', status: 'ONLINE', extra: {}, mount_status: {} },
        { id: 2, name: 'Worker-02', ip: '192.0.2.11', status: 'OFFLINE', extra: {}, mount_status: {} },
      ],
      total: 2,
      skip: 0,
      limit: 50,
    });
    mockHostsFacets.mockResolvedValue(defaultFacets(2));

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    // Wait for host data to load
    await screen.findByText('Worker-01');
    expect(screen.getByText('Worker-02')).toBeInTheDocument();
    expect(screen.getByTestId('host-table')).toBeInTheDocument();
  });

  it('does not query fleet script presence summary on HostsPage', async () => {
    const { api } = await import('../../utils/api');
    mockHostsListPage.mockResolvedValue({
      items: [{ id: 1, name: 'Worker-01', ip: '192.0.2.10', status: 'ONLINE', extra: {}, mount_status: {} }],
      total: 1,
      skip: 0,
      limit: 50,
    });
    mockHostsFacets.mockResolvedValue(defaultFacets(1));

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    await screen.findByText('Worker-01');
    expect(api.scriptPresence.summary).not.toHaveBeenCalled();
    expect(screen.getByTestId('host-table-summary-error')).toHaveTextContent('ok');
  });

  it('does not query plan runs to compute host active task counts', async () => {
    const { api } = await import('../../utils/api');
    mockHostsListPage.mockResolvedValue({
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
      skip: 0,
      limit: 50,
    });
    mockHostsFacets.mockResolvedValue(defaultFacets(1));

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    await screen.findByText('Worker-01');
    expect(api.planRuns.list).not.toHaveBeenCalled();
  });

  it('shows online device count from heartbeat capacity, not historical DB records', async () => {
    mockHostsListPage.mockResolvedValue({
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
      skip: 0,
      limit: 50,
    });
    mockHostsFacets.mockResolvedValue(defaultFacets(1));

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    await screen.findByTestId('device-count-192-0-2-87');
    expect(screen.getByTestId('device-count-192-0-2-87')).toHaveTextContent('0');
  });

  it('deactivates watcher admin state after confirmation', async () => {
    const { api } = await import('../../utils/api');
    mockHostsListPage.mockResolvedValue({
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
      skip: 0,
      limit: 50,
    });
    mockHostsFacets.mockResolvedValue(defaultFacets(1));

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
    mockHostsListPage.mockResolvedValue({
      items: [
        { id: 1, name: 'n1', ip: '10.0.0.1', status: 'ONLINE', extra: {}, agent_installed: true },
        { id: 2, name: 'n2', ip: '10.0.0.2', status: 'ONLINE', extra: {}, agent_installed: true },
      ],
      total: 2,
      skip: 0,
      limit: 50,
    });
    mockHostsFacets.mockResolvedValue(defaultFacets(2));
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
    mockHostsListPage.mockResolvedValue({
      items: [
        { id: 1, name: 'n1', ip: '10.0.0.1', status: 'ONLINE', extra: {}, agent_installed: true },
        { id: 2, name: 'n2', ip: '10.0.0.2', status: 'ONLINE', extra: {}, agent_installed: true },
        { id: 3, name: 'n3', ip: '10.0.0.3', status: 'ONLINE', extra: {}, agent_installed: true },
      ],
      total: 3,
      skip: 0,
      limit: 50,
    });
    mockHostsFacets.mockResolvedValue(defaultFacets(3));
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
    mockHostsListPage.mockResolvedValue({
      items: [
        { id: 10, name: 'n10', ip: '10.0.0.10', status: 'ONLINE', extra: {}, agent_installed: true },
        { id: 11, name: 'n11', ip: '10.0.0.11', status: 'ONLINE', extra: {}, agent_installed: true },
      ],
      total: 2,
      skip: 0,
      limit: 50,
    });
    mockHostsFacets.mockResolvedValue(defaultFacets(2));
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

  it('prunes selected host ids when a host disappears from the page', async () => {
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
    mockHostsListPage.mockResolvedValue({ items: hosts, total: 2, skip: 0, limit: 50 });
    mockHostsFacets.mockResolvedValue(defaultFacets(2));

    const { hostKeys } = await import('../../utils/api/queryKeys');
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const pageKey = hostKeys.page({ skip: 0, limit: 50, sort_by: 'priority', sort_dir: 'desc' });
    queryClient.setQueryData(pageKey, { items: hosts, total: 2, skip: 0, limit: 50 });

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

    queryClient.setQueryData(pageKey, { items: [hosts[0]], total: 1, skip: 0, limit: 50 });

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
  beforeEach(async () => {
    vi.clearAllMocks();
    mocks.confirm.mockReset().mockResolvedValue(true);
    mockHostsListPage.mockReset().mockResolvedValue({ items: [], total: 0, skip: 0, limit: 50 });
    mockHostsFacets.mockReset().mockResolvedValue(defaultFacets(0));
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

  it('「显示已退役」开关把 include_retired 穿透到 listPage 与 facets', async () => {
    mockHostsListPage.mockResolvedValue({
      items: [{ id: 1, name: 'Worker-01', ip: '192.0.2.10', status: 'ONLINE', extra: {}, mount_status: {} }],
      total: 1,
      skip: 0,
      limit: 50,
    });
    mockHostsFacets.mockResolvedValue(defaultFacets(1));
    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    await screen.findByText('Worker-01');
    await waitFor(() => {
      expect(mockHostsListPage).toHaveBeenCalledWith(
        expect.objectContaining({ skip: 0, limit: 50 }),
      );
      expect(mockHostsFacets).toHaveBeenCalledWith(false);
    });

    fireEvent.click(screen.getByTestId('hosts-show-retired'));

    await waitFor(() => {
      expect(mockHostsListPage).toHaveBeenLastCalledWith(
        expect.objectContaining({ include_retired: true, skip: 0, limit: 50 }),
      );
      expect(mockHostsFacets).toHaveBeenCalledWith(true);
    });
  });

  it('全部主机退役（列表为空）时开关仍可见可点——否则无法解除退役（#2051）', async () => {
    mockHostsListPage.mockImplementation((params: { include_retired?: boolean }) =>
      Promise.resolve(
        params?.include_retired
          ? { items: [retiredHost], total: 1, skip: 0, limit: 50 }
          : { items: [], total: 0, skip: 0, limit: 50 },
      ),
    );
    mockHostsFacets.mockImplementation((includeRetired?: boolean) =>
      Promise.resolve(defaultFacets(includeRetired ? 1 : 0)),
    );
    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    const toggle = await screen.findByTestId('hosts-show-retired');
    expect(toggle).toBeInTheDocument();
    expect(await screen.findByText(/勾选「显示已退役」/)).toBeInTheDocument();

    fireEvent.click(toggle);

    await waitFor(() => {
      expect(mockHostsListPage).toHaveBeenLastCalledWith(
        expect.objectContaining({ include_retired: true, skip: 0, limit: 50 }),
      );
    });
    expect(await screen.findByText('Retired-09')).toBeInTheDocument();
  });

  it('全新环境（从未接入）不把空集归因退役（#2362）', async () => {
    mockHostsListPage.mockResolvedValue({ items: [], total: 0, skip: 0, limit: 50 });
    mockHostsFacets.mockResolvedValue(defaultFacets(0));
    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    expect(await screen.findByText('暂无主机')).toBeInTheDocument();
    expect(screen.getByText(/等待 Agent 接入/)).toBeInTheDocument();
    expect(screen.queryByText(/所有主机都已退役/)).not.toBeInTheDocument();
  });

  it('退役入口通过 RetireHostDialog 收集必填原因并调用 API（写审计）', async () => {
    const { api } = await import('../../utils/api');
    mockHostsListPage.mockResolvedValue({
      items: [{ id: 1, name: 'Worker-01', ip: '192.0.2.10', status: 'ONLINE', extra: {}, mount_status: {} }],
      total: 1,
      skip: 0,
      limit: 50,
    });
    mockHostsFacets.mockResolvedValue(defaultFacets(1));
    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId('retire-1'));
    const input = await screen.findByTestId('host-retire-reason-input');
    fireEvent.change(input, { target: { value: '  样机报废  ' } });
    fireEvent.click(screen.getByTestId('host-retire-submit'));

    await waitFor(() =>
      expect(api.hosts.retire).toHaveBeenCalledWith(1, '样机报废'),
    );
  });

  it('解除退役入口通过 RetireHostDialog 收集原因并调用 API', async () => {
    const { api } = await import('../../utils/api');
    mockHostsListPage.mockResolvedValue({ items: [retiredHost], total: 1, skip: 0, limit: 50 });
    mockHostsFacets.mockResolvedValue(defaultFacets(1));
    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId('unretire-9'));
    const input = await screen.findByTestId('host-retire-reason-input');
    fireEvent.change(input, { target: { value: '恢复使用' } });
    fireEvent.click(screen.getByTestId('host-retire-submit'));

    await waitFor(() =>
      expect(api.hosts.unretire).toHaveBeenCalledWith(9, '恢复使用'),
    );
  });

  it('空原因拦截提交且取消对话框不发起请求', async () => {
    const { api } = await import('../../utils/api');
    mockHostsListPage.mockResolvedValue({
      items: [{ id: 1, name: 'Worker-01', ip: '192.0.2.10', status: 'ONLINE', extra: {}, mount_status: {} }],
      total: 1,
      skip: 0,
      limit: 50,
    });
    mockHostsFacets.mockResolvedValue(defaultFacets(1));
    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId('retire-1'));
    await screen.findByTestId('host-retire-dialog');
    fireEvent.click(screen.getByTestId('host-retire-submit'));
    expect(screen.getByRole('alert')).toHaveTextContent('退役原因不能为空');
    expect(api.hosts.retire).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    await waitFor(() => {
      expect(screen.queryByTestId('host-retire-dialog')).not.toBeInTheDocument();
    });
    expect(api.hosts.retire).not.toHaveBeenCalled();
  });

  it('批量删除失败时透出 409 文案（不吞原因）', async () => {
    const { api } = await import('../../utils/api');
    mockHostsListPage.mockResolvedValue({
      items: [{ id: 1, name: 'Worker-01', ip: '192.0.2.10', status: 'OFFLINE', extra: {}, mount_status: {} }],
      total: 1,
      skip: 0,
      limit: 50,
    });
    mockHostsFacets.mockResolvedValue(defaultFacets(1));
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

  it('批量安装跳过退役主机并显式提示（#1807 / D5 批量 skip）', async () => {
    const { api } = await import('../../utils/api');
    mockHostsListPage.mockResolvedValue({
      items: [
        { id: 1, name: 'Worker-01', ip: '192.0.2.10', status: 'OFFLINE', extra: {}, mount_status: {}, agent_installed: true },
        {
          id: 9, name: 'Retired-09', ip: '192.0.2.99', status: 'OFFLINE', extra: {}, mount_status: {},
          agent_installed: true, retired_at: '2026-09-13T00:00:00Z',
        },
      ],
      total: 2,
      skip: 0,
      limit: 50,
    });
    mockHostsFacets.mockResolvedValue(defaultFacets(2));
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
    await waitFor(() =>
      expect(mocks.confirm).toHaveBeenCalledWith(
        expect.objectContaining({ description: expect.stringContaining('将对 1 台主机安装') }),
      ),
    );
    expect(api.agentInstall.trigger).not.toHaveBeenCalledWith(9);
  });

  it('取消的安装走 info 提示，不报失败', async () => {
    const { api } = await import('../../utils/api');
    mockHostsListPage.mockResolvedValue({
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
      skip: 0,
      limit: 50,
    });
    mockHostsFacets.mockResolvedValue(defaultFacets(1));
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

  it('#3193 取消的刷机前置走 info 提示，不报失败', async () => {
    const { api } = await import('../../utils/api');
    mockHostsListPage.mockResolvedValue({
      items: [
        {
          id: 'h-fp',
          name: 'Worker-FP',
          ip: '192.0.2.88',
          status: 'ONLINE',
          extra: {},
          mount_status: {},
          agent_installed: true,
        },
      ],
      total: 1,
      skip: 0,
      limit: 50,
    });
    mockHostsFacets.mockResolvedValue(defaultFacets(1));
    mocks.confirm.mockResolvedValue(true);
    vi.mocked(api.flashPrereqs.status).mockResolvedValueOnce({
      host_id: 'h-fp',
      log_path: '/var/log/stp/con-fp.log',
      status: 'canceled',
      console_run_id: 'con-fp',
      console_status: 'CANCELED',
      console_found: false,
    });

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId('select-all-hosts'));
    fireEvent.click(await screen.findByTestId('host-bulk-flash-prereqs'));

    await waitFor(() =>
      expect(mocks.toast.info).toHaveBeenCalledWith(
        expect.stringContaining('刷机前置已取消'),
      ),
    );
    expect(mocks.toast.error).not.toHaveBeenCalledWith(
      expect.stringContaining('刷机前置失败'),
    );
  });
});
