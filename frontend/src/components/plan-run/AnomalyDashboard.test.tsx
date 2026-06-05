import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import AnomalyDashboard from './AnomalyDashboard';
import type { WatcherSummary } from '@/utils/api/types';

const makeData = (overrides: Partial<WatcherSummary> = {}): WatcherSummary => ({
  plan_run_id: 1,
  window_minutes: 60,
  window_start_at: '2026-06-05T00:00:00Z',
  window_end_at: '2026-06-05T01:00:00Z',
  abnormal_rate: 0,
  exceeded: false,
  categories: [],
  total: 0,
  affected_device_count: 0,
  total_devices: 10,
  threshold: 0.3,
  ...overrides,
} as WatcherSummary);

describe('AnomalyDashboard', () => {
  it('shows loading state', () => {
    render(<AnomalyDashboard isLoading />);
    expect(screen.getByText('加载中…')).toBeTruthy();
  });

  it('shows error state', () => {
    render(<AnomalyDashboard isError />);
    expect(screen.getByText('加载失败')).toBeTruthy();
  });

  it('shows empty message when no categories', () => {
    render(<AnomalyDashboard data={makeData()} />);
    expect(screen.getByText(/当前时间窗内暂无异常事件/)).toBeTruthy();
  });

  it('shows exceeded banner when exceeded', () => {
    render(<AnomalyDashboard data={makeData({ exceeded: true, abnormal_rate: 0.8 })} />);
    expect(screen.getByText(/异常率超阈值/)).toBeTruthy();
  });

  it('renders category rows', () => {
    render(
      <AnomalyDashboard
        data={makeData({
          categories: [
            {
              category: 'AEE',
              count: 5,
              trend_change: 1,
              latest_device_serial: 'A1B2C3D4',
              affected_device_count: 2,
            },
          ],
        })}
      />
    );
    expect(screen.getByText('AEE 崩溃')).toBeTruthy();
    expect(screen.getByText('5')).toBeTruthy();
  });

  it('calls onWindowChange when button clicked', () => {
    const fn = vi.fn();
    render(<AnomalyDashboard data={makeData()} onWindowChange={fn} windowMinutes={60} />);
    fireEvent.click(screen.getByText('15m'));
    expect(fn).toHaveBeenCalledWith(15);
  });
});
