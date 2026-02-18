import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

// Mock dependencies before importing Dashboard
vi.mock('../utils/api', () => ({
  api: {
    hosts: {
      list: vi.fn().mockResolvedValue({ data: { items: [], total: 0 } }),
    },
    stats: {
      activity: vi.fn().mockResolvedValue({ data: { points: [], hours: 24 } }),
      completionTrend: vi.fn().mockResolvedValue({ data: { points: [], days: 7 } }),
    },
  },
}));

vi.mock('../hooks/useRealtimeDashboard', () => ({
  useRealtimeDashboard: vi.fn(() => ({
    devices: [],
    isConnected: false,
    lastUpdateTime: new Date('2026-01-01T12:00:00'),
    isLoading: false,
    isError: false,
  })),
}));

vi.mock('../config', () => ({
  WS_DASHBOARD_ENDPOINT: 'ws://localhost/ws/dashboard',
}));

vi.mock('@/components/charts', () => ({
  DeviceStatusChart: () => <div data-testid="device-status-chart" />,
  HostResourceChart: () => <div data-testid="host-resource-chart" />,
  ActivityChart: () => <div data-testid="activity-chart" />,
  CompletionTrendChart: () => <div data-testid="completion-trend-chart" />,
}));

vi.mock('@/mappers', () => ({
  mapDeviceToViewModel: (d: any) => d,
}));

vi.mock('../components/layout', () => ({
  PageContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
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

describe('Dashboard', () => {
  it('renders page title', async () => {
    const Dashboard = (await import('./Dashboard')).default;
    render(<Dashboard />, { wrapper: createWrapper() });
    expect(screen.getByText('仪表盘')).toBeInTheDocument();
    expect(screen.getByText('系统运行状态总览')).toBeInTheDocument();
  });

  it('renders stat cards', async () => {
    const Dashboard = (await import('./Dashboard')).default;
    render(<Dashboard />, { wrapper: createWrapper() });
    expect(screen.getByText('主机总数')).toBeInTheDocument();
    expect(screen.getByText('设备总数')).toBeInTheDocument();
    expect(screen.getByText('测试中')).toBeInTheDocument();
    expect(screen.getByText('告警')).toBeInTheDocument();
  });

  it('renders chart section header', async () => {
    const Dashboard = (await import('./Dashboard')).default;
    render(<Dashboard />, { wrapper: createWrapper() });
    expect(screen.getByText('数据统计')).toBeInTheDocument();
  });

  it('shows connection status badge', async () => {
    const Dashboard = (await import('./Dashboard')).default;
    render(<Dashboard />, { wrapper: createWrapper() });
    expect(screen.getByText('已断开')).toBeInTheDocument();
  });

  it('renders error state when data loading fails', async () => {
    const { useRealtimeDashboard } = await import('../hooks/useRealtimeDashboard');
    (useRealtimeDashboard as any).mockReturnValue({
      devices: [],
      isConnected: false,
      lastUpdateTime: new Date(),
      isLoading: false,
      isError: true,
    });

    const Dashboard = (await import('./Dashboard')).default;
    render(<Dashboard />, { wrapper: createWrapper() });
    expect(screen.getByText('数据加载失败')).toBeInTheDocument();
  });

  it('shows hosts data when loaded', async () => {
    const { useRealtimeDashboard } = await import('../hooks/useRealtimeDashboard');
    (useRealtimeDashboard as any).mockReturnValue({
      devices: [
        { serial: 'DEV1', model: 'Pixel', status: 'ONLINE', battery_level: 80, temperature: 30, network_latency: 10 },
        { serial: 'DEV2', model: 'Galaxy', status: 'BUSY', battery_level: 50, temperature: 35, network_latency: 20 },
      ],
      isConnected: true,
      lastUpdateTime: new Date(),
      isLoading: false,
      isError: false,
    });

    const Dashboard = (await import('./Dashboard')).default;
    render(<Dashboard />, { wrapper: createWrapper() });
    expect(screen.getByText('实时连接')).toBeInTheDocument();
  });
});
