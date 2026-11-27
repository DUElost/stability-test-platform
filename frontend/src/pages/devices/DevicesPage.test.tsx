import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { deviceKeys } from '@/utils/api/queryKeys';
import type {
  DeviceFacets,
  DeviceFilterQueryParams,
  DeviceListItem,
  DeviceListPageQueryParams,
} from '@/utils/api/types';

const mockListPage = vi.fn();
const mockFacets = vi.fn();
const mockGetDevice = vi.fn();
const mockSelectionPreview = vi.fn();
const mockFetchAllDevicePages = vi.fn();
const mockFetchHostList = vi.fn();
const mockProjectsList = vi.fn();
const mockAssignDevicesToProject = vi.fn();
const mockCreateDevice = vi.fn();
const mockBulkSwipeTrail = vi.fn();
const mockBulkSkipOobe = vi.fn();
const mockRetireBatch = vi.fn();
const mockUnretireDevice = vi.fn();
const mockReturnDebts = vi.fn();
const mockReturnOverride = vi.fn();
const mockUseAuthSession = vi.fn(() => ({ data: { role: 'admin' } }));

vi.mock('@/utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return {
    ...actual,
    fetchHostList: (...args: unknown[]) => mockFetchHostList(...args),
    fetchAllHosts: (includeRetired = false) => mockFetchHostList(0, 200, includeRetired),
    fetchAllDevicePages: (...args: unknown[]) => mockFetchAllDevicePages(...args),
    assignDevicesToProject: (...args: unknown[]) => mockAssignDevicesToProject(...args),
    api: {
      ...actual.api,
      projects: {
        ...actual.api.projects,
        list: (...args: unknown[]) => mockProjectsList(...args),
        listActive: (...args: unknown[]) => mockProjectsList(...args),
      },
      devices: {
        ...actual.api.devices,
        listPage: (...args: unknown[]) => mockListPage(...args),
        facets: (...args: unknown[]) => mockFacets(...args),
        get: (...args: unknown[]) => mockGetDevice(...args),
        selectionPreview: (...args: unknown[]) => mockSelectionPreview(...args),
        create: (...args: unknown[]) => mockCreateDevice(...args),
        bulkSwipeTrail: (...args: unknown[]) => mockBulkSwipeTrail(...args),
        bulkSkipOobe: (...args: unknown[]) => mockBulkSkipOobe(...args),
        retireBatch: (...args: unknown[]) => mockRetireBatch(...args),
        unretire: (...args: unknown[]) => mockUnretireDevice(...args),
        returnDebts: (...args: unknown[]) => mockReturnDebts(...args),
        returnOverride: (...args: unknown[]) => mockReturnOverride(...args),
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

function buildMockFacets(
  items: DeviceListItem[],
  params: DeviceFilterQueryParams = {},
): DeviceFacets {
  const statusCounts = {
    all: items.length,
    ONLINE: items.filter((d) => d.status === 'ONLINE').length,
    BUSY: items.filter((d) => d.status === 'BUSY').length,
    OFFLINE: items.filter((d) => d.status === 'OFFLINE').length,
    ERROR: items.filter((d) => d.status === 'ERROR').length,
    need_attention: items.filter((d) => d.status === 'OFFLINE' || d.status === 'ERROR').length,
    tagged: items.filter((d) => (d.tags?.length ?? 0) > 0).length,
  };
  const scopeH = items.filter((d) => {
    if (params.status === 'need_attention') {
      if (d.status !== 'OFFLINE' && d.status !== 'ERROR') return false;
    } else if (params.status && d.status !== params.status) {
      return false;
    }
    if (params.has_tags && !(d.tags?.length)) return false;
    return true;
  });
  const hostMap = new Map<string, string>();
  for (const d of scopeH) {
    const key = d.host_id != null ? String(d.host_id) : 'unassigned';
    if (!hostMap.has(key)) hostMap.set(key, d.host_name || (key === 'unassigned' ? '未分配' : key));
  }
  const effectiveHost = params.host_id && hostMap.has(params.host_id) ? params.host_id : undefined;
  const scopeD = effectiveHost
    ? scopeH.filter((d) => (d.host_id != null ? String(d.host_id) : 'unassigned') === effectiveHost)
    : scopeH;
  return {
    status_counts: statusCounts,
    options: {
      hosts: Array.from(hostMap.entries()).map(([value, label]) => ({ value, label })),
      models: Array.from(new Set(scopeD.map((d) => d.model || '未知设备'))).sort(),
      products: Array.from(
        new Set(scopeD.map((d) => d.product).filter((v): v is string => Boolean(v))),
      ).sort(),
      versions: Array.from(
        new Set(scopeD.map((d) => d.build_display_id).filter((v): v is string => Boolean(v))),
      ).sort(),
    },
  };
}

function setServerDevices(items: DeviceListItem[], totalOverride?: number) {
  mockListPage.mockImplementation(async (params: DeviceListPageQueryParams = {}) => {
    let filtered = [...items];
    if (params.status === 'need_attention') {
      filtered = filtered.filter((d) => d.status === 'OFFLINE' || d.status === 'ERROR');
    } else if (params.status) {
      filtered = filtered.filter((d) => d.status === params.status);
    }
    if (params.has_tags) {
      filtered = filtered.filter((d) => (d.tags?.length ?? 0) > 0);
    }
    if (params.host_id) {
      filtered = filtered.filter(
        (d) => (d.host_id != null ? String(d.host_id) : 'unassigned') === params.host_id,
      );
    }
    if (params.model) {
      filtered = filtered.filter((d) => (d.model || '未知设备') === params.model);
    }
    if (params.product) {
      filtered = filtered.filter((d) => d.product === params.product);
    }
    if (params.version) {
      filtered = filtered.filter((d) => d.build_display_id === params.version);
    }
    if (params.q) {
      const q = params.q.toLowerCase();
      filtered = filtered.filter((d) => d.serial.toLowerCase().includes(q));
    }
    const skip = params.skip ?? 0;
    const limit = params.limit ?? 50;
    return {
      items: filtered.slice(skip, skip + limit),
      total: totalOverride ?? filtered.length,
      skip,
      limit,
    };
  });
  mockFacets.mockImplementation(async (params: DeviceFilterQueryParams = {}) =>
    buildMockFacets(items, params),
  );
}

function createWrapper(initialEntries?: string[]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <MemoryRouter initialEntries={initialEntries}>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
}

describe('DevicesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseAuthSession.mockReturnValue({ data: { role: 'admin' } });
    setServerDevices([
      {
        id: 1,
        serial: 'TEST-SERIAL',
        model: 'TestModel',
        host_id: '198-51-100-123',
        host_name: '198.51.100.123',
        status: 'ONLINE',
        tags: [],
        last_seen: '2026-08-05T18:00:00+08:00',
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
    mockBulkSkipOobe.mockResolvedValue({
      script_name: 'oobe_skip',
      script_version: '1.0.0',
      ok: 1,
      failed: 0,
      skipped: 0,
      results: [],
    });
    mockReturnDebts.mockResolvedValue([]);
    mockReturnOverride.mockResolvedValue({
      device_id: 1,
      families: ['power-cycle'],
      closed_count: 1,
      debt_lease_released: true,
      open_debt_count: 0,
    });
  });

  it('uses server-side single page (projection=list) and independent facets without full fleet fetch', async () => {
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      const row = screen.getByText('TEST-SERIAL').closest('tr');
      expect(row).not.toBeNull();
      expect(within(row as HTMLElement).getByText('198.51.100.123')).toBeInTheDocument();
    });

    expect(mockListPage).toHaveBeenCalledWith(
      expect.objectContaining({ skip: 0, limit: 50, projection: 'list' }),
    );
    expect(mockFacets).toHaveBeenCalledTimes(1);
    expect(mockFetchAllDevicePages).not.toHaveBeenCalled();
    expect(mockFetchHostList).not.toHaveBeenCalled();
  });

  it('#496 B3-1：翻页、切换页容量与列排序不重复请求 Facets，且下线遥测列排序入口', async () => {
    const many: DeviceListItem[] = Array.from({ length: 65 }, (_, i) => ({
      id: i + 1,
      serial: `DEV-${String(i + 1).padStart(3, '0')}`,
      model: 'M1',
      host_id: 'h1',
      host_name: 'Host-1',
      status: 'ONLINE',
      tags: [],
      last_seen: '2026-08-05T18:00:00+08:00',
    }));
    setServerDevices(many);
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('DEV-001')).toBeInTheDocument());
    expect(mockFacets).toHaveBeenCalledTimes(1);

    // 服务端分页首版仅保留「设备 (serial)」排序按钮，遥测列与主机名列无排序按钮
    expect(within(screen.getByRole('columnheader', { name: '设备' })).getByRole('button')).toBeInTheDocument();
    expect(within(screen.getByRole('columnheader', { name: '状态' })).queryByRole('button')).toBeNull();
    expect(within(screen.getByRole('columnheader', { name: '电量' })).queryByRole('button')).toBeNull();
    expect(within(screen.getByRole('columnheader', { name: '温度' })).queryByRole('button')).toBeNull();
    expect(within(screen.getByRole('columnheader', { name: '所属主机' })).queryByRole('button')).toBeNull();
    expect(within(screen.getByRole('columnheader', { name: '最后活跃' })).queryByRole('button')).toBeNull();

    // 翻页 → 触发 listPage(skip=50)，绝不重复触发 facets
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    await waitFor(() =>
      expect(mockListPage).toHaveBeenLastCalledWith(
        expect.objectContaining({ skip: 50, limit: 50, projection: 'list' }),
      ),
    );
    expect(mockFacets).toHaveBeenCalledTimes(1);

    // 切换 serial 排序 → 触发 listPage(sort_by='serial', sort_dir='asc')，绝不重复触发 facets
    fireEvent.click(within(screen.getByRole('columnheader', { name: '设备' })).getByRole('button'));
    await waitFor(() =>
      expect(mockListPage).toHaveBeenLastCalledWith(
        expect.objectContaining({ sort_by: 'serial', sort_dir: 'asc', skip: 0 }),
      ),
    );
    expect(mockFacets).toHaveBeenCalledTimes(1);

    // 切换页容量 100 → 触发 listPage(limit=100)
    fireEvent.click(screen.getByTestId('device-page-size-100'));
    await waitFor(() =>
      expect(mockListPage).toHaveBeenLastCalledWith(
        expect.objectContaining({ skip: 0, limit: 100, projection: 'list' }),
      ),
    );
    expect(mockFacets).toHaveBeenCalledTimes(1);
  });

  it('#496 B3-1：切换状态后互斥造数的型号/版本选项按服务端 Facets 收口且往返可切回', async () => {
    setServerDevices([
      {
        id: 1,
        serial: 'IDLE-DEV',
        model: 'MODEL-IDLE-ONLY',
        product: 'PROD-IDLE',
        build_display_id: 'VER-IDLE-ONLY',
        host_id: 'h-idle',
        host_name: 'Host-Idle',
        status: 'ONLINE',
        tags: [],
        last_seen: '2026-08-05T18:00:00+08:00',
      },
      {
        id: 2,
        serial: 'OFFLINE-DEV',
        model: 'MODEL-OFFLINE-ONLY',
        product: 'PROD-OFFLINE',
        build_display_id: 'VER-OFFLINE-ONLY',
        host_id: 'h-off',
        host_name: 'Host-Offline',
        status: 'OFFLINE',
        tags: [],
        last_seen: '2026-08-05T18:00:00+08:00',
      },
    ]);
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('IDLE-DEV')).toBeInTheDocument());

    // 切换到「空闲」→ facets 传 status='ONLINE'，下拉仅包含 MODEL-IDLE-ONLY / VER-IDLE-ONLY
    fireEvent.click(screen.getByRole('button', { name: /空闲/ }));
    await waitFor(() => {
      expect(mockFacets).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'ONLINE' }));
      expect(screen.queryByText('OFFLINE-DEV')).not.toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('combobox', { name: '按型号筛选' }));
    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'MODEL-IDLE-ONLY' })).toBeInTheDocument();
      expect(screen.queryByRole('option', { name: 'MODEL-OFFLINE-ONLY' })).not.toBeInTheDocument();
    });
    fireEvent.keyDown(document, { key: 'Escape' });

    fireEvent.click(screen.getByRole('combobox', { name: '按版本筛选' }));
    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'VER-IDLE-ONLY' })).toBeInTheDocument();
      expect(screen.queryByRole('option', { name: 'VER-OFFLINE-ONLY' })).not.toBeInTheDocument();
    });
    fireEvent.keyDown(document, { key: 'Escape' });

    // 往返：全部 → 需关注 → 全部
    fireEvent.click(screen.getByRole('button', { name: /需关注/ }));
    await waitFor(() =>
      expect(mockListPage).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: 'need_attention' }),
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: /全部设备/ }));
    await waitFor(() => {
      expect(screen.getByText('IDLE-DEV')).toBeInTheDocument();
      expect(screen.getByText('OFFLINE-DEV')).toBeInTheDocument();
    });
  });

  it('#496 B3-1：跨页预览冻结匹配 ID 并驱动归入项目，超限时禁用写操作保留导出', async () => {
    const user = userEvent.setup();
    const page1: DeviceListItem[] = Array.from({ length: 50 }, (_, i) => ({
      id: i + 1,
      serial: `SN-${i + 1}`,
      model: 'M-PAGE1',
      host_id: 'h1',
      host_name: 'Host-1',
      status: 'ONLINE',
      tags: [],
      last_seen: '2026-08-05T18:00:00+08:00',
    }));
    setServerDevices(page1, 120);
    mockSelectionPreview.mockResolvedValueOnce({
      action: 'assign_project',
      total_matched: 120,
      max_allowed: 500,
      exceeds_limit: false,
      device_ids: [1, 2, 101, 102],
      status_counts: { all: 120, ONLINE: 120, BUSY: 0, OFFLINE: 0, ERROR: 0, need_attention: 0, tagged: 0 },
      affected_models: ['M-PAGE1', 'M-PAGE2'],
      devices_without_model_serials: [],
      retired_count: 0,
      active_count: 120,
      stale_count: 0,
    });

    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });
    await waitFor(() => expect(screen.getByText('SN-1')).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText('选择设备 SN-1'));
    const selectAllBtn = await screen.findByTestId('device-select-all-filtered');
    expect(selectAllBtn).toHaveTextContent('选择全部筛选结果 (120)');
    await user.click(selectAllBtn);

    await waitFor(() => expect(mockSelectionPreview).toHaveBeenCalledTimes(1));
    expect(await screen.findByTestId('device-selection-scope-badge')).toHaveTextContent('跨页已冻结');

    // 打开归入弹窗：显示跨页预览返回的受影响型号，提交使用冻结 ID
    await user.click(screen.getByTestId('device-bulk-assign-project'));
    expect(await screen.findByTestId('assign-model-scope')).toHaveTextContent('M-PAGE1、M-PAGE2');
    await user.selectOptions(screen.getByTestId('assign-project-select'), 'proj-a');
    await user.click(screen.getByTestId('assign-project-confirm'));
    await waitFor(() =>
      expect(mockAssignDevicesToProject).toHaveBeenCalledWith('proj-a', [1, 2, 101, 102]),
    );

    // 超限场景（exceeds_limit=true）：写按钮全部禁用
    mockSelectionPreview.mockResolvedValueOnce({
      action: 'assign_project',
      total_matched: 620,
      max_allowed: 500,
      exceeds_limit: true,
      device_ids: [],
      status_counts: { all: 620, ONLINE: 620, BUSY: 0, OFFLINE: 0, ERROR: 0, need_attention: 0, tagged: 0 },
      affected_models: ['M-BIG'],
      devices_without_model_serials: [],
      retired_count: 0,
      active_count: 620,
      stale_count: 0,
    });
    fireEvent.click(screen.getByLabelText('选择设备 SN-1'));
    await user.click(await screen.findByTestId('device-select-all-filtered'));
    await waitFor(() => expect(screen.getByTestId('device-bulk-assign-project')).toBeDisabled());
    expect(screen.getByTestId('device-bulk-tags')).toBeDisabled();
  });

  it('#4117 Q3：切换筛选后晚到的旧跨页预览回包被丢弃，不会复活旧集合或提交旧筛选 ID', async () => {
    const user = userEvent.setup();
    const onlineDevs: DeviceListItem[] = Array.from({ length: 50 }, (_, i) => ({
      id: i + 1,
      serial: `ONLINE-${i + 1}`,
      model: 'M-ONLINE',
      host_id: 'h1',
      host_name: 'Host-1',
      status: 'ONLINE',
      tags: [],
      last_seen: '2026-08-05T18:00:00+08:00',
    }));
    const offlineDevs: DeviceListItem[] = [
      {
        id: 201,
        serial: 'OFFLINE-201',
        model: 'M-OFFLINE',
        host_id: 'h2',
        host_name: 'Host-2',
        status: 'OFFLINE',
        tags: [],
        last_seen: '2026-08-05T18:00:00+08:00',
      },
    ];
    setServerDevices([...onlineDevs, ...offlineDevs], 120);

    let resolveStalePreview!: (value: unknown) => void;
    mockSelectionPreview.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveStalePreview = resolve;
        }),
    );

    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });
    await waitFor(() => expect(screen.getByText('ONLINE-1')).toBeInTheDocument());

    // 在初始筛选（全部）下触发跨页预览，请求挂起
    fireEvent.click(screen.getByLabelText('选择设备 ONLINE-1'));
    await user.click(await screen.findByTestId('device-select-all-filtered'));
    await waitFor(() => expect(mockSelectionPreview).toHaveBeenCalledTimes(1));

    // 在预览回包前切换到「需关注」筛选
    fireEvent.click(screen.getByRole('button', { name: /需关注/ }));
    await waitFor(() => expect(screen.getByText('OFFLINE-201')).toBeInTheDocument());

    // 晚到的旧预览回包（包含不属于「需关注」的旧 ID [1, 2, 101]）
    resolveStalePreview({
      action: 'assign_project',
      total_matched: 120,
      max_allowed: 500,
      exceeds_limit: false,
      device_ids: [1, 2, 101],
      status_counts: { all: 120, ONLINE: 120, BUSY: 0, OFFLINE: 0, ERROR: 0, need_attention: 0, tagged: 0 },
      affected_models: ['M-ONLINE'],
      devices_without_model_serials: [],
      retired_count: 0,
      active_count: 120,
      stale_count: 0,
    });

    // 等微任务落地，旧跨页冻结不得复活
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId('device-selection-scope-badge')).not.toBeInTheDocument();
    expect(screen.queryByTestId('device-bulk-action-bar')).not.toBeInTheDocument();

    // 在新筛选下勾选 OFFLINE-201 并提交归入项目，确认只提交 201 而绝不含旧预览 ID
    fireEvent.click(screen.getByLabelText('选择设备 OFFLINE-201'));
    await user.click(await screen.findByTestId('device-bulk-assign-project'));
    await user.selectOptions(await screen.findByTestId('assign-project-select'), 'proj-a');
    await user.click(screen.getByTestId('assign-project-confirm'));
    await waitFor(() => expect(mockAssignDevicesToProject).toHaveBeenCalledWith('proj-a', [201]));
  });

  it('#496 B3-1：?drawer= 深链指向非当前页设备时独立调用 GET /devices/{id} 打开抽屉', async () => {
    mockGetDevice.mockResolvedValue({
      id: 999,
      serial: 'DEEP-LINK-999',
      model: 'DeepModel',
      host_id: 'h-deep',
      status: 'ONLINE',
      tags: ['deep'],
      last_seen: '2026-08-05T18:00:00+08:00',
    });
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper(['/devices?drawer=999']) });

    await waitFor(() => expect(mockGetDevice).toHaveBeenCalledWith(999));
    expect(await screen.findByTestId('fleet-device-drawer')).toHaveTextContent('DEEP-LINK-999');
  });

  it('passes disk telemetry through to the storage column', async () => {
    setServerDevices([
      {
        id: 1,
        serial: 'TEST-SERIAL',
        model: 'TestModel',
        host_id: '198-51-100-123',
        host_name: '198.51.100.123',
        status: 'ONLINE',
        tags: [],
        last_seen: '2026-08-05T18:00:00+08:00',
        disk_total: 128 * 1024 ** 3,
        disk_used: 100 * 1024 ** 3,
      },
    ]);
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByRole('columnheader', { name: '存储' })).toBeInTheDocument();
      expect(screen.getByText('剩 28.0 GiB')).toBeInTheDocument();
    });
  });

  it('expanded row shows the device return status and open debt entries', async () => {
    setServerDevices([
      {
        id: 1,
        serial: 'TEST-SERIAL',
        model: 'TestModel',
        host_id: '198-51-100-123',
        host_name: '198.51.100.123',
        status: 'ONLINE',
        tags: [],
        last_seen: '2026-08-05T18:00:00+08:00',
        return_state: {
          'power-cycle': {
            state: 'debt',
            source: 'job',
            updated_at: '2026-10-19T02:00:00Z',
          },
        },
      },
    ]);
    mockReturnDebts.mockResolvedValue([
      {
        id: 11,
        family: 'power-cycle',
        source_kind: 'job',
        source_job_id: 9527,
        cause: 'not_executed',
        created_at: '2026-10-19T02:00:00Z',
      },
    ]);
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('TEST-SERIAL')).toBeInTheDocument());
    fireEvent.click(screen.getByText('TEST-SERIAL'));

    expect(await screen.findByTestId('device-return-summary-1')).toHaveTextContent('欠账');
    expect(screen.getByTestId('device-return-family-1-power-cycle')).toHaveTextContent('来源：job');
    expect(await screen.findByTestId('device-return-debt-11')).toHaveTextContent('来源：job #9527');
    expect(screen.getByTestId('device-return-override-1')).toBeInTheDocument();
  });

  it('#3904：BUSY+MAINTENANCE 欠账首屏渲染「归还欠账」而非「测试中」', async () => {
    setServerDevices([
      {
        id: 21,
        serial: 'DEBT-BUSY',
        model: 'TestModel',
        host_id: 'h-debt',
        host_name: 'host-debt',
        status: 'BUSY',
        tags: [],
        last_seen: '2026-08-05T18:00:00+08:00',
        active_lease_type: 'MAINTENANCE',
        active_lease_reason: '归还欠账',
        return_state: {
          'power-cycle': { state: 'debt', source: 'job', updated_at: '2026-10-19T02:00:00Z' },
        },
      },
    ]);
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    const badge = await screen.findByTestId('device-status-badge-21');
    expect(badge).toHaveTextContent('归还欠账');
    expect(badge).not.toHaveTextContent('测试中');
  });

  it('#3904：BUSY+MAINTENANCE 非欠账首屏渲染「维护占用」', async () => {
    setServerDevices([
      {
        id: 22,
        serial: 'MAINT-BUSY',
        model: 'TestModel',
        host_id: 'h-maint',
        host_name: 'host-maint',
        status: 'BUSY',
        tags: [],
        last_seen: '2026-08-05T18:00:00+08:00',
        active_lease_type: 'MAINTENANCE',
        active_lease_reason: 'manual_network_supply',
      },
    ]);
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    const badge = await screen.findByTestId('device-status-badge-22');
    expect(badge).toHaveTextContent('维护占用');
    expect(badge.getAttribute('title')).toBe('manual_network_supply');
    expect(badge).not.toHaveTextContent('测试中');
  });

  it('#3904：BUSY+JOB 与缺 active_lease_type 旧响应均维持「测试中」', async () => {
    setServerDevices([
      {
        id: 23,
        serial: 'JOB-BUSY',
        model: 'TestModel',
        host_id: 'h-job',
        host_name: 'host-job',
        status: 'BUSY',
        tags: [],
        last_seen: '2026-08-05T18:00:00+08:00',
        active_lease_type: 'JOB',
        active_lease_reason: 'plan-run',
      },
      {
        id: 24,
        serial: 'LEGACY-BUSY',
        model: 'TestModel',
        host_id: 'h-legacy',
        host_name: 'host-legacy',
        status: 'BUSY',
        tags: [],
        last_seen: '2026-08-05T18:00:00+08:00',
      },
    ]);
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    expect(await screen.findByTestId('device-status-badge-23')).toHaveTextContent('测试中');
    expect(screen.getByTestId('device-status-badge-24')).toHaveTextContent('测试中');
  });

  it('admin can bulk-assign selected devices to a project', async () => {
    const user = userEvent.setup();
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('TEST-SERIAL')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('选择设备 TEST-SERIAL'));
    const assignButton = await screen.findByTestId('device-bulk-assign-project');

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
    const user = userEvent.setup();
    mockUseAuthSession.mockReturnValue({ data: { role: 'user' } });
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('TEST-SERIAL')).toBeInTheDocument());
    const addBtn = screen.getByTestId('device-add-button');
    expect(addBtn).toBeDisabled();
    expect(addBtn).toHaveAttribute('title', '需要管理员权限');

    fireEvent.click(screen.getByLabelText('选择设备 TEST-SERIAL'));

    await waitFor(() => expect(screen.getByTestId('device-bulk-action-bar')).toBeInTheDocument());
    expect(screen.getByTestId('device-bulk-assign-project')).toBeDisabled();
    expect(screen.getByTestId('device-bulk-assign-project')).toHaveAttribute('title', '需要管理员权限');
    expect(screen.getByTestId('device-bulk-tags')).toBeDisabled();
    expect(screen.getByTestId('device-bulk-tags')).toHaveAttribute('title', '需要管理员权限');
    await user.click(screen.getByTestId('device-bulk-more'));
    expect(screen.queryByTestId('device-bulk-swipe-trail-on')).not.toBeInTheDocument();
    expect(screen.queryByTestId('device-bulk-swipe-trail-off')).not.toBeInTheDocument();
    expect(screen.queryByTestId('device-bulk-skip-oobe')).not.toBeInTheDocument();
  });

  it('admin can bulk-enable swipe trail on selected devices', async () => {
    const user = userEvent.setup();
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('TEST-SERIAL')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('选择设备 TEST-SERIAL'));
    await user.click(await screen.findByTestId('device-bulk-more'));
    await user.click(await screen.findByTestId('device-bulk-swipe-trail-on'));

    await waitFor(() => {
      expect(mockBulkSwipeTrail).toHaveBeenCalledWith([1], true);
    });
  });

  it('admin can quick-skip OOBE on selected devices via more menu', async () => {
    const user = userEvent.setup();
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('TEST-SERIAL')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('选择设备 TEST-SERIAL'));
    await user.click(await screen.findByTestId('device-bulk-more'));
    await user.click(await screen.findByTestId('device-bulk-skip-oobe'));

    await waitFor(() => {
      expect(mockBulkSkipOobe).toHaveBeenCalledWith([1]);
    });
  });

  it('#823：添加设备后失效覆盖任意筛选态的设备列表', async () => {
    mockCreateDevice.mockResolvedValue({ id: 99, serial: 'NEW-SERIAL' });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
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
    mockCreateDevice.mockResolvedValue({ id: 99, serial: 'NEW-SERIAL' });
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
    fireEvent.click(screen.getByRole('button', { name: /添加设备/ }));
    fireEvent.click(screen.getByRole('button', { name: /mock-添加提交/ }));

    await waitFor(() => expect(mockCreateDevice).toHaveBeenCalled());
    await waitFor(() =>
      expect(queryClient.getQueryState(deviceKeys.all())?.isInvalidated).toBe(true),
    );
  });

  it('#2068：归入项目后全量设备缓存（deviceKeys.all()）一并失效', async () => {
    const user = userEvent.setup();
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
    expect(mockListPage).toHaveBeenLastCalledWith(
      expect.not.objectContaining({ include_stale: true, include_retired: true }),
    );

    fireEvent.click(screen.getByTestId('devices-more-filters'));
    fireEvent.click(screen.getByTestId('device-toggle-stale'));
    await waitFor(() =>
      expect(mockListPage).toHaveBeenLastCalledWith(
        expect.objectContaining({ include_stale: true }),
      ),
    );

    fireEvent.click(await screen.findByTestId('device-toggle-retired'));
    await waitFor(() =>
      expect(mockListPage).toHaveBeenLastCalledWith(
        expect.objectContaining({ include_stale: true, include_retired: true }),
      ),
    );
  });

  it('#2962：陈旧/退役/退役建议徽标按行渲染', async () => {
    setServerDevices([
      {
        id: 11, serial: 'STALE-DEV', model: 'M', host_id: '198-51-100-123', host_name: '198.51.100.123',
        status: 'OFFLINE', tags: [], last_seen: '2026-09-01T00:00:00Z', is_stale: true,
      },
      {
        id: 12, serial: 'RETIRED-DEV', model: 'M', host_id: '198-51-100-123', host_name: '198.51.100.123',
        status: 'ONLINE', tags: [], last_seen: '2026-09-26T00:00:00Z',
        retired_at: '2026-09-26T08:00:00Z', retired_by: 'admin', retire_reason: '报废',
      },
      {
        id: 13, serial: 'OLD-DEV', model: 'M', host_id: '198-51-100-123', host_name: '198.51.100.123',
        status: 'OFFLINE', tags: [], last_seen: '2026-08-01T00:00:00Z',
        is_stale: true, retire_suggested: true,
      },
    ]);
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    expect(await screen.findByTestId('device-stale-badge-11')).toBeInTheDocument();
    expect(screen.getByTestId('device-retired-badge-12')).toBeInTheDocument();
    expect(screen.getByTestId('device-retire-suggested-13')).toBeInTheDocument();
    expect(screen.queryByTestId('device-stale-badge-12')).not.toBeInTheDocument();
  });

  it('#2962：admin 可走批量入口退役设备（逐台结果回执）', async () => {
    const user = userEvent.setup();
    mockRetireBatch.mockResolvedValue({
      results: [{ device_id: 1, serial: 'TEST-SERIAL', status: 'retired' }],
      retired: 1, already_retired: 0, conflict: 0, not_found: 0, failed: 0,
    });
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('TEST-SERIAL')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('选择设备 TEST-SERIAL'));
    await user.click(await screen.findByTestId('device-bulk-more'));
    await user.click(await screen.findByTestId('device-bulk-retire'));
    await user.type(await screen.findByLabelText('退役原因'), '库存报废');
    await user.click(screen.getByTestId('device-retire-submit'));

    await waitFor(() =>
      expect(mockRetireBatch).toHaveBeenCalledWith([1], '库存报废'),
    );
  });

  it('#3483：解除退役全选已退役，不出现「并未退役（幂等跳过）」', async () => {
    const user = userEvent.setup();
    setServerDevices([
      {
        id: 21, serial: 'RET-A', model: 'M', host_id: '198-51-100-123', status: 'ONLINE',
        tags: [], last_seen: '2026-09-26T00:00:00Z', retired_at: '2026-09-26T08:00:00Z',
      },
      {
        id: 22, serial: 'RET-B', model: 'M', host_id: '198-51-100-123', status: 'ONLINE',
        tags: [], last_seen: '2026-09-26T00:00:00Z', retired_at: '2026-09-26T09:00:00Z',
      },
    ]);
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('RET-A')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('选择设备 RET-A'));
    fireEvent.click(screen.getByLabelText('选择设备 RET-B'));
    await user.click(await screen.findByTestId('device-bulk-more'));
    await user.click(await screen.findByTestId('device-bulk-unretire'));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('将对选中的 2 台设备解除退役');
    expect(dialog).not.toHaveTextContent('并未退役（幂等跳过）');
  });

  it('#3483：解除退役混入在役设备时提示实际幂等跳过台数', async () => {
    const user = userEvent.setup();
    setServerDevices([
      {
        id: 21, serial: 'RET-A', model: 'M', host_id: '198-51-100-123', status: 'ONLINE',
        tags: [], last_seen: '2026-09-26T00:00:00Z', retired_at: '2026-09-26T08:00:00Z',
      },
      {
        id: 22, serial: 'RET-B', model: 'M', host_id: '198-51-100-123', status: 'ONLINE',
        tags: [], last_seen: '2026-09-26T00:00:00Z', retired_at: '2026-09-26T09:00:00Z',
      },
      {
        id: 23, serial: 'ACTIVE-C', model: 'M', host_id: '198-51-100-123', status: 'ONLINE',
        tags: [], last_seen: '2026-09-26T00:00:00Z',
      },
    ]);
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('ACTIVE-C')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('选择设备 RET-A'));
    fireEvent.click(screen.getByLabelText('选择设备 RET-B'));
    fireEvent.click(screen.getByLabelText('选择设备 ACTIVE-C'));
    await user.click(await screen.findByTestId('device-bulk-more'));
    await user.click(await screen.findByTestId('device-bulk-unretire'));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('其中 1 台并未退役（幂等跳过）。');
  });

  it('#3483：退役模式读数不变（提示已退役台数）', async () => {
    const user = userEvent.setup();
    setServerDevices([
      {
        id: 21, serial: 'RET-A', model: 'M', host_id: '198-51-100-123', status: 'ONLINE',
        tags: [], last_seen: '2026-09-26T00:00:00Z', retired_at: '2026-09-26T08:00:00Z',
      },
      {
        id: 22, serial: 'RET-B', model: 'M', host_id: '198-51-100-123', status: 'ONLINE',
        tags: [], last_seen: '2026-09-26T00:00:00Z', retired_at: '2026-09-26T09:00:00Z',
      },
      {
        id: 23, serial: 'ACTIVE-C', model: 'M', host_id: '198-51-100-123', status: 'ONLINE',
        tags: [], last_seen: '2026-09-26T00:00:00Z',
      },
    ]);
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('ACTIVE-C')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('选择设备 RET-A'));
    fireEvent.click(screen.getByLabelText('选择设备 RET-B'));
    fireEvent.click(screen.getByLabelText('选择设备 ACTIVE-C'));
    await user.click(await screen.findByTestId('device-bulk-more'));
    await user.click(await screen.findByTestId('device-bulk-retire'));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('其中 2 台已是退役态（幂等跳过）。');
  });

  it('#2962：非 admin 看不到退役入口', async () => {
    const user = userEvent.setup();
    mockUseAuthSession.mockReturnValue({ data: { role: 'user' } });
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('TEST-SERIAL')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('选择设备 TEST-SERIAL'));
    await waitFor(() => expect(screen.getByTestId('device-bulk-action-bar')).toBeInTheDocument());
    await user.click(screen.getByTestId('device-bulk-more'));
    expect(screen.queryByTestId('device-bulk-retire')).not.toBeInTheDocument();
    expect(screen.queryByTestId('device-bulk-unretire')).not.toBeInTheDocument();
  });

  it('#4117 U2-1a：0 设备项目筛选保留 FilterWorkbench，不陷入整页空态死胡同', async () => {
    setServerDevices([]);
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper(['/devices?project=proj-a']) });

    expect(await screen.findByTestId('device-project-filter')).toBeInTheDocument();
    expect(screen.getByText('未找到匹配条件的设备')).toBeInTheDocument();
    expect(screen.queryByText('还没有设备')).not.toBeInTheDocument();
  });

  it('#4117 U2-1a：需关注(离线/异常) QuickView 同时命中 offline 与 error 并支持往返清除', async () => {
    setServerDevices([
      { id: 1, serial: 'DEV-IDLE', model: 'M1', host_id: '198-51-100-123', status: 'ONLINE', tags: [], last_seen: '2026-08-05T18:00:00+08:00' },
      { id: 2, serial: 'DEV-OFFLINE', model: 'M1', host_id: '198-51-100-123', status: 'OFFLINE', tags: [], last_seen: '2026-08-05T18:00:00+08:00' },
      { id: 3, serial: 'DEV-ERROR', model: 'M1', host_id: '198-51-100-123', status: 'ERROR', tags: [], last_seen: '2026-08-05T18:00:00+08:00' },
    ]);
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('DEV-IDLE')).toBeInTheDocument());
    const attentionBtn = screen.getByRole('button', { name: /需关注/ });
    fireEvent.click(attentionBtn);

    await waitFor(() => {
      expect(screen.queryByText('DEV-IDLE')).not.toBeInTheDocument();
      expect(screen.getByText('DEV-OFFLINE')).toBeInTheDocument();
      expect(screen.getByText('DEV-ERROR')).toBeInTheDocument();
    });

    fireEvent.click(attentionBtn);
    await waitFor(() => {
      expect(screen.getByText('DEV-IDLE')).toBeInTheDocument();
      expect(screen.getByText('DEV-OFFLINE')).toBeInTheDocument();
      expect(screen.getByText('DEV-ERROR')).toBeInTheDocument();
    });
  });
});

