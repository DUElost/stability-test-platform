import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { deviceKeys } from '@/utils/api/queryKeys';

const mockFetchAllDevicePages = vi.fn();
const mockFetchHostList = vi.fn();
const mockProjectsList = vi.fn();
const mockAssignDevicesToProject = vi.fn();
const mockCreateDevice = vi.fn();
const mockBulkSwipeTrail = vi.fn();
const mockRetireBatch = vi.fn();
const mockUnretireDevice = vi.fn();
const mockUseAuthSession = vi.fn(() => ({ data: { role: 'admin' } }));

vi.mock('@/utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return {
    ...actual,
    fetchHostList: (...args: unknown[]) => mockFetchHostList(...args),
    // #3152：hostMap 数据源改 fetchAllHosts——委托回同一 mock（无参调用=不含退役）
    fetchAllHosts: (includeRetired = false) => mockFetchHostList(0, 200, includeRetired),
    // #3131：页面按 total 翻页拉全量（不再单次 list(0,1200)），故数据源在这一层注入
    fetchAllDevicePages: (...args: unknown[]) => mockFetchAllDevicePages(...args),
    // ADR-0029：批量归入走独立导出（非 api 属性）
    assignDevicesToProject: (...args: unknown[]) => mockAssignDevicesToProject(...args),
    api: {
      ...actual.api,
      projects: {
        ...actual.api.projects,
        // #709 起选择器/批量归入弹窗走 listActive（归档默认过滤）；#1935：
        // 原 mock 只覆盖 list，弹窗实际查 listActive → 选项为空误红。
        list: (...args: unknown[]) => mockProjectsList(...args),
        listActive: (...args: unknown[]) => mockProjectsList(...args),
      },
      devices: {
        ...actual.api.devices,
        create: (...args: unknown[]) => mockCreateDevice(...args),
        bulkSwipeTrail: (...args: unknown[]) => mockBulkSwipeTrail(...args),
        retireBatch: (...args: unknown[]) => mockRetireBatch(...args),
        unretire: (...args: unknown[]) => mockUnretireDevice(...args),
      },
    },
  };
});

vi.mock('@/hooks/useToast', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
  }),
}));

vi.mock('@/hooks/useAuthSession', () => ({
  useAuthSession: () => mockUseAuthSession(),
}));

vi.mock('@/hooks/useFleetDeviceUpdates', () => ({
  useFleetDeviceUpdates: vi.fn(),
}));

vi.mock('./components/AddDeviceModal', () => ({
  // 打开时提供一个提交按钮，供 page 级用例驱动 create 接线（弹窗自身行为由
  // AddDeviceModal.test.tsx 覆盖）
  AddDeviceModal: ({ isOpen, onSubmit }: { isOpen?: boolean; onSubmit?: (d: { serial: string }) => void }) =>
    isOpen ? (
      <button type="button" onClick={() => onSubmit?.({ serial: 'NEW-SERIAL' })}>
        mock-添加提交
      </button>
    ) : null,
}));

vi.mock('./components/BatchEditDeviceTagsDialog', () => ({
  BatchEditDeviceTagsDialog: () => null,
}));

// RetireDevicesDialog 保持真实渲染（#3497 G6：页面级用例断言弹窗计数文案）
// DeviceBulkActionBar 与 AssignProjectDialog 保持真实渲染（归入流程端到端测试）

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
}

