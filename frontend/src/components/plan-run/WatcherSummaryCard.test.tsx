import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import WatcherSummaryCard from './WatcherSummaryCard';
import type { WatcherSummary } from '@/utils/api/types';

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
});
