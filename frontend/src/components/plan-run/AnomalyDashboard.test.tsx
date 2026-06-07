import type { ReactNode } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import AnomalyDashboard from './AnomalyDashboard';
import type { WatcherSummary } from '@/utils/api/types';

vi.mock('@/components/charts/StableResponsiveContainer', () => ({
  StableResponsiveContainer: ({
    children,
    className,
  }: {
    children: ReactNode;
    className?: string;
  }) => <div className={className}>{children}</div>,
}));

function makeSection(overrides: Record<string, unknown> = {}) {
  return {
    total_events: 3,
    affected_device_count: 2,
    top_package_name: 'com.runtime.camera',
    top_subtype: 'JE',
    subtype_distribution: [
      { subtype: 'JE', group: 'AEE', count: 2, share: 0.6667 },
      { subtype: 'HWT', group: 'VENDOR_AEE', count: 1, share: 0.3333 },
    ],
    package_ranking: [
      {
        package_name: 'com.runtime.camera',
        total_count: 2,
        affected_device_count: 1,
        latest_detected_at: '2026-06-06T00:10:00Z',
        subtype_breakdown: [{ subtype: 'JE', count: 2 }],
      },
      {
        package_name: 'com.vendor.camera',
        total_count: 1,
        affected_device_count: 1,
        latest_detected_at: '2026-06-06T00:12:00Z',
        subtype_breakdown: [{ subtype: 'HWT', count: 1 }],
      },
    ],
    ...overrides,
  };
}

const makeData = (overrides: Record<string, unknown> = {}): WatcherSummary =>
  ({
    plan_run_id: 1,
    time_scope: 'all',
    window_minutes: null,
    window_start_at: '2026-06-05T00:00:00Z',
    window_end_at: '2026-06-05T01:00:00Z',
    categories: [],
    total: 0,
    affected_device_count: 0,
    total_devices: 10,
    abnormal_rate: 0,
    threshold: 0.3,
    exceeded: false,
    supports_origin_split: true,
    current_run: makeSection(),
    preexisting: makeSection({
      total_events: 1,
      affected_device_count: 1,
      top_package_name: 'com.legacy.camera',
      top_subtype: 'ANR',
      subtype_distribution: [{ subtype: 'ANR', group: 'AEE', count: 1, share: 1 }],
      package_ranking: [
        {
          package_name: 'com.legacy.camera',
          total_count: 1,
          affected_device_count: 1,
          latest_detected_at: '2026-06-05T23:58:00Z',
          subtype_breakdown: [{ subtype: 'ANR', count: 1 }],
        },
      ],
    }),
    ...overrides,
  }) as WatcherSummary;

describe('AnomalyDashboard', () => {
  it('shows loading state', () => {
    render(<AnomalyDashboard isLoading />);
    expect(screen.getByText('加载中…')).toBeTruthy();
  });

  it('shows error state', () => {
    render(<AnomalyDashboard isError />);
    expect(screen.getByText('异常数据加载失败，请稍后重试')).toBeTruthy();
  });

  it('renders the redesigned AEE dashboard without abnormal-rate messaging', () => {
    render(<AnomalyDashboard {...({ data: makeData(), timeScope: 'all' } as any)} />);
    expect(screen.getByText('本次新增 · 细分类型占比')).toBeTruthy();
    expect(screen.getByText('本次新增 · 包名榜')).toBeTruthy();
    expect(screen.getByText('运行前遗留')).toBeTruthy();
    expect(screen.getAllByText('com.runtime.camera').length).toBeGreaterThan(0);
    expect(screen.queryByText(/异常率/)).toBeNull();
    expect(screen.queryByText(/超阈值/)).toBeNull();
  });

  it('renders a Recharts donut chart with legend below and center total', () => {
    const longSubtype = 'Kernel API Dump Very Long Tail Stability Exception Name';
    render(
      <AnomalyDashboard
        {...({
          data: makeData({
            current_run: makeSection({
              top_subtype: longSubtype,
              subtype_distribution: [
                { subtype: longSubtype, group: 'AEE', count: 2, share: 0.6667 },
                { subtype: 'HWT', group: 'VENDOR_AEE', count: 1, share: 0.3333 },
              ],
              package_ranking: [
                {
                  package_name: 'com.runtime.camera',
                  total_count: 2,
                  affected_device_count: 1,
                  latest_detected_at: '2026-06-06T00:10:00Z',
                  subtype_breakdown: [{ subtype: longSubtype, count: 2 }],
                },
                {
                  package_name: 'com.vendor.camera',
                  total_count: 1,
                  affected_device_count: 1,
                  latest_detected_at: '2026-06-06T00:12:00Z',
                  subtype_breakdown: [{ subtype: 'HWT', count: 1 }],
                },
              ],
            }),
          }),
          timeScope: 'all',
        } as any)}
      />,
    );
    const pieChart = screen.getByTestId('current-run-pie-chart');
    expect(pieChart.getAttribute('data-chart-type')).toBe('recharts-donut');
    expect(screen.getByTestId('current-run-pie-chart-legend')).toHaveAttribute(
      'data-legend-position',
      'side',
    );
    // Legend shows both subtypes (truncation info on fullLabel is in legend text)
    const legend = screen.getByTestId('current-run-pie-chart-legend');
    expect(legend.textContent).toContain('HWT');
    expect(pieChart.querySelector('[data-center-total="true"]')?.textContent).toBe('3');
  });

  it('shows compatibility hint when origin split is unavailable', () => {
    render(
      <AnomalyDashboard
        {...({
          data: makeData({
            supports_origin_split: false,
            current_run: makeSection({
              total_events: 2,
              top_package_name: 'com.legacy.unknown',
              top_subtype: 'ANR',
            }),
            preexisting: makeSection({
              total_events: 0,
              subtype_distribution: [],
              package_ranking: [],
            }),
          }),
          timeScope: 'all',
        } as any)}
      />,
    );
    expect(
      screen.getByText('该计划运行未记录新增/遗留来源标记，无法拆分运行前遗留'),
    ).toBeTruthy();
  });

  it('filters the pie-chart legend when a package row is selected', () => {
    render(<AnomalyDashboard {...({ data: makeData(), timeScope: 'all' } as any)} />);
    expect(screen.getAllByText('HWT').length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: /com\.runtime\.camera/i }));
    expect(screen.queryAllByText('HWT')).toHaveLength(0);
    expect(screen.getAllByText('JE').length).toBeGreaterThan(0);
  });

  it('calls onTimeScopeChange when time-scope button is clicked', () => {
    const fn = vi.fn();
    render(
      <AnomalyDashboard
        {...({
          data: makeData(),
          timeScope: 'all',
          onTimeScopeChange: fn,
        } as any)}
      />,
    );
    fireEvent.click(screen.getByText('15m'));
    expect(fn).toHaveBeenCalledWith('15m');
  });
});