describe('DevicesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetchAllDevicePages.mockResolvedValue({
      items: [
        {
          id: 1,
          serial: 'TEST-SERIAL',
          model: 'TestModel',
          host_id: '198-51-100-123',
          status: 'ONLINE',
          tags: [],
          last_seen: '2026-08-05T18:00:00+08:00',
        },
      ],
      total: 1,
    });
    mockFetchHostList.mockResolvedValue([
      {
        id: '198-51-100-123',
        name: '198.51.100.123',
        ip: '198.51.100.123',
        ssh_user: 'android',
        status: 'ONLINE',
        extra: {},
        mount_status: {},
        last_heartbeat: null,
      },
    ]);
    mockProjectsList.mockResolvedValue([
      {
        project_key: 'proj-a',
        display_name: 'Project A',
        jira_project_key: null,
        product_line: null,
        customer: 'CustA',
        platform: 'MTK',
        form_factor: 'PHONE',
        status: 'ACTIVE',
        created_at: '2026-08-01T00:00:00Z',
        updated_at: '2026-08-01T00:00:00Z',
        device_count: 1,
        running_run_count: 0,
      },
    ]);
    mockAssignDevicesToProject.mockResolvedValue([]);
    mockBulkSwipeTrail.mockResolvedValue({
      enabled: true,
      ok: 1,
      failed: 0,
      skipped: 0,
      results: [],
    });
  });

  it('resolves host name when device host_id is a string', async () => {
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      const row = screen.getByText('TEST-SERIAL').closest('tr');
      expect(row).not.toBeNull();
      expect(within(row as HTMLElement).getByText('198.51.100.123')).toBeInTheDocument();
    });
  });

  // 设备存储指标：API 已产出 disk_total/disk_used（字节），页面须透传到表的「存储」列
  it('passes disk telemetry through to the storage column', async () => {
    mockFetchAllDevicePages.mockResolvedValue({
      items: [
        {
          id: 1,
          serial: 'TEST-SERIAL',
          model: 'TestModel',
          host_id: '198-51-100-123',
          status: 'ONLINE',
          tags: [],
          last_seen: '2026-08-05T18:00:00+08:00',
          disk_total: 128 * 1024 ** 3,
          disk_used: 100 * 1024 ** 3,
        },
      ],
      total: 1,
    });
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByRole('columnheader', { name: '存储' })).toBeInTheDocument();
      expect(screen.getByText('剩 28.0 GiB')).toBeInTheDocument();
    });
  });

  it('admin can bulk-assign selected devices to a project', async () => {
    const user = userEvent.setup();
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    // 选中设备 → 底部批量操作栏出现
    await waitFor(() => {
      expect(screen.getByText('TEST-SERIAL')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByLabelText('选择设备 TEST-SERIAL'));
    const assignButton = await screen.findByTestId('device-bulk-assign-project');
    expect(assignButton).toBeInTheDocument();

    // 打开归入对话框 → 选择项目 → 确认
    await user.click(assignButton);
    expect(await screen.findByTestId('assign-project-select')).toBeInTheDocument();
    expect(screen.getByTestId('assign-model-scope')).toHaveTextContent('TestModel');
    await user.selectOptions(screen.getByTestId('assign-project-select'), 'proj-a');
    await user.click(screen.getByTestId('assign-project-confirm'));

    await waitFor(() => {
      expect(mockAssignDevicesToProject).toHaveBeenCalledWith('proj-a', [1]);
    });
  });

  it('non-admin does not see the bulk-assign entry', async () => {
    mockUseAuthSession.mockReturnValue({ data: { role: 'user' } });
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('TEST-SERIAL')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByLabelText('选择设备 TEST-SERIAL'));

    await waitFor(() => {
      expect(screen.getByTestId('device-bulk-action-bar')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('device-bulk-assign-project')).not.toBeInTheDocument();
    // 批量标签入口同样不显示（非 admin）
    expect(screen.queryByTestId('device-bulk-tags')).not.toBeInTheDocument();
    expect(screen.queryByTestId('device-bulk-swipe-trail-on')).not.toBeInTheDocument();
    expect(screen.queryByTestId('device-bulk-swipe-trail-off')).not.toBeInTheDocument();
  });

  it('admin can bulk-enable swipe trail on selected devices', async () => {
    mockUseAuthSession.mockReturnValue({ data: { role: 'admin' } });
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('TEST-SERIAL')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByLabelText('选择设备 TEST-SERIAL'));
    fireEvent.click(await screen.findByTestId('device-bulk-swipe-trail-on'));

    await waitFor(() => {
      expect(mockBulkSwipeTrail).toHaveBeenCalledWith([1], true);
    });
  });

  it('#823：添加设备后失效覆盖任意筛选态的设备列表', async () => {
    mockUseAuthSession.mockReturnValue({ data: { role: 'admin' } });
    mockCreateDevice.mockResolvedValue({ id: 99, serial: 'NEW-SERIAL' });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // 预置一个「项目筛选态」列表查询（页面当前未在看它）：旧实现用 list() 无参键失效，
    // 对象深比较不匹配 → 永远失效不到它。
    queryClient.setQueryData(deviceKeys.list('PROJ-X', true), { items: [], total: 0 });

    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, {
      wrapper: ({ children }: { children: React.ReactNode }) => (
        <MemoryRouter>
          <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
        </MemoryRouter>
      ),
    });

    await waitFor(() => expect(screen.getByText('TEST-SERIAL')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /添加设备/ }));
    fireEvent.click(screen.getByRole('button', { name: /mock-添加提交/ }));

    await waitFor(() => expect(mockCreateDevice).toHaveBeenCalled());
    await waitFor(() =>
      expect(queryClient.getQueryState(deviceKeys.list('PROJ-X', true))?.isInvalidated).toBe(true),
    );
  });

  it('#2068：添加设备后全量设备缓存（deviceKeys.all()）一并失效', async () => {
    mockUseAuthSession.mockReturnValue({ data: { role: 'admin' } });
    mockCreateDevice.mockResolvedValue({ id: 99, serial: 'NEW-SERIAL' });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // 计划执行设备矩阵 / 排程选择器消费的全量键：修复前它挂在 ['devices-all']，
    // 与写后失效用的 ['devices'] 前缀互不覆盖 → 永远不失效（注释却声称覆盖全量）。
    queryClient.setQueryData(deviceKeys.all(), { items: [], total: 0 });

    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, {
      wrapper: ({ children }: { children: React.ReactNode }) => (
        <MemoryRouter>
          <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
        </MemoryRouter>
      ),
    });

    await waitFor(() => expect(screen.getByText('TEST-SERIAL')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /添加设备/ }));
    fireEvent.click(screen.getByRole('button', { name: /mock-添加提交/ }));

    await waitFor(() => expect(mockCreateDevice).toHaveBeenCalled());
    await waitFor(() =>
      expect(queryClient.getQueryState(deviceKeys.all())?.isInvalidated).toBe(true),
    );
  });

  it('#2068：归入项目后全量设备缓存（deviceKeys.all()）一并失效', async () => {
    const user = userEvent.setup();
    mockUseAuthSession.mockReturnValue({ data: { role: 'admin' } });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(deviceKeys.all(), { items: [], total: 0 });

    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, {
      wrapper: ({ children }: { children: React.ReactNode }) => (
        <MemoryRouter>
          <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
        </MemoryRouter>
      ),
    });

    await waitFor(() => expect(screen.getByText('TEST-SERIAL')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('选择设备 TEST-SERIAL'));
    await user.click(await screen.findByTestId('device-bulk-assign-project'));
    await user.selectOptions(await screen.findByTestId('assign-project-select'), 'proj-a');
    await user.click(screen.getByTestId('assign-project-confirm'));

    await waitFor(() => expect(mockAssignDevicesToProject).toHaveBeenCalled());
    await waitFor(() =>
      expect(queryClient.getQueryState(deviceKeys.all())?.isInvalidated).toBe(true),
    );
  });

  it('#2962：陈旧/退役开关驱动服务端过滤参数', async () => {
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('TEST-SERIAL')).toBeInTheDocument());
    // 默认：两个开关都不带（服务端默认隐藏陈旧与退役）
    expect(mockFetchAllDevicePages).toHaveBeenLastCalledWith(
      expect.objectContaining({ includeStale: false, includeRetired: false }),
    );

    fireEvent.click(screen.getByTestId('device-toggle-stale'));
    await waitFor(() =>
      expect(mockFetchAllDevicePages).toHaveBeenLastCalledWith(
        expect.objectContaining({ includeStale: true, includeRetired: false }),
      ),
    );

    // 键变化触发重新拉取：loading 期间工具栏不在 DOM，等它回来再点第二个开关
    fireEvent.click(await screen.findByTestId('device-toggle-retired'));
    await waitFor(() =>
      expect(mockFetchAllDevicePages).toHaveBeenLastCalledWith(
        expect.objectContaining({ includeStale: true, includeRetired: true }),
      ),
    );
  });

  it('#2962：陈旧/退役/退役建议徽标按行渲染', async () => {
    mockFetchAllDevicePages.mockResolvedValue({
      items: [
        { id: 11, serial: 'STALE-DEV', model: 'M', host_id: '198-51-100-123',
          status: 'OFFLINE', tags: [], last_seen: '2026-09-01T00:00:00Z', is_stale: true },
        { id: 12, serial: 'RETIRED-DEV', model: 'M', host_id: '198-51-100-123',
          status: 'ONLINE', tags: [], last_seen: '2026-09-26T00:00:00Z',
          retired_at: '2026-09-26T08:00:00Z', retired_by: 'admin', retire_reason: '报废' },
        { id: 13, serial: 'OLD-DEV', model: 'M', host_id: '198-51-100-123',
          status: 'OFFLINE', tags: [], last_seen: '2026-08-01T00:00:00Z',
          is_stale: true, retire_suggested: true },
      ],
      total: 3,
    });
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    expect(await screen.findByTestId('device-stale-badge-11')).toBeInTheDocument();
    expect(screen.getByTestId('device-retired-badge-12')).toBeInTheDocument();
    expect(screen.getByTestId('device-retire-suggested-13')).toBeInTheDocument();
    // 已退役行不再重复陈旧徽标（退役是更强的终态）
    expect(screen.queryByTestId('device-stale-badge-12')).not.toBeInTheDocument();
  });

  it('#2962：admin 可走批量入口退役设备（逐台结果回执）', async () => {
    const user = userEvent.setup();
    mockUseAuthSession.mockReturnValue({ data: { role: 'admin' } });
    mockRetireBatch.mockResolvedValue({
      results: [{ device_id: 1, serial: 'TEST-SERIAL', status: 'retired' }],
      retired: 1, already_retired: 0, conflict: 0, not_found: 0, failed: 0,
    });
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('TEST-SERIAL')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('选择设备 TEST-SERIAL'));
    await user.click(await screen.findByTestId('device-bulk-retire'));
    await user.type(await screen.findByLabelText('退役原因'), '库存报废');
    await user.click(screen.getByTestId('device-retire-submit'));

    await waitFor(() =>
      expect(mockRetireBatch).toHaveBeenCalledWith([1], '库存报废'),
    );
  });

  // #3483：unretire 的幂等跳过数是「选中里未退役的台」——修复前无条件传已退役数，
  // 全选已退役（解除退役的正常形态）会被误报「全部 N 台将跳过」。
  it('#3483：解除退役全选已退役，不出现「并未退役（幂等跳过）」', async () => {
    const user = userEvent.setup();
    mockUseAuthSession.mockReturnValue({ data: { role: 'admin' } });
    mockFetchAllDevicePages.mockResolvedValue({
      items: [
        { id: 21, serial: 'RET-A', model: 'M', host_id: '198-51-100-123', status: 'ONLINE',
          tags: [], last_seen: '2026-09-26T00:00:00Z', retired_at: '2026-09-26T08:00:00Z' },
        { id: 22, serial: 'RET-B', model: 'M', host_id: '198-51-100-123', status: 'ONLINE',
          tags: [], last_seen: '2026-09-26T00:00:00Z', retired_at: '2026-09-26T09:00:00Z' },
      ],
      total: 2,
    });
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('RET-A')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('选择设备 RET-A'));
    fireEvent.click(screen.getByLabelText('选择设备 RET-B'));
    await user.click(await screen.findByTestId('device-bulk-unretire'));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('将对选中的 2 台设备解除退役');
    expect(dialog).not.toHaveTextContent('并未退役（幂等跳过）');
  });

  it('#3483：解除退役混入在役设备时提示实际幂等跳过台数', async () => {
    const user = userEvent.setup();
    mockUseAuthSession.mockReturnValue({ data: { role: 'admin' } });
    mockFetchAllDevicePages.mockResolvedValue({
      items: [
        { id: 21, serial: 'RET-A', model: 'M', host_id: '198-51-100-123', status: 'ONLINE',
          tags: [], last_seen: '2026-09-26T00:00:00Z', retired_at: '2026-09-26T08:00:00Z' },
        { id: 22, serial: 'RET-B', model: 'M', host_id: '198-51-100-123', status: 'ONLINE',
          tags: [], last_seen: '2026-09-26T00:00:00Z', retired_at: '2026-09-26T09:00:00Z' },
        { id: 23, serial: 'ACTIVE-C', model: 'M', host_id: '198-51-100-123', status: 'ONLINE',
          tags: [], last_seen: '2026-09-26T00:00:00Z' },
      ],
      total: 3,
    });
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('ACTIVE-C')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('选择设备 RET-A'));
    fireEvent.click(screen.getByLabelText('选择设备 RET-B'));
    fireEvent.click(screen.getByLabelText('选择设备 ACTIVE-C'));
    await user.click(await screen.findByTestId('device-bulk-unretire'));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('其中 1 台并未退役（幂等跳过）。');
  });

  it('#3483：退役模式读数不变（提示已退役台数）', async () => {
    const user = userEvent.setup();
    mockUseAuthSession.mockReturnValue({ data: { role: 'admin' } });
    mockFetchAllDevicePages.mockResolvedValue({
      items: [
        { id: 21, serial: 'RET-A', model: 'M', host_id: '198-51-100-123', status: 'ONLINE',
          tags: [], last_seen: '2026-09-26T00:00:00Z', retired_at: '2026-09-26T08:00:00Z' },
        { id: 22, serial: 'RET-B', model: 'M', host_id: '198-51-100-123', status: 'ONLINE',
          tags: [], last_seen: '2026-09-26T00:00:00Z', retired_at: '2026-09-26T09:00:00Z' },
        { id: 23, serial: 'ACTIVE-C', model: 'M', host_id: '198-51-100-123', status: 'ONLINE',
          tags: [], last_seen: '2026-09-26T00:00:00Z' },
      ],
      total: 3,
    });
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('ACTIVE-C')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('选择设备 RET-A'));
    fireEvent.click(screen.getByLabelText('选择设备 RET-B'));
    fireEvent.click(screen.getByLabelText('选择设备 ACTIVE-C'));
    await user.click(await screen.findByTestId('device-bulk-retire'));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('其中 2 台已是退役态（幂等跳过）。');
  });

  it('#2962：非 admin 看不到退役入口', async () => {
    mockUseAuthSession.mockReturnValue({ data: { role: 'user' } });
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('TEST-SERIAL')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('选择设备 TEST-SERIAL'));
    await waitFor(() =>
      expect(screen.getByTestId('device-bulk-action-bar')).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('device-bulk-retire')).not.toBeInTheDocument();
    expect(screen.queryByTestId('device-bulk-unretire')).not.toBeInTheDocument();
  });
});
