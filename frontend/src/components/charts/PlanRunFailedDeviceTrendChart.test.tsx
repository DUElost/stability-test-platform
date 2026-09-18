import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PlanRunFailedDeviceTrendChart } from './PlanRunFailedDeviceTrendChart';
import type { PlanRunFailedDevicePoint } from '@/utils/api/types';

const points: PlanRunFailedDevicePoint[] = [
  { date: '2026-06-01', failed_devices: 4, run_count: 3 },
  { date: '2026-06-02', failed_devices: 2, run_count: 2 },
];

describe('PlanRunFailedDeviceTrendChart', () => {
  it('renders skeleton while loading', () => {
    const { container } = render(<PlanRunFailedDeviceTrendChart data={[]} isLoading />);
    expect(container.querySelector('[class*="animate-pulse"]')).toBeInTheDocument();
  });

  it('renders empty state when there is no data', () => {
    render(<PlanRunFailedDeviceTrendChart data={[]} isLoading={false} />);
    expect(screen.getByText('失败设备数趋势 (30d)')).toBeInTheDocument();
    expect(screen.getByText('暂无数据')).toBeInTheDocument();
  });

  it('renders title without crashing when data is present', () => {
    render(<PlanRunFailedDeviceTrendChart data={points} isLoading={false} />);
    expect(screen.getByText('失败设备数趋势 (30d)')).toBeInTheDocument();
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument();
  });

  it('treats undefined data the same as empty', () => {
    render(<PlanRunFailedDeviceTrendChart isLoading={false} />);
    expect(screen.getByText('暂无数据')).toBeInTheDocument();
  });
});
