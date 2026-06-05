import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import BusinessFlowStepper from './BusinessFlowStepper';
import type { PlanRunTimeline, TimelineStage } from '@/utils/api/types';

const makeStage = (overrides: Partial<TimelineStage>): TimelineStage => ({
  stage: 'init',
  status: 'pending',
  device_total: 0,
  device_succeeded: 0,
  device_failed: 0,
  steps: [],
  ...overrides,
});

const makeTimeline = (stages: TimelineStage[]): PlanRunTimeline => ({
  plan_run_id: 1,
  current_stage: 'init',
  stages,
  triggered_at: '2026-01-01T00:00:00Z',
  run_type: 'MANUAL',
} as PlanRunTimeline);

describe('BusinessFlowStepper', () => {
  it('renders 3 stage nodes', () => {
    render(<BusinessFlowStepper />);
    expect(screen.getByTestId('stage-node-init')).toBeTruthy();
    expect(screen.getByTestId('stage-node-patrol')).toBeTruthy();
    expect(screen.getByTestId('stage-node-teardown')).toBeTruthy();
  });

  it('shows loading state', () => {
    render(<BusinessFlowStepper isLoading />);
    expect(screen.getByText('加载中…')).toBeTruthy();
  });

  it('shows error state', () => {
    render(<BusinessFlowStepper isError />);
    expect(screen.getByText('加载失败')).toBeTruthy();
  });

  it('marks a running patrol stage with cycle index', () => {
    const tl = makeTimeline([
      makeStage({ stage: 'init', status: 'completed', device_succeeded: 5 }),
      makeStage({
        stage: 'patrol',
        status: 'running',
        patrol_active_devices: 3,
        device_succeeded: 2,
        patrol_cycle_index: 2,
      }),
    ]);
    render(<BusinessFlowStepper timeline={tl} />);
    expect(screen.getByTestId('stage-node-patrol').textContent).toContain('周期 #2');
  });

  it('marks a completed stage', () => {
    const tl = makeTimeline([
      makeStage({ stage: 'init', status: 'completed', device_succeeded: 5 }),
    ]);
    render(<BusinessFlowStepper timeline={tl} />);
    expect(screen.getByTestId('stage-node-init')).toBeTruthy();
  });
});
