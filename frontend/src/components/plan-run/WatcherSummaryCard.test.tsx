import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import WatcherSummaryCard from './WatcherSummaryCard';
import type { AeeBreakdown, WatcherSummary } from '@/utils/api/types';

const fixture: WatcherSummary = {
  plan_run_id: 12,
  window_minutes: 60,
  window_start_at: '2026-05-08T11:30:00Z',
  window_end_at: '2026-05-08T12:30:00Z',
  categories: [
    {
      category: 'AEE',
      count: 4,
      affected_device_count: 2,
      trend_change: 3,
      latest_device_serial: 'DEV-3064',
      latest_detected_at: '2026-05-08T12:25:00Z',
    },
    {
      category: 'ANR',
      count: 2,
      affected_device_count: 2,
      trend_change: -1,
      latest_device_serial: 'DEV-1024',
      latest_detected_at: '2026-05-08T12:20:00Z',
    },
    {
      category: 'TOMBSTONE',
      count: 1,
      affected_device_count: 1,
      trend_change: 0,
      latest_device_serial: 'DEV-2048',
      latest_detected_at: '2026-05-08T12:10:00Z',
    },
  ],
  total: 7,
  affected_device_count: 5,
  total_devices: 8,
  abnormal_rate: 0.625,
  threshold: 0.05,
  exceeded: true,
};

const breakdownFixture: AeeBreakdown = {
  crash_count: 3,            // AEE
  vendor_crash_count: 1,     // VENDOR_AEE
  anr_count: 2,
  packages: ['com.app.a', 'com.vendor.b', 'unknown'],
  by_package: [
    {
      package_name: 'com.app.a',
      crash_count: 3,
      vendor_crash_count: 0,
      anr_count: 1,
      latest_detected_at: '2026-05-08T12:25:00Z',
    },
    {
      package_name: 'com.vendor.b',
      crash_count: 0,
      vendor_crash_count: 1,
      anr_count: 0,
      latest_detected_at: '2026-05-08T12:18:00Z',
    },
    {
      package_name: 'unknown',
      crash_count: 0,
      vendor_crash_count: 0,
      anr_count: 1,
      latest_detected_at: '2026-05-08T12:20:00Z',
    },
  ],
};

