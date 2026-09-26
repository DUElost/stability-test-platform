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

  // #3350（ADR-0023 D3）：step 行显示 script_name@version（口径统一走 scriptIdentity）
  it('渲染每个 step 的脚本身份 chip', () => {
    const tl = makeTimeline([
      makeStage({
        stage: 'init',
        status: 'completed',
        steps: [
          {
            step_key: 'prepare', script_name: 'device_prepare', script_version: '1.2.3',
            stage: 'init', sort_order: 1, device_total: 1, device_succeeded: 1,
            device_failed: 0, device_running: 0,
          },
        ],
      }),
    ]);
    render(<BusinessFlowStepper timeline={tl} />);
    expect(screen.getByTestId('stage-step-script-init-prepare')).toHaveTextContent(
      'device_prepare@1.2.3',
    );
  });

  it('step 缺版本（旧快照）只显示 script_name；缺 script_name 不渲染 chip', () => {
    const tl = makeTimeline([
      makeStage({
        stage: 'init',
        status: 'pending',
        steps: [
          {
            step_key: 'legacy', script_name: 'legacy_script', script_version: null,
            stage: 'init', sort_order: 1, device_total: 0, device_succeeded: 0,
            device_failed: 0, device_running: 0,
          },
          {
            step_key: 'ghost', script_name: '', script_version: null,
            stage: 'init', sort_order: 2, device_total: 0, device_succeeded: 0,
            device_failed: 0, device_running: 0,
          },
        ],
      }),
    ]);
    render(<BusinessFlowStepper timeline={tl} />);
    expect(screen.getByTestId('stage-step-script-init-legacy')).toHaveTextContent('legacy_script');
    expect(screen.queryByTestId('stage-step-script-init-ghost')).not.toBeInTheDocument();
  });
});
