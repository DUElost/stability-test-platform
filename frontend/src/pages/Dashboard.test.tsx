import { act, render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

// Mock dependencies before importing Dashboard
vi.mock('@/utils/api', async (importOriginal) => {
  // #2364：只替换 api 面，其余（loadErrorCopy 等）走真实实现——错误分类映射
  // 是本次要验的行为，不该被 mock 掉。
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return {
    ...actual,
    api: {
      stats: {
        dashboardSummary: vi.fn().mockResolvedValue({
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
            { ip: '203.0.113.36', cpu_load: 0.36, ram_usage: 23.87, disk_usage: 8.07 },
          ],
        }),
        activity: vi.fn().mockResolvedValue({ points: [], hours: 24 }),
        completionTrend: vi.fn().mockResolvedValue({ points: [], days: 7 }),
        hostFailureRate: vi.fn().mockResolvedValue({ items: [] }),
        planSuccessRate: vi.fn().mockResolvedValue({ items: [] }),
        planRunPassRateTrend: vi.fn().mockResolvedValue({ points: [] }),
      },
      results: {
        summary: vi.fn().mockResolvedValue({
          runs_by_status: { finished: 0, failed: 0, canceled: 0, running: 0, total: 0 },
          test_type_stats: [],
          risk_distribution: { high: 2, medium: 1, low: 5, unknown: 0 },
          recent_runs: [],
        }),
      },
    },
  };
});
vi.mock('../hooks/useRealtimeDashboard', () => ({
  useRealtimeDashboard: vi.fn(() => ({
    isConnected: false,
    lastUpdateTime: new Date('2026-01-01T12:00:00'),
    lastMessage: null,
  })),
}));

vi.mock('../config', () => ({
  DASHBOARD_SUBSCRIPTION: 'dashboard',
}));

vi.mock('@/components/charts', () => ({
  DeviceStatusChart: () => <div data-testid="device-status-chart" />,
  HostResourceChart: () => <div data-testid="host-resource-chart" />,
  ActivityChart: () => <div data-testid="activity-chart" />,
  CompletionTrendChart: () => <div data-testid="completion-trend-chart" />,
  HostFailureRateChart: () => <div data-testid="host-failure-rate-chart" />,
  PlanSuccessRateChart: () => <div data-testid="plan-success-rate-chart" />,
  PlanRunPassRateTrendChart: () => <div data-testid="plan-run-pass-rate-trend-chart" />,
  RiskDistributionChart: () => <div data-testid="risk-distribution-chart" />,
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

function createQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
}

function createWrapper(client: QueryClient = createQueryClient()) {
  return ({ children }: { children: React.ReactNode }) => (
    <MemoryRouter>
      <QueryClientProvider client={client}>
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
    expect(screen.getByText('在线 1 · 0.2%')).toBeInTheDocument();
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

  it('renders risk distribution chart to fill the 2-col grid', async () => {
    const Dashboard = (await import('./Dashboard')).default;
    render(<Dashboard />, { wrapper: createWrapper() });
    expect(await screen.findByTestId('risk-distribution-chart')).toBeInTheDocument();
  });

  it('renders error state when data loading fails', async () => {
    const { api } = await import('@/utils/api');
    // 用 *Once*：mock 在文件内跨用例共享，常驻拒绝会污染后续用例（#2364 新增用例
    // 需要页面正常渲染出风险卡）。
    (api.stats.dashboardSummary as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('Network Error'),
    );

    const Dashboard = (await import('./Dashboard')).default;
    render(<Dashboard />, { wrapper: createWrapper() });
    expect(await screen.findByText(/数据加载失败/)).toBeInTheDocument();
  });

  // #2364：风险卡曾把任何失败都写成「加载失败」且整卡消失——刷新失败但手里还有
  // 上次成功数据时，应保留图表并标注数据陈旧，而不是让用户失去全部信号。
  it('keeps the last risk distribution and marks it stale when a refresh fails', async () => {
    const { api, ApiError } = await import('@/utils/api');
    const summary = api.results.summary as ReturnType<typeof vi.fn>;
    const client = createQueryClient();

    const Dashboard = (await import('./Dashboard')).default;
    render(<Dashboard />, { wrapper: createWrapper(client) });
    expect(await screen.findByTestId('risk-distribution-chart')).toBeInTheDocument();

    summary.mockRejectedValue(new ApiError('NETWORK_ERROR', '网络请求失败'));
    await act(async () => {
      await client.refetchQueries({ queryKey: ['results-summary'] });
    });

    expect(await screen.findByText(/风险分布最近一次刷新失败：请检查网络连接或稍后重试/)).toBeInTheDocument();
    expect(screen.getByTestId('risk-distribution-chart')).toBeInTheDocument();
  });

  // #2364：连上次数据都没有时给分类文案——超时（服务端繁忙）与网络层排查方向不同。
  it('shows the classified reason when the risk card has no data at all', async () => {
    const { api, ApiError } = await import('@/utils/api');
    (api.results.summary as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError('TIMEOUT', '请求超时，请重试'),
    );

    const Dashboard = (await import('./Dashboard')).default;
    render(<Dashboard />, { wrapper: createWrapper() });
    expect(
      await screen.findByText(/风险分布请求超时（服务端繁忙或网络慢），请稍后重试/),
    ).toBeInTheDocument();
  });
});
