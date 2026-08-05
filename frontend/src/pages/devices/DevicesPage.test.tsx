import { render, screen, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const mockDevicesList = vi.fn();
const mockFetchHostList = vi.fn();

vi.mock('@/utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return {
    ...actual,
    fetchHostList: (...args: unknown[]) => mockFetchHostList(...args),
    api: {
      ...actual.api,
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
  useAuthSession: () => ({
    data: { role: 'admin' },
  }),
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

vi.mock('@/components/device/DeviceBulkActionBar', () => ({
  default: () => null,
}));

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
          host_id: '172-21-9-123',
          status: 'ONLINE',
          tags: [],
          last_seen: '2026-08-05T18:00:00+08:00',
        },
      ],
      total: 1,
    });
    mockFetchHostList.mockResolvedValue([
      {
        id: '172-21-9-123',
        name: '172.21.9.123',
        ip: '172.21.9.123',
        ssh_user: 'android',
        status: 'ONLINE',
        extra: {},
        mount_status: {},
        last_heartbeat: null,
      },
    ]);
  });

  it('resolves host name when device host_id is a string', async () => {
    const DevicesPage = (await import('./DevicesPage')).default;
    render(<DevicesPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      const row = screen.getByText('TEST-SERIAL').closest('tr');
      expect(row).not.toBeNull();
      expect(within(row as HTMLElement).getByText('172.21.9.123')).toBeInTheDocument();
    });
  });
});
