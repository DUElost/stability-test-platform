import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PlanRunPassRateTrendChart } from './PlanRunPassRateTrendChart';
import type { PlanRunPassRatePoint } from '@/utils/api/types';

const points: PlanRunPassRatePoint[] = [
  { date: '2026-06-01', avg_pass_rate: 0.8, run_count: 3 },
  { date: '2026-06-02', avg_pass_rate: 0.5, run_count: 2 },
];

describe('PlanRunPassRateTrendChart', () => {
  it('renders skeleton while loading', () => {
    const { container } = render(<PlanRunPassRateTrendChart data={[]} isLoading />);
    expect(container.querySelector('[class*="animate-pulse"]')).toBeInTheDocument();
  });

  it('renders empty state when there is no data', () => {
    render(<PlanRunPassRateTrendChart data={[]} isLoading={false} />);
    expect(screen.getByText('运行通过率趋势 (30d)')).toBeInTheDocument();
    expect(screen.getByText('暂无数据')).toBeInTheDocument();
  });

  it('renders title without crashing when data is present', () => {
    render(<PlanRunPassRateTrendChart data={points} isLoading={false} />);
    expect(screen.getByText('运行通过率趋势 (30d)')).toBeInTheDocument();
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument();
  });

  it('treats undefined data the same as empty', () => {
    render(<PlanRunPassRateTrendChart isLoading={false} />);
    expect(screen.getByText('暂无数据')).toBeInTheDocument();
  });
});