async function selectAllDevices() {
  await waitFor(() => expect(screen.getAllByLabelText(/^选择设备 /)[0]).toBeInTheDocument());
  for (const checkbox of screen.getAllByLabelText(/^选择设备 /)) {
    fireEvent.click(checkbox);
  }
  await waitFor(() => expect(screen.getByTestId('device-bulk-action-bar')).toBeInTheDocument());
}

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
    await waitFor(() => expect(stub.captured).toHaveLength(1));
    return await stub.captured[0].text();
  } finally {
    stub.restore();
  }
}

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
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true, writable: true, value: { writeText },
    });
    mockUseAuthSession.mockReturnValue({ data: { role: 'admin' } });
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });
    await selectAllDevices();
    await user.click(await screen.findByTestId('device-bulk-more'));
    await user.click(await screen.findByTestId('device-bulk-copy-serials'));
    await waitFor(() => expect(writes.length + execCalls.length).toBeGreaterThan(0));
    return writes[0] ?? '';
  } finally {
    if (originalExec) Object.defineProperty(document, 'execCommand', { configurable: true, writable: true, value: originalExec });
    else delete (document as unknown as Record<string, unknown>).execCommand;
  }
}

function dangerousDevice(overrides: Partial<DeviceListItem> = {}): DeviceListItem {
  return {
    id: 1,
    serial: '=1+1',
    model: '@SUM(1,1)',
    host_id: '198-51-100-123',
    host_name: null,
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
    mockProjectsList.mockResolvedValue([]);
  });

  it('危险 serial / model / build / tags 在 raw CSV 里带可见 apostrophe', async () => {
    setServerDevices([dangerousDevice()]);
    const csv = await exportCsvText();
    expect(csv).toContain("'=1+1");
    expect(csv).toContain("'@SUM(1,1)");
    expect(csv).toContain("'-V1");
    expect(csv).toContain("'=cmd|x");
  });

  it('TAB / NUL / 全角触发前缀也中和', async () => {
    setServerDevices([
      dangerousDevice({ serial: '\t=1+1', model: '\0=1+1' }),
      dangerousDevice({ id: 2, serial: '＝1+1', model: '＋x' }),
    ]);
    const csv = await exportCsvText();
    expect(csv).toContain("'\t=1+1");
    expect(csv).toContain("'\0=1+1");
    expect(csv).toContain("'＝1+1");
    expect(csv).toContain("'＋x");
  });

  it('危险 host.name（管理员输入）进入 Host 列时被中和', async () => {
    setServerDevices([
      dangerousDevice({ serial: 'S1', model: 'M', host_name: '=HACK()', build_display_id: null, tags: [] }),
    ]);
    const csv = await exportCsvText();
    expect(csv).toContain(`"'=HACK()"`);
  });

  it('正常 host.name 不误伤（无前缀时不加 apostrophe）', async () => {
    setServerDevices([
      dangerousDevice({ serial: 'S1', model: 'M', host_name: 'node-a', build_display_id: null, tags: [] }),
    ]);
    const csv = await exportCsvText();
    expect(csv).toContain('"node-a"');
    expect(csv).not.toContain("'node-a");
  });

  it('正常设备与 number ID 不误伤：中间位置的 - / + / @ 保持原样', async () => {
    setServerDevices([
      {
        id: 7, serial: 'ABC-123', model: 'A+B', host_id: '198-51-100-123', host_name: null,
        status: 'ONLINE', tags: ['smoke', 'regression'],
        build_display_id: 'V1', last_seen: '2026-08-05T18:00:00+08:00',
      },
    ]);
    const csv = await exportCsvText();
    expect(csv).toContain('ABC-123');
    expect(csv).toContain('A+B');
    expect(csv).toContain('smoke|regression');
    expect(csv).not.toContain("'ABC-123");
    expect(csv).not.toContain("'A+B");
    expect(csv).not.toContain("'smoke|regression");
    expect(csv).toContain('"7","ABC-123"');
    expect(csv).not.toContain("'7");
  });

  it('number ID 保持数值语义：负数 id 不得被加 apostrophe 变成文本', async () => {
    setServerDevices([
      {
        id: -5, serial: 'NEG-ID', model: 'M', host_id: null, host_name: null, status: 'ONLINE',
        tags: [], build_display_id: null, last_seen: '2026-08-05T18:00:00+08:00',
      },
    ]);
    const csv = await exportCsvText();
    expect(csv).toContain('"-5","NEG-ID"');
    expect(csv).not.toContain("'-5");
  });

  it('CSV 原有逗号 / 双引号 / CR/LF framing 回归不因中和而回退', async () => {
    setServerDevices([
      {
        id: 1, serial: 'a,b', model: 'say "hi"', host_id: null, host_name: null, status: 'ONLINE',
        tags: [], build_display_id: null, last_seen: '2026-08-05T18:00:00+08:00',
      },
    ]);
    const csv = await exportCsvText();
    expect(csv).toContain('"a,b"');
    expect(csv).toContain('"say ""hi"""');
    expect(csv).toContain('"ID","Serial","Model","Product","Status","Host","Build","Tags","Last Seen"');
  });
});

