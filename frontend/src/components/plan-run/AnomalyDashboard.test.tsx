import type { ReactNode } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import AnomalyDashboard from './AnomalyDashboard';
import { api } from '@/utils/api';
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

vi.mock('@/utils/api', () => ({
  api: {
    planRuns: {
      getCrashDetails: vi.fn().mockResolvedValue([]),
    },
  },
}));

function render_(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

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
    render_(<AnomalyDashboard runId={1} isLoading />);
    expect(screen.getByText('加载中…')).toBeTruthy();
  });

  it('shows error state', () => {
    render_(<AnomalyDashboard runId={1} isError />);
    expect(screen.getByText('异常数据加载失败，请稍后重试')).toBeTruthy();
  });

  it('renders the redesigned AEE dashboard without abnormal-rate messaging', () => {
    render_(<AnomalyDashboard {...({ data: makeData(), timeScope: 'all' } as any)} />);
    expect(screen.getByText('本次新增 · 细分类型占比')).toBeTruthy();
    expect(screen.getByText('本次新增 · 包名榜')).toBeTruthy();
    expect(screen.getByTestId('anomaly-tab-current')).toBeTruthy();
    expect(screen.getByTestId('anomaly-tab-preexisting')).toBeTruthy();
    // 遗留默认不占首屏：未点 Tab 时不应渲染包名榜
    expect(screen.queryByText('运行前遗留 · 包名榜')).toBeNull();
    expect(screen.queryByTestId('preexisting-pkg-com.legacy.camera')).toBeNull();
    expect(screen.getAllByText('com.runtime.camera').length).toBeGreaterThan(0);
    expect(screen.queryByText(/异常率/)).toBeNull();
    expect(screen.queryByText(/超阈值/)).toBeNull();
  });

  it('renders UNIVIEW group with source prefix, kept separate from AEE (#1956)', () => {
    render_(
      <AnomalyDashboard
        {...({
          data: makeData({
            current_run: makeSection({
              total_events: 2,
              affected_device_count: 1,
              top_package_name: 'com.android.camera2',
              top_subtype: 'Java Crash',
              subtype_distribution: [
                { subtype: 'ANR', group: 'UNIVIEW', count: 1, share: 0.5 },
                { subtype: 'Java Crash', group: 'UNIVIEW', count: 1, share: 0.5 },
              ],
              package_ranking: [
                {
                  package_name: 'com.android.camera2',
                  total_count: 1,
                  affected_device_count: 1,
                  latest_detected_at: '2026-09-14T08:59:00Z',
                  subtype_breakdown: [
                    { subtype: 'Java Crash', group: 'UNIVIEW', count: 1 },
                  ],
                },
              ],
            }),
          }),
          timeScope: 'all',
        } as any)}
      />,
    );
    // 展锐 subtype 带来源前缀（同名 subtype 可能与 AEE 家族并存）
    expect(screen.getAllByText(/UNIVIEW · Java Crash/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/UNIVIEW · ANR/).length).toBeGreaterThan(0);
    expect(screen.getAllByText('com.android.camera2').length).toBeGreaterThan(0);
  });

  it('renders preexisting package ranking rows and toggles selection', () => {
    render_(
      <AnomalyDashboard
        {...({
          data: makeData({
            preexisting: makeSection({
              total_events: 4,
              package_ranking: [
                {
                  package_name: 'com.preexisting.a',
                  total_count: 3,
                  affected_device_count: 2,
                  latest_detected_at: null,
                  subtype_breakdown: [{ subtype: 'NE', count: 3 }],
                },
                {
                  package_name: 'com.preexisting.b',
                  total_count: 1,
                  affected_device_count: 1,
                  latest_detected_at: null,
                  subtype_breakdown: [{ subtype: 'ANR', count: 1 }],
                },
              ],
            }),
          }),
          timeScope: 'all',
        } as any)}
      />,
    );
    fireEvent.click(screen.getByTestId('anomaly-tab-preexisting'));
    const rowA = screen.getByTestId('preexisting-pkg-com.preexisting.a');
    expect(rowA).toBeTruthy();
    expect(screen.getByText('com.preexisting.b')).toBeTruthy();
    fireEvent.click(rowA);
    expect(rowA.getAttribute('aria-pressed')).toBe('true');
  });

  it('opens the preexisting full package drawer', () => {
    const ranking = Array.from({ length: 7 }, (_, i) => ({
      package_name: `com.pkg.${i}`,
      total_count: 7 - i,
      affected_device_count: 1,
      latest_detected_at: null,
      subtype_breakdown: [{ subtype: 'NE', count: 7 - i }],
    }));
    render_(
      <AnomalyDashboard
        {...({
          data: makeData({ preexisting: makeSection({ package_ranking: ranking }) }),
          timeScope: 'all',
        } as any)}
      />,
    );
    fireEvent.click(screen.getByTestId('anomaly-tab-preexisting'));
    fireEvent.click(screen.getByRole('button', { name: /查看全部 \(7\)/i }));
    expect(screen.getByText(/包名榜 · 全部 \(7\)/i)).toBeTruthy();
    expect(screen.getByText('com.pkg.6')).toBeTruthy();
  });

  it('renders a Recharts donut chart with legend below and center total', () => {
    const longSubtype = 'Kernel API Dump Very Long Tail Stability Exception Name';
    render_(
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
      'below',
    );
    // Legend shows both subtypes (truncation info on fullLabel is in legend text)
    const legend = screen.getByTestId('current-run-pie-chart-legend');
    expect(legend.textContent).toContain('HWT');
    expect(pieChart.querySelector('[data-center-total="true"]')?.textContent).toBe('3');
  });

  it('shows compatibility hint when origin split is unavailable', () => {
    render_(
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
    render_(<AnomalyDashboard {...({ data: makeData(), timeScope: 'all' } as any)} />);
    expect(screen.getAllByText('HWT').length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: /com\.runtime\.camera/i }));
    expect(screen.queryAllByText('HWT')).toHaveLength(0);
    expect(screen.getAllByText('JE').length).toBeGreaterThan(0);
  });

  it('calls onTimeScopeChange when time-scope button is clicked', () => {
    const fn = vi.fn();
    render_(
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

  it('flags platforms without a collector as unsupported (R4-b b1)', () => {
    render_(
      <AnomalyDashboard
        {...({
          data: makeData({
            platform_buckets: [
              {
                platform: 'MTK',
                categories: [],
                total: 2,
                affected_device_count: 1,
                reconciler_supported: true,
              },
              {
                platform: 'QCOM',
                categories: [],
                total: 0,
                affected_device_count: 0,
                reconciler_supported: false,
              },
            ],
          }),
          timeScope: 'all',
        } as any)}
      />,
    );
    // 无采集实现 → 明示「平台未支持」，不得渲染成「信号 0 · 设备 0」
    expect(screen.getByText('平台未支持')).toBeTruthy();
    expect(screen.getByText(/信号 2/)).toBeTruthy();
  });
  // watcher_capability 是本次运行各 Job 中「最降级」的一档（plan_run_watcher_summary._aggregate_watcher_capability）。
  // 降级提示曾在 WatcherSummaryCard 里，08-21 随死组件删除（a58e45e3）而丢失。
  describe('异常采集降级提示', () => {
    it('watcher_capability=unavailable 时提示「无异常」不等于没有崩溃', () => {
      render_(<AnomalyDashboard {...({ data: makeData({ watcher_capability: 'unavailable' }), timeScope: 'all' } as any)} />);
      const banner = screen.getByTestId('watcher-capability-degraded');
      expect(banner).toHaveAttribute('role', 'status');
      expect(banner).toHaveTextContent('异常采集降级');
      expect(banner).toHaveTextContent('「无异常」不等于没有崩溃');
    });

    it.each([['polling'], ['inotifyd_realtime'], ['skipped'], [null]])(
      'watcher_capability=%s 时不提示',
      (capability) => {
        render_(<AnomalyDashboard {...({ data: makeData({ watcher_capability: capability }), timeScope: 'all' } as any)} />);
        expect(screen.queryByTestId('watcher-capability-degraded')).toBeNull();
      },
    );

    it('加载失败时不提示（没有数据就没有结论）', () => {
      render_(<AnomalyDashboard runId={1} isError data={makeData({ watcher_capability: 'unavailable' })} />);
      expect(screen.queryByTestId('watcher-capability-degraded')).toBeNull();
    });
  });

  describe('U5-1 包名榜全量 Crash 明细抽屉与口径对齐', () => {
    beforeEach(() => {
      vi.mocked(api.planRuns.getCrashDetails).mockReset();
      vi.mocked(api.planRuns.getCrashDetails).mockResolvedValue([]);
    });

    it('点击「查看全部」后点击第 6+ 包名可在抽屉内加载并展示报错设备与事件明细', async () => {
      const ranking = Array.from({ length: 8 }, (_, i) => ({
        package_name: `com.app.pkg${i + 1}`,
        total_count: 10 - i,
        affected_device_count: 2,
        latest_detected_at: '2026-11-24T04:00:00Z',
        subtype_breakdown: [{ subtype: 'JE', count: 10 - i }],
      }));
      vi.mocked(api.planRuns.getCrashDetails).mockResolvedValue([
        {
          package_name: 'com.app.pkg7',
          subtype: 'JE',
          group: 'AEE',
          device_serial: 'SER-701',
          detected_at: '2026-11-24T03:50:00Z',
          entry_origin: 'runtime',
        },
        {
          package_name: 'com.app.pkg7',
          subtype: 'ANR',
          group: 'AEE',
          device_serial: 'SER-702',
          detected_at: '2026-11-24T03:45:00Z',
          entry_origin: 'runtime',
        },
        {
          // 运行前遗留事件在「本次新增」抽屉中必须被过滤掉
          package_name: 'com.app.pkg7',
          subtype: 'NE',
          group: 'AEE',
          device_serial: 'SER-LEGACY',
          detected_at: '2026-11-23T23:00:00Z',
          entry_origin: 'baseline',
        },
      ]);

      const onSelectDeviceSerial = vi.fn();
      render_(
        <AnomalyDashboard
          runId={1594}
          data={makeData({ current_run: makeSection({ package_ranking: ranking }) })}
          timeScope="6h"
          onSelectDeviceSerial={onSelectDeviceSerial}
        />,
      );

      fireEvent.click(screen.getByRole('button', { name: /查看全部 \(8\)/i }));
      fireEvent.click(screen.getByTestId('drawer-pkg-com.app.pkg7'));

      await waitFor(() => {
        expect(api.planRuns.getCrashDetails).toHaveBeenCalledWith(1594, 'com.app.pkg7', '6h');
      });

      const panel = await screen.findByTestId('crash-detail-panel');
      expect(panel.textContent).toContain('com.app.pkg7');
      expect(panel.textContent).toContain('SER-701');
      expect(panel.textContent).toContain('SER-702');
      // baseline 来源在「本次新增」抽屉中应过滤掉
      expect(panel.textContent).not.toContain('SER-LEGACY');

      // 点击受影响设备串号直达单机详情回调
      fireEvent.click(screen.getByTestId('crash-device-chip-SER-701'));
      expect(onSelectDeviceSerial).toHaveBeenCalledWith('SER-701');
    });

    it('支持抽屉内包名搜索过滤与运行前遗留 Tab 查看 baseline 明细', async () => {
      const ranking = Array.from({ length: 7 }, (_, i) => ({
        package_name: `com.legacy.mod${i + 1}`,
        total_count: 7 - i,
        affected_device_count: 1,
        latest_detected_at: null,
        subtype_breakdown: [{ subtype: 'NE', count: 7 - i }],
      }));
      vi.mocked(api.planRuns.getCrashDetails).mockResolvedValue([
        {
          package_name: 'com.legacy.mod6',
          subtype: 'NE',
          group: 'AEE',
          device_serial: 'SER-BASE-6',
          detected_at: '2026-11-23T22:00:00Z',
          entry_origin: 'baseline',
        },
        {
          package_name: 'com.legacy.mod6',
          subtype: 'JE',
          group: 'AEE',
          device_serial: 'SER-RUNTIME-SKIP',
          detected_at: '2026-11-24T01:00:00Z',
          entry_origin: 'runtime',
        },
      ]);

      render_(
        <AnomalyDashboard
          runId={1594}
          data={makeData({ preexisting: makeSection({ package_ranking: ranking }) })}
          timeScope="all"
        />,
      );

      fireEvent.click(screen.getByTestId('anomaly-tab-preexisting'));
      fireEvent.click(screen.getByRole('button', { name: /查看全部 \(7\)/i }));

      const searchInput = screen.getByLabelText('搜索包名');
      fireEvent.change(searchInput, { target: { value: 'mod6' } });
      expect(screen.queryByTestId('drawer-pkg-com.legacy.mod1')).toBeNull();
      fireEvent.click(screen.getByTestId('drawer-pkg-com.legacy.mod6'));

      const panel = await screen.findByTestId('crash-detail-panel');
      await waitFor(() => {
        expect(panel.textContent).toContain('SER-BASE-6');
      });
      expect(panel.textContent).not.toContain('SER-RUNTIME-SKIP');
    });

    it('Top 5 行不存在无效的 button 嵌套 button，且点击查看详情可直接打开抽屉明细', async () => {
      vi.mocked(api.planRuns.getCrashDetails).mockResolvedValue([
        {
          package_name: 'com.runtime.camera',
          subtype: 'JE',
          group: 'AEE',
          device_serial: 'SER-TOP1',
          detected_at: '2026-11-24T04:05:00Z',
          entry_origin: 'runtime',
        },
      ]);

      const { container } = render_(
        <AnomalyDashboard runId={1594} data={makeData()} timeScope="1h" />,
      );
      expect(container.querySelectorAll('button button').length).toBe(0);

      fireEvent.click(screen.getByTestId('crash-detail-btn-com.runtime.camera'));
      await waitFor(() => {
        expect(api.planRuns.getCrashDetails).toHaveBeenCalledWith(1594, 'com.runtime.camera', '1h');
      });
      const panel = await screen.findByTestId('crash-detail-panel');
      expect(panel.textContent).toContain('SER-TOP1');
    });
  });
});
