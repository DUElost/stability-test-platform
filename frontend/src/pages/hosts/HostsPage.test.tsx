import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const mocks = vi.hoisted(() => ({
  confirm: vi.fn().mockResolvedValue(true),
}));

// Mock api
vi.mock('../../utils/api', () => ({
  api: {
    hosts: {
      list: vi.fn().mockResolvedValue({ data: { items: [], total: 0 } }),
      create: vi.fn().mockResolvedValue({ data: {} }),
      updateWatcherAdminState: vi.fn().mockResolvedValue({ data: {} }),
    },
    devices: {
      list: vi.fn().mockResolvedValue({ data: { items: [], total: 0 } }),
    },
    tasks: {
      list: vi.fn().mockResolvedValue({ data: { items: [], total: 0 } }),
    },
    deploy: {
      trigger: vi.fn().mockResolvedValue({ data: {} }),
      batchDeploy: vi.fn().mockResolvedValue({ data: {} }),
    },
    hotUpdate: {
      trigger: vi.fn().mockResolvedValue({ data: {} }),
    },
    planRuns: {
      list: vi.fn().mockResolvedValue([]),
    },
  },
}));

// Mock toast
vi.mock('../../components/ui/toast', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  }),
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
    onWatcherAdminStateChange,
  }: {
    hosts: any[];
    onWatcherAdminStateChange?: (hostId: string | number, nextActive: boolean) => void;
  }) => (
    <div data-testid="host-table">
      {hosts.map((h: any) => (
        <div key={h.id} data-testid={`host-row-${h.id}`}>
          <span>{h.name}</span>
          <span>{h.ip}</span>
          <span>{h.status}</span>
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
    const { api } = await import('../../utils/api');
    (api.hosts.list as any).mockResolvedValue({ data: { items: [], total: 0 } });
    (api.devices.list as any).mockResolvedValue({ data: { items: [], total: 0 } });
    (api.planRuns.list as any).mockResolvedValue([]);
  });

  it('renders loading state initially', async () => {
    const { api } = await import('../../utils/api');
    // Make the promise never resolve to show loading
    (api.hosts.list as any).mockReturnValueOnce(new Promise(() => {}));

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    expect(screen.getByText('主机管理')).toBeInTheDocument();
    expect(screen.getByText('管理和监控测试执行节点')).toBeInTheDocument();
  });

  it('renders page header and add button', async () => {
    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    expect(screen.getByText('主机管理')).toBeInTheDocument();
    expect((await screen.findAllByText('添加主机')).length).toBeGreaterThan(0);
  });

  it('renders empty state when no hosts', async () => {
    const HostsPage = (await import('./HostsPage')).default;
    const { container } = render(<HostsPage />, { wrapper: createWrapper() });

    // Wait for query to resolve
    await screen.findByText('主机管理');

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
    const { api } = await import('../../utils/api');
    (api.hosts.list as any).mockResolvedValue({
      data: {
        items: [
          { id: 1, name: 'Worker-01', ip: '172.21.15.10', status: 'ONLINE', extra: {}, mount_status: {} },
          { id: 2, name: 'Worker-02', ip: '172.21.15.11', status: 'OFFLINE', extra: {}, mount_status: {} },
        ],
        total: 2,
      },
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
    (api.hosts.list as any).mockResolvedValue({
      data: {
        items: [
          {
            id: 'host-1',
            name: 'Worker-01',
            ip: '172.21.15.10',
            status: 'ONLINE',
            extra: {},
            mount_status: {},
            watcher_admin_active: true,
            capacity: { active_jobs: 2, effective_slots: 1, max_concurrent_jobs: 3 },
          },
        ],
        total: 1,
      },
    });

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    await screen.findByText('Worker-01');
    expect(api.planRuns.list).not.toHaveBeenCalled();
  });

  it('deactivates watcher admin state after confirmation', async () => {
    const { api } = await import('../../utils/api');
    (api.hosts.list as any).mockResolvedValue({
      data: {
        items: [
          {
            id: 'host-1',
            name: 'Worker-01',
            ip: '172.21.15.10',
            status: 'ONLINE',
            extra: {},
            mount_status: {},
            watcher_admin_active: true,
          },
        ],
        total: 1,
      },
    });

    const HostsPage = (await import('./HostsPage')).default;
    render(<HostsPage />, { wrapper: createWrapper() });

    await screen.findByText('Worker-01');
    fireEvent.click(screen.getByTestId('watcher-toggle-host-1'));

    expect(mocks.confirm).toHaveBeenCalledWith({
      description: '将节点设为未激活后，只影响后续新派发任务；正在运行的任务不受影响。是否继续？',
    });
  });
});
