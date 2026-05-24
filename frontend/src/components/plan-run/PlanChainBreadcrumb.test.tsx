import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import PlanChainBreadcrumb from './PlanChainBreadcrumb';
import type { PlanChain } from '@/utils/api/types';

const fixture: PlanChain = {
  plan_run_id: 12,
  root_plan_run_id: 9,
  nodes: [
    {
      plan_id: 5,
      plan_name: '基础校验',
      plan_run_id: 9,
      status: 'SUCCESS',
      chain_index: 0,
      duration_seconds: 64,
      failure_threshold: 0.1,
      pass_rate: 1.0,
      is_current: false,
      is_blocked: false,
    },
    {
      plan_id: 7,
      plan_name: '24h 烧机',
      plan_run_id: 12,
      status: 'RUNNING',
      chain_index: 1,
      failure_threshold: 0.05,
      pass_rate: 0.95,
      is_current: true,
      is_blocked: false,
    },
    {
      plan_id: 11,
      plan_name: '后置回收',
      plan_run_id: null,
      status: 'pending',
      chain_index: 2,
      failure_threshold: 0.1,
      is_current: false,
      is_blocked: true,
      block_reason: '上游 PlanRun #12 未达到 SUCCESS/PARTIAL_SUCCESS',
    },
  ],
};

describe('PlanChainBreadcrumb', () => {
  it('renders all chain nodes with current highlight and pass rate', () => {
    render(<PlanChainBreadcrumb chain={fixture} />);
    expect(screen.getByTestId('chain-node-5')).toHaveTextContent('基础校验');
    expect(screen.getByTestId('chain-node-7')).toHaveTextContent('24h 烧机');
    expect(screen.getByTestId('chain-node-7')).toHaveTextContent('当前');
    expect(screen.getByTestId('chain-node-11')).toHaveTextContent('暂不触发');
  });

  it('navigates only to runs that have a plan_run_id and are not current', () => {
    const onNav = vi.fn();
    render(<PlanChainBreadcrumb chain={fixture} onNavigateRun={onNav} />);

    // Click historic node (#5) → navigates
    fireEvent.click(screen.getByTestId('chain-node-5'));
    expect(onNav).toHaveBeenCalledWith(9);

    // Click current node (#7) → should NOT navigate (no role="button")
    fireEvent.click(screen.getByTestId('chain-node-7'));
    expect(onNav).toHaveBeenCalledTimes(1);

    // Click pending node (#11) → no plan_run_id, should NOT navigate
    fireEvent.click(screen.getByTestId('chain-node-11'));
    expect(onNav).toHaveBeenCalledTimes(1);
  });

  it('shows loading + empty state placeholders', () => {
    const { rerender } = render(<PlanChainBreadcrumb chain={undefined} isLoading />);
    expect(screen.getByTestId('plan-chain-loading')).toBeInTheDocument();
    rerender(<PlanChainBreadcrumb chain={{ plan_run_id: 1, root_plan_run_id: 1, nodes: [] }} />);
    expect(screen.getByTestId('plan-chain-empty')).toBeInTheDocument();
  });

  it('shows chain dispatch failure banner with error detail', () => {
    render(
      <PlanChainBreadcrumb
        chain={fixture}
        chainDispatchFailed={{
          at: '2026-05-08T13:00:00Z',
          error: 'devices unavailable',
        }}
      />,
    );
    const banner = screen.getByTestId('chain-dispatch-failed-banner');
    expect(banner).toHaveTextContent('下游 Plan 派发失败');
    expect(banner).toHaveTextContent('devices unavailable');
    expect(banner).toHaveTextContent('手动触发');
    expect(banner).not.toHaveTextContent('自动重试');
  });
});