describe('WatcherSummaryCard', () => {
  it('renders categories with trend arrows + threshold banner when exceeded', () => {
    render(<WatcherSummaryCard data={fixture} />);
    expect(screen.getByTestId('watcher-cat-AEE')).toHaveTextContent('4');
    expect(screen.getByTestId('watcher-cat-AEE-trend')).toHaveTextContent('+3');
    expect(screen.getByTestId('watcher-cat-ANR-trend')).toHaveTextContent('-1');
    expect(screen.getByTestId('watcher-cat-TOMBSTONE-trend')).toHaveTextContent('0');

    // Threshold banner
    const banner = screen.getByTestId('watcher-threshold-banner');
    expect(banner).toHaveTextContent('超过阈值');
    expect(banner).toHaveTextContent('62.5%');

    // Threshold marker on the progress bar
    expect(screen.getByTestId('watcher-threshold-marker')).toBeInTheDocument();
  });

  it('does not show threshold banner when not exceeded but still warns when there are signals', () => {
    render(
      <WatcherSummaryCard
        data={{
          ...fixture,
          abnormal_rate: 0.02,
          exceeded: false,
          total: 1,
        }}
      />,
    );
    expect(screen.queryByTestId('watcher-threshold-banner')).not.toBeInTheDocument();
    expect(screen.getByTestId('watcher-warn-banner')).toBeInTheDocument();
  });

  it('renders empty state when categories list is empty', () => {
    render(
      <WatcherSummaryCard
        data={{
          ...fixture,
          categories: [],
          total: 0,
          affected_device_count: 0,
          abnormal_rate: 0,
          exceeded: false,
        }}
      />,
    );
    expect(screen.getByText('该窗口内未检测到异常')).toBeInTheDocument();
    // M0 T0-6 子项 4: 空态附带 Watcher 启用引导文案
    expect(screen.getByTestId('watcher-disabled-hint')).toHaveTextContent(
      'Watcher 已启用',
    );
  });

  it('forwards window changes to parent', () => {
    const onChange = vi.fn();
    render(
      <WatcherSummaryCard
        data={fixture}
        windowMinutes={60}
        onWindowChange={onChange}
      />,
    );
    fireEvent.click(screen.getByTestId('watcher-window-1440'));
    expect(onChange).toHaveBeenCalledWith(1440);
  });

  // ----------------------------------------------------------------------
  // M0/PR #2: aee_breakdown 渲染
  // ----------------------------------------------------------------------

  it('renders top-bar Crash + ANR chips when aee_breakdown is present (crash = AEE + VENDOR_AEE)', () => {
    render(
      <WatcherSummaryCard
        data={{ ...fixture, aee_breakdown: breakdownFixture }}
      />,
    );
    const summary = screen.getByTestId('watcher-aee-summary');
    expect(summary).toBeInTheDocument();
    // crash chip = crash_count(3) + vendor_crash_count(1) = 4
    const crashChip = screen.getByTestId('watcher-crash-chip');
    expect(crashChip).toHaveTextContent('4 Crash');
    // 同时有 AEE + Vendor 时挂 tooltip 拆分提示
    expect(crashChip).toHaveAttribute('title', 'AEE 3 + Vendor 1');
    // anr chip
    expect(screen.getByTestId('watcher-anr-chip')).toHaveTextContent('2 ANR');
  });

  it('renders packages chip row with unknown bucket included', () => {
    render(
      <WatcherSummaryCard
        data={{ ...fixture, aee_breakdown: breakdownFixture }}
      />,
    );
    const row = screen.getByTestId('watcher-packages-row');
    expect(row).toBeInTheDocument();
    // 每个包都有自己的 chip,总数 = crash + vendor + anr
    expect(screen.getByTestId('watcher-pkg-com.app.a')).toHaveTextContent(
      'com.app.a',
    );
    expect(screen.getByTestId('watcher-pkg-com.app.a')).toHaveTextContent('(4)'); // 3+0+1
    expect(screen.getByTestId('watcher-pkg-com.vendor.b')).toHaveTextContent('(1)');
    expect(screen.getByTestId('watcher-pkg-unknown')).toHaveTextContent('unknown');
    expect(screen.getByTestId('watcher-pkg-unknown')).toHaveTextContent('(1)');
  });

  it('category row carries Top 3 by_package title scoped to that category', () => {
    // 给 AEE 加多个有 crash_count 的包,验证 Top 3 排序仅看 crash_count
    const breakdownTop3: AeeBreakdown = {
      crash_count: 6,
      vendor_crash_count: 0,
      anr_count: 0,
      packages: ['com.a', 'com.b', 'com.c', 'com.d'],
      by_package: [
        { package_name: 'com.a', crash_count: 3, vendor_crash_count: 0, anr_count: 0 },
        { package_name: 'com.b', crash_count: 2, vendor_crash_count: 0, anr_count: 0 },
        { package_name: 'com.c', crash_count: 1, vendor_crash_count: 0, anr_count: 5 },
        { package_name: 'com.d', crash_count: 0, vendor_crash_count: 0, anr_count: 9 },
      ],
    };
    render(
      <WatcherSummaryCard
        data={{ ...fixture, aee_breakdown: breakdownTop3 }}
      />,
    );
    const aeeRow = screen.getByTestId('watcher-cat-AEE');
    // 仅按 crash_count 降序,排除 0,取 Top 3 → com.a / com.b / com.c
    expect(aeeRow).toHaveAttribute(
      'title',
      'Top 3 应用: com.a (3), com.b (2), com.c (1)',
    );
    // ANR 行按 anr_count 排序 → com.d (9), com.c (5)
    const anrRow = screen.getByTestId('watcher-cat-ANR');
    expect(anrRow).toHaveAttribute(
      'title',
      'Top 3 应用: com.d (9), com.c (5)',
    );
    // TOMBSTONE 无映射字段 → 不挂 title
    const tombRow = screen.getByTestId('watcher-cat-TOMBSTONE');
    expect(tombRow).not.toHaveAttribute('title');
  });

  it('hides chips + packages row when aee_breakdown is null', () => {
    render(
      <WatcherSummaryCard
        data={{ ...fixture, aee_breakdown: null }}
      />,
    );
    expect(screen.queryByTestId('watcher-aee-summary')).not.toBeInTheDocument();
    expect(screen.queryByTestId('watcher-packages-row')).not.toBeInTheDocument();
    // category 行不再附带 Top 3 title
    expect(screen.getByTestId('watcher-cat-AEE')).not.toHaveAttribute('title');
  });

  it('omits AeeBreakdownChips when crash=0 and anr=0 (defensive)', () => {
    render(
      <WatcherSummaryCard
        data={{
          ...fixture,
          aee_breakdown: {
            crash_count: 0,
            vendor_crash_count: 0,
            anr_count: 0,
            packages: [],
            by_package: [],
          },
        }}
      />,
    );
    expect(screen.queryByTestId('watcher-aee-summary')).not.toBeInTheDocument();
    // packages 为空也不渲染 row
    expect(screen.queryByTestId('watcher-packages-row')).not.toBeInTheDocument();
  });

  it('does not render legacy transition badge in watcher mainline mode', () => {
    render(<WatcherSummaryCard data={fixture as WatcherSummary} />);
    fireEvent.click(screen.getByTestId('watcher-details-toggle'));
    expect(screen.queryByTestId('watcher-dual-write-badge')).not.toBeInTheDocument();
  });

  // ----------------------------------------------------------------------
  // M0/C-6 (§2.4 #5): watcher_capability 单通道降级徽章
  // ----------------------------------------------------------------------

  it('renders unavailable badge when watcher_capability is unavailable', () => {
    render(
      <WatcherSummaryCard
        data={{ ...fixture, watcher_capability: 'unavailable' }}
      />,
    );
    fireEvent.click(screen.getByTestId('watcher-details-toggle'));
    const badge = screen.getByTestId('watcher-capability-badge');
    expect(badge).toHaveAttribute('data-capability', 'unavailable');
    expect(badge).toHaveTextContent('Watcher 不可用');
    const title = badge.getAttribute('title') ?? '';
    expect(title).toMatch(/Watcher 未正常启动/);
    expect(title).not.toMatch(/单通道/);
  });

  it('does not render capability badge for normal capability (inotifyd_realtime)', () => {
    render(
      <WatcherSummaryCard
        data={{ ...fixture, watcher_capability: 'inotifyd_realtime' }}
      />,
    );
    expect(
      screen.queryByTestId('watcher-capability-badge'),
    ).not.toBeInTheDocument();
  });

  it('does not render capability badge when watcher_capability is absent/null', () => {
    render(
      <WatcherSummaryCard data={{ ...fixture, watcher_capability: null }} />,
    );
    expect(
      screen.queryByTestId('watcher-capability-badge'),
    ).not.toBeInTheDocument();
  });
});
