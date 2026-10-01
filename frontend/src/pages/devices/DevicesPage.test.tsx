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

// ── #3237 / 批次 B3 G1（S1 / S4）：设备 CSV 与 clipboard 的 spreadsheet 安全语义 ──
// S1 的外部可控面：serial / model / build_display_id 来自 ADB、tags 来自管理员/API、
// host.name 来自管理员；S4 的 serial 粘贴进 spreadsheet 必须保持一条 serial = 一个 cell。

/**
 * 勾选当前筛选结果里的全部设备。
 *
 * 不用 `device-select-all-filtered` 入口：它在「已全选」时按设计隐藏（canSelectAllFiltered），
 * 单设备 fixture 下永远点不到，直接逐行勾更稳。
 */
async function selectAllDevices() {
  await waitFor(() => expect(screen.getAllByLabelText(/^选择设备 /)[0]).toBeInTheDocument());
  for (const checkbox of screen.getAllByLabelText(/^选择设备 /)) {
    fireEvent.click(checkbox);
  }
  await waitFor(() => expect(screen.getByTestId('device-bulk-action-bar')).toBeInTheDocument());
}

/** 拦住 blob URL，拿到导出 CSV 的 raw 文本（未剥 BOM 前）。 */
function stubBlobUrl() {
  const captured: Blob[] = [];
  const originalCreate = Object.getOwnPropertyDescriptor(URL, 'createObjectURL');
  const originalRevoke = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL');
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true, writable: true,
    value: (blob: Blob) => { captured.push(blob); return `blob:mock-${captured.length}`; },
  });
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true, writable: true, value: vi.fn(),
  });
  return {
    captured,
    restore: () => {
      for (const [name, desc] of [
        ['createObjectURL', originalCreate] as const,
        ['revokeObjectURL', originalRevoke] as const,
      ]) {
        if (desc) Object.defineProperty(URL, name, desc);
        else delete (URL as unknown as Record<string, unknown>)[name];
      }
    },
  };
}

async function exportCsvText(): Promise<string> {
  const stub = stubBlobUrl();
  try {
    const user = userEvent.setup();
    mockUseAuthSession.mockReturnValue({ data: { role: 'admin' } });
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });
    await selectAllDevices();
    await user.click(await screen.findByText('导出 CSV'));
    expect(stub.captured).toHaveLength(1);
    return await stub.captured[0].text();
  } finally {
    stub.restore();
  }
}

/** 跑一次复制 serials，返回实际写进 clipboard 的文本（writeText 与 fallback 两条路径都收）。 */
async function copySerialsText(): Promise<string> {
  const writes: string[] = [];
  const execCalls: string[] = [];
  const writeText = vi.fn(async (text: string) => { writes.push(text); });
  const originalExec = document.execCommand?.bind(document);
  Object.defineProperty(document, 'execCommand', {
    configurable: true, writable: true,
    value: vi.fn((cmd: string) => {
      execCalls.push(cmd);
      const ta = document.querySelector('textarea');
      if (ta) writes.push(ta.value);
      return true;
    }),
  });
  try {
    const user = userEvent.setup();
    // 必须在 setup() 之后装桩：userEvent.setup() 会用自带的 clipboard stub 覆盖
    // navigator.clipboard，先装桩会被它无声顶掉（本单第一版就踩了这个静默失真）。
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true, writable: true, value: { writeText },
    });
    mockUseAuthSession.mockReturnValue({ data: { role: 'admin' } });
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });
    await selectAllDevices();
    await user.click(await screen.findByText('复制序列号'));
    // writeText 优先；缺失时才走 textarea + execCommand fallback
    expect(writes.length + execCalls.length).toBeGreaterThan(0);
    return writes[0] ?? '';
  } finally {
    if (originalExec) Object.defineProperty(document, 'execCommand', { configurable: true, writable: true, value: originalExec });
    else delete (document as unknown as Record<string, unknown>).execCommand;
  }
}

function dangerousDevice(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    serial: '=1+1',
    model: '@SUM(1,1)',
    host_id: '198-51-100-123',
    status: 'ONLINE',
    tags: ['=cmd', 'x'],
    build_display_id: '-V1',
    last_seen: '2026-08-05T18:00:00+08:00',
    ...overrides,
  };
}