describe('DevicesPage — clipboard 结构与公式中和（S4 / #3237）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockProjectsList.mockResolvedValue([]);
  });

  it('公式前缀被中和', async () => {
    setServerDevices([
      dangerousDevice({ id: 1, serial: '=1+1' }),
      dangerousDevice({ id: 2, serial: '@x' }),
    ]);
    expect(await copySerialsText()).toBe("'=1+1\n'@x");
  });

  it('行首双引号被中和，不把下一条记录吞进同一 cell', async () => {
    setServerDevices([
      dangerousDevice({ id: 1, serial: '"=1+1' }),
      dangerousDevice({ id: 2, serial: 'B' }),
    ]);
    const text = await copySerialsText();
    expect(text).toBe('\'"=1+1\nB');
    expect(text.split('\n')).toHaveLength(2);
  });

  it('结构-only：行首引号（无公式前缀）同样被中和，不吞并下一条记录', async () => {
    setServerDevices([
      dangerousDevice({ id: 1, serial: '"a' }),
      dangerousDevice({ id: 2, serial: 'B' }),
    ]);
    const text = await copySerialsText();
    expect(text).toBe('\'"a\nB');
    expect(text.split('\n')).toHaveLength(2);
  });

  it('内嵌 TAB / CR / LF / NUL 四种都不制造额外 clipboard cell/row', async () => {
    setServerDevices([
      dangerousDevice({ id: 1, serial: 'a\tb' }),
      dangerousDevice({ id: 2, serial: 'c\rd' }),
      dangerousDevice({ id: 3, serial: 'e\nf' }),
      dangerousDevice({ id: 4, serial: 'g\0h' }),
    ]);
    const text = await copySerialsText();
    expect(text).toBe('a\\tb\nc\\rd\ne\\nf\ng\\0h');
    expect(text.split('\n')).toHaveLength(4);
    expect(text).not.toMatch(/[\t\r\0]/);
  });

  it('普通 serial 原样复制', async () => {
    setServerDevices([
      dangerousDevice({ id: 1, serial: 'ABC-123' }),
      dangerousDevice({ id: 2, serial: 'foo@bar' }),
    ]);
    expect(await copySerialsText()).toBe('ABC-123\nfoo@bar');
  });
});
