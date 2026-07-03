import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { HostFailureRateChart } from './HostFailureRateChart';
import type { HostFailureRateItem } from '@/utils/api/types';

const items: HostFailureRateItem[] = [
  { host_id: 'h-1', hostname: 'host-alpha', ip_address: '10.0.0.1', total_jobs: 10, failed: 4, failure_rate: 0.4 },
  { host_id: 'h-2', hostname: 'host-beta', ip_address: '10.0.0.2', total_jobs: 20, failed: 1, failure_rate: 0.05 },
];

describe('HostFailureRateChart', () => {
  it('renders skeleton while loading', () => {
    const { container } = render(<HostFailureRateChart data={[]} isLoading />);
    expect(container.querySelector('[class*="animate-pulse"]')).toBeInTheDocument();
  });

  it('renders empty state when there is no data', () => {
    render(<HostFailureRateChart data={[]} isLoading={false} />);
    expect(screen.getByText('节点失败率排行 (30d)')).toBeInTheDocument();
    expect(screen.getByText('暂无数据')).toBeInTheDocument();
  });

  it('renders title without crashing when data is present', () => {
    render(<HostFailureRateChart data={items} isLoading={false} />);
    expect(screen.getByText('节点失败率排行 (30d)')).toBeInTheDocument();
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument();
  });

  it('treats undefined data the same as empty', () => {
    render(<HostFailureRateChart isLoading={false} />);
    expect(screen.getByText('暂无数据')).toBeInTheDocument();
  });
});
