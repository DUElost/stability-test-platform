import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PlanSuccessRateChart } from './PlanSuccessRateChart';
import type { PlanSuccessRateItem } from '@/utils/api/types';

const items: PlanSuccessRateItem[] = [
  { plan_id: 1, plan_name: 'smoke-plan', total_jobs: 10, passed: 10, failed: 0, pass_rate: 1.0 },
  { plan_id: 2, plan_name: 'flaky-plan', total_jobs: 10, passed: 6, failed: 4, pass_rate: 0.6 },
];

describe('PlanSuccessRateChart', () => {
  it('renders skeleton while loading', () => {
    const { container } = render(<PlanSuccessRateChart data={[]} isLoading />);
    expect(container.querySelector('[class*="animate-pulse"]')).toBeInTheDocument();
  });

  it('renders empty state when there is no data', () => {
    render(<PlanSuccessRateChart data={[]} isLoading={false} />);
    expect(screen.getByText('方案成功率排行 (30d)')).toBeInTheDocument();
    expect(screen.getByText('暂无数据')).toBeInTheDocument();
  });

  it('renders title without crashing when data is present', () => {
    render(<PlanSuccessRateChart data={items} isLoading={false} />);
    expect(screen.getByText('方案成功率排行 (30d)')).toBeInTheDocument();
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument();
  });

  it('treats undefined data the same as empty', () => {
    render(<PlanSuccessRateChart isLoading={false} />);
    expect(screen.getByText('暂无数据')).toBeInTheDocument();
  });
});
