import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PlanFailedDevicesChart } from './PlanFailedDevicesChart';
import type { PlanFailedDevicesItem } from '@/utils/api/types';

const items: PlanFailedDevicesItem[] = [
  { plan_id: 1, plan_name: 'stable-plan', total_jobs: 10, failed: 0 },
  { plan_id: 2, plan_name: 'pressured-plan', total_jobs: 10, failed: 4 },
];

describe('PlanFailedDevicesChart (ADR-0048)', () => {
  it('renders skeleton while loading', () => {
    const { container } = render(<PlanFailedDevicesChart data={[]} isLoading />);
    expect(container.querySelector('[class*="animate-pulse"]')).toBeInTheDocument();
  });

  it('renders empty state without crash records', () => {
    render(<PlanFailedDevicesChart data={[]} isLoading={false} />);
    expect(screen.getByText('方案失败设备数排行 (30d)')).toBeInTheDocument();
    expect(screen.getByText('近 30 天无失败设备记录')).toBeInTheDocument();
  });

  it('renders title without crashing when data is present', () => {
    render(<PlanFailedDevicesChart data={items} isLoading={false} />);
    expect(screen.getByText('方案失败设备数排行 (30d)')).toBeInTheDocument();
    expect(screen.queryByText('近 30 天无失败设备记录')).not.toBeInTheDocument();
  });

  it('treats undefined data the same as empty', () => {
    render(<PlanFailedDevicesChart isLoading={false} />);
    expect(screen.getByText('近 30 天无失败设备记录')).toBeInTheDocument();
  });
});
