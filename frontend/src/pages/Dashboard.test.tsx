import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

// Mock dependencies before importing Dashboard
vi.mock('../utils/api', () => ({
  api: {
    stats: {
      dashboardSummary: vi.fn().mockResolvedValue({
        data: {
          hosts: {
            total: 453,
            online: 1,
            offline: 451,
            degraded: 1,
            avg_cpu_load: 0.36,
            avg_ram_usage: 23.87,
            avg_disk_usage: 8.07,
            online_rate: 0.0022,
          },
          devices: {
            total: 483,
            idle: 1,
            testing: 0,
            offline: 482,
            error: 0,
            low_battery: 169,
            high_temp: 0,
          },
          alerts: {
            total: 169,
            low_battery: 169,
            high_temp: 0,
            error: 0,
          },
          host_resources: [
            { ip: '172.21.10.36', cpu_load: 0.36, ram_usage: 23.87, disk_usage: 8.07 },
          ],
        },
      }),
      activity: vi.fn().mockResolvedValue({ data: { points: [], hours: 24 } }),
      completionTrend: vi.fn().mockResolvedValue({ data: { points: [], days: 7 } }),
    },
  },
}));

vi.mock('../hooks/useRealtimeDashboard', () => ({
  useRealtimeDashboard: vi.fn(() => ({
    isConnected: false,
    lastUpdateTime: new Date('2026-01-01T12:00:00'),
    lastMessage: null,
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
  PageHeader: ({ title, subtitle }: { title: string; subtitle?: string }) => (
    <div>
      <h2>{title}</h2>
      {subtitle && <p>{subtitle}</p>}
    </div>
  ),
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
  it('renders authoritative dashboard summary instead of paginated list length', async () => {
    const Dashboard = (await import('./Dashboard')).default;
    render(<Dashboard />, { wrapper: createWrapper() });

    expect(await screen.findByText('453')).toBeInTheDocument();
    expect(await screen.findByText('483')).toBeInTheDocument();
    expect(screen.getByText('169')).toBeInTheDocument();
    expect(screen.getByText('0.2%')).toBeInTheDocument();
  });

  it('renders page title', async () => {
    const Dashboard = (await import('./Dashboard')).default;
    render(<Dashboard />, { wrapper: createWrapper() });
    expect(screen.getByText('仪表盘')).toBeInTheDocument();
    expect(screen.getByText('系统运行状态总览')).toBeInTheDocument();
  });

  it('renders stat cards', async () => {
    const Dashboard = (await import('./Dashboard')).default;
    render(<Dashboard />, { wrapper: createWrapper() });
    expect(await screen.findByText('主机总数')).toBeInTheDocument();
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
    const { api } = await import('../utils/api');
    (api.stats.dashboardSummary as any).mockRejectedValue(new Error('Network Error'));

    const Dashboard = (await import('./Dashboard')).default;
    render(<Dashboard />, { wrapper: createWrapper() });
    expect(await screen.findByText('数据加载失败')).toBeInTheDocument();
  });

  it('shows connected status when ws is on', async () => {
    const { useRealtimeDashboard } = await import('../hooks/useRealtimeDashboard');
    (useRealtimeDashboard as any).mockReturnValue({
      isConnected: true,
      lastUpdateTime: new Date(),
      lastMessage: null,
    });

    const Dashboard = (await import('./Dashboard')).default;
    render(<Dashboard />, { wrapper: createWrapper() });
    expect(await screen.findByText('实时连接')).toBeInTheDocument();
  });
});
