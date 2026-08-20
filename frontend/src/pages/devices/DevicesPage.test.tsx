import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const mockDevicesList = vi.fn();
const mockFetchHostList = vi.fn();
const mockProjectsList = vi.fn();
const mockAssignDevicesToProject = vi.fn();
const mockUseAuthSession = vi.fn(() => ({ data: { role: 'admin' } }));

vi.mock('@/utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return {
    ...actual,
    fetchHostList: (...args: unknown[]) => mockFetchHostList(...args),
    // ADR-0029：批量归入走独立导出（非 api 属性）
    assignDevicesToProject: (...args: unknown[]) => mockAssignDevicesToProject(...args),
    api: {
      ...actual.api,
      projects: {
        ...actual.api.projects,
        list: (...args: unknown[]) => mockProjectsList(...args),
      },
      devices: {
        ...actual.api.devices,
        list: (...args: unknown[]) => mockDevicesList(...args),
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

vi.mock('./components/AddDeviceModal', () => ({
  AddDeviceModal: () => null,
}));

vi.mock('./components/BatchEditDeviceTagsDialog', () => ({
  BatchEditDeviceTagsDialog: () => null,
}));

vi.mock('./components/DeviceMetricsModal', () => ({
  DeviceMetricsModal: () => null,
}));

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
    mockDevicesList.mockResolvedValue({
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
    await user.click(screen.getByTestId('assign-project-select'));
    await user.click(await screen.findByRole('option', { name: 'Project A（proj-a）' }));
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
  });
});