describe('DevicesPage — CSV 公式前缀中和（S1 / #3237）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetchHostList.mockResolvedValue([]);
    mockProjectsList.mockResolvedValue([]);
  });

  it('危险 serial / model / build / tags 在 raw CSV 里带可见 apostrophe', async () => {
    mockFetchAllDevicePages.mockResolvedValue({ items: [dangerousDevice()], total: 1 });
    const csv = await exportCsvText();
    expect(csv).toContain("'=1+1"); // serial
    expect(csv).toContain("'@SUM(1,1)"); // model
    expect(csv).toContain("'-V1"); // build_display_id
    expect(csv).toContain("'=cmd|x"); // tags 整格 join 后判定一次
  });

  it('TAB / NUL / 全角触发前缀也中和', async () => {
    mockFetchAllDevicePages.mockResolvedValue({
      items: [
        dangerousDevice({ serial: '\t=1+1', model: '\0=1+1' }),
        dangerousDevice({ id: 2, serial: '＝1+1', model: '＋x' }),
      ],
      total: 2,
    });
    const csv = await exportCsvText();
    expect(csv).toContain("'\t=1+1");
    expect(csv).toContain("'\0=1+1");
    expect(csv).toContain("'＝1+1");
    expect(csv).toContain("'＋x");
  });

  it('正常设备与 number ID 不误伤：中间位置的 - / + / @ 保持原样', async () => {
    mockFetchAllDevicePages.mockResolvedValue({
      items: [
        { id: 7, serial: 'ABC-123', model: 'A+B', host_id: '198-51-100-123',
          status: 'ONLINE', tags: ['smoke', 'regression'],
          build_display_id: 'V1', last_seen: '2026-08-05T18:00:00+08:00' },
      ],
      total: 1,
    });
    const csv = await exportCsvText();
    expect(csv).toContain('ABC-123');
    expect(csv).toContain('A+B');
    expect(csv).toContain('smoke|regression');
    // number ID 保持数值语义，且不得给普通标签格加 apostrophe
    expect(csv).not.toContain("'ABC-123");
    expect(csv).not.toContain("'A+B");
    expect(csv).not.toContain("'smoke|regression");
    // number ID 保持数值语义：先 String() 再中和的实现会产出 "'7"
    expect(csv).toContain('"7","ABC-123"');
    expect(csv).not.toContain("'7");
  });

  it('number ID 保持数值语义：负数 id 不得被加 apostrophe 变成文本', async () => {
    // §4.3 R4 的判别点：先 String() 再中和的错误实现会把 number -5 变成文本 '-5。
    // 只用正数 id 抓不住（正数首字符不在触发集里），必须有一个负数。
    mockFetchAllDevicePages.mockResolvedValue({
      items: [
        { id: -5, serial: 'NEG-ID', model: 'M', host_id: null, status: 'ONLINE',
          tags: [], build_display_id: null, last_seen: '2026-08-05T18:00:00+08:00' },
      ],
      total: 1,
    });
    const csv = await exportCsvText();
    expect(csv).toContain('"-5","NEG-ID"');
    expect(csv).not.toContain("'-5");
  });

  it('CSV 原有逗号 / 双引号 / CR/LF framing 回归不因中和而回退', async () => {
    mockFetchAllDevicePages.mockResolvedValue({
      items: [
        { id: 1, serial: 'a,b', model: 'say "hi"', host_id: null, status: 'ONLINE',
          tags: [], build_display_id: null, last_seen: '2026-08-05T18:00:00+08:00' },
      ],
      total: 1,
    });
    const csv = await exportCsvText();
    expect(csv).toContain('"a,b"');
    expect(csv).toContain('"say ""hi"""');
    // 表头与列数不变：8 列
    expect(csv).toContain('"ID","Serial","Model","Status","Host","Build","Tags","Last Seen"');
  });
});

describe('DevicesPage — clipboard 结构与公式中和（S4 / #3237）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetchHostList.mockResolvedValue([]);
    mockProjectsList.mockResolvedValue([]);
  });

  it('公式前缀被中和', async () => {
    mockFetchAllDevicePages.mockResolvedValue({
      items: [dangerousDevice({ id: 1, serial: '=1+1' }), dangerousDevice({ id: 2, serial: '@x' })],
      total: 2,
    });
    expect(await copySerialsText()).toBe("'=1+1\n'@x");
  });

  it('行首双引号被中和，不把下一条记录吞进同一 cell', async () => {
    mockFetchAllDevicePages.mockResolvedValue({
      items: [{ ...dangerousDevice({ id: 1, serial: '"=1+1' }) }, { ...dangerousDevice({ id: 2, serial: 'B' }) }],
      total: 2,
    });
    const text = await copySerialsText();
    expect(text).toBe('\'"=1+1\nB');
    expect(text.split('\n')).toHaveLength(2);
  });

  it('内嵌 TAB / CR / LF / NUL 不制造额外 clipboard cell/row', async () => {
    mockFetchAllDevicePages.mockResolvedValue({
      items: [
        { ...dangerousDevice({ id: 1, serial: 'a\tb' }) },
        { ...dangerousDevice({ id: 2, serial: 'c\nd' }) },
      ],
      total: 2,
    });
    const text = await copySerialsText();
    expect(text).toBe('a\\tb\nc\\nd');
    expect(text.split('\n')).toHaveLength(2);
  });

  it('普通 serial 原样复制', async () => {
    mockFetchAllDevicePages.mockResolvedValue({
      items: [
        { ...dangerousDevice({ id: 1, serial: 'ABC-123' }) },
        { ...dangerousDevice({ id: 2, serial: 'foo@bar' }) },
      ],
      total: 2,
    });
    expect(await copySerialsText()).toBe('ABC-123\nfoo@bar');
  });
});
