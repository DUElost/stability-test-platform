import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PlanStepList } from './PlanStepList';
import type { ParameterProjection } from '@/utils/api/types';

const projection: ParameterProjection = {
  layer: 'L1',
  context: { read_at: '2026-10-10T00:00:00.000000Z', authority: 'test' },
  steps: [],
  plan_settings: [{
    path: ['timeout_seconds'],
    label: '巡检时长',
    meaning: '整次执行墙钟',
    state: 'explicit',
    source: 'step_override',
    value: 120,
    sensitive: false,
    is_set: true,
  }],
  watcher_policy: { note: '独立策略', items: [] },
  dispatch_decisions: [],
  safe_debug: { steps: [], plan_settings: { raw: 'RAW_JSON_SENTINEL' }, watcher_policy: {} },
};

describe('PlanStepList', () => {
  it('renders the shared projection and not a default_params dump', () => {
    render(<PlanStepList projection={projection} />);
    expect(screen.getByTestId('plan-step-list')).toBeInTheDocument();
    expect(screen.getByTestId('parameter-projection-list')).toBeInTheDocument();
    expect(screen.getByText('整次执行墙钟')).toBeInTheDocument();
    expect(screen.queryByText(/default_params/)).not.toBeInTheDocument();
    expect(screen.queryByText('RAW_JSON_SENTINEL')).not.toBeInTheDocument();
  });

  it('shows the unavailable copy instead of raw parameters', () => {
    render(<PlanStepList projection={projection} unavailable />);
    expect(screen.getByTestId('parameter-projection-unavailable')).toHaveTextContent(
      '没有安全投影时不展示原始参数',
    );
    expect(screen.queryByText('整次执行墙钟')).not.toBeInTheDocument();
  });
});
