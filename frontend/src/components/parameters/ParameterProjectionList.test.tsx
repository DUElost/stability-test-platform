import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ParameterProjectionList } from './ParameterProjectionList';
import type { ParameterItem, ParameterProjection, ParameterState } from '@/utils/api/types';

const STATES: ParameterState[] = [
  'explicit',
  'unset_definite',
  'env_fallback',
  'pending_dispatch',
  'actual',
];

function item(partial: Partial<ParameterItem> & Pick<ParameterItem, 'path' | 'label' | 'state'>): ParameterItem {
  return {
    meaning: `${partial.label}的含义`,
    sensitive: false,
    is_set: partial.value != null,
    source: 'step_override',
    ...partial,
  };
}

function projection(overrides: Partial<ParameterProjection> = {}): ParameterProjection {
  return {
    layer: 'L1',
    context: { read_at: '2026-10-10T00:00:00.000000Z', authority: 'test' },
    steps: [{
      step_key: 'check',
      script_name: 'check_device',
      script_version: '1.0.0',
      stage: 'init',
      sort_order: 0,
      enabled: true,
      executes: true,
      metadata_missing: false,
      params: [
        item({
          path: ['password'],
          label: 'WiFi 密码',
          state: 'explicit',
          source: 'step_override',
          sensitive: true,
          is_set: true,
          value: 'SENTINEL_PASSWORD',
        }),
      ],
      settings: [
        item({
          path: ['timeout_seconds'],
          label: '墙钟上限',
          state: 'env_fallback',
          source: null,
          value: null,
          is_set: false,
          fallback_chain: 'Plan → STP_STEP_WALL_CLOCK_SECONDS → 300',
        }),
      ],
    }],
    plan_settings: STATES.map((state) => item({
      path: [state],
      label: `状态-${state}`,
      state,
      source: state === 'explicit' ? 'schema_default' : state === 'unset_definite' ? 'script_default' : state === 'pending_dispatch' ? 'dispatch_injection' : null,
      value: state === 'explicit' ? '确定值' : null,
      is_set: state === 'explicit',
    })),
    watcher_policy: {
      note: '异常采集策略独立于步骤参数',
      items: [item({
        path: ['watcher', 'enabled'],
        label: '采集开关',
        state: 'explicit',
        source: 'schema_default',
        value: true,
      })],
    },
    dispatch_decisions: [{
      kind: 'wifi',
      state: 'pending_dispatch',
      factors: ['资源池', '设备主机'],
    }],
    safe_debug: {
      steps: [{ raw: 'RAW_JSON_SENTINEL' }],
      plan_settings: { secret: 'RAW_JSON_SENTINEL' },
      watcher_policy: { secret: 'RAW_JSON_SENTINEL' },
    },
    ...overrides,
  };
}

describe('ParameterProjectionList', () => {
  it('shows value or state, source, layer, and meaning for each of the five states', () => {
    render(<ParameterProjectionList projection={projection()} />);
    expect(screen.getByTestId('parameter-layer')).toHaveTextContent('L1');
    const labels: Record<ParameterState, string> = {
      explicit: '已确定',
      unset_definite: '未设置',
      env_fallback: '沿回落链',
      pending_dispatch: '待派发确定',
      actual: '已下发',
    };
    for (const state of STATES) {
      const row = screen.getByText(`状态-${state}`).closest('[data-testid="parameter-item"]');
      expect(row).not.toBeNull();
      const view = within(row as HTMLElement);
      expect(view.getByText(`${`状态-${state}`}的含义`)).toBeInTheDocument();
      expect(view.getByTestId('parameter-value')).toHaveTextContent(
        state === 'explicit' ? '确定值' : labels[state],
      );
      expect(view.getAllByText(labels[state]).length).toBeGreaterThan(0);
      expect(view.getByText('L1')).toBeInTheDocument();
    }
    expect(screen.getAllByText('模式默认').length).toBeGreaterThan(0);
    expect(screen.getAllByText('脚本默认').length).toBeGreaterThan(0);
    expect(screen.getAllByText('派发填入').length).toBeGreaterThan(0);
    expect(screen.getAllByText('来源不可追溯').length).toBeGreaterThan(0);
  });

  it('shows only whether a sensitive value is set', () => {
    render(<ParameterProjectionList projection={projection()} />);
    const row = screen.getByText('WiFi 密码').closest('[data-testid="parameter-item"]');
    expect(within(row as HTMLElement).getByTestId('parameter-value')).toHaveTextContent('已设置');
    expect(screen.queryByText('SENTINEL_PASSWORD')).not.toBeInTheDocument();
    expect(screen.queryByText(/\*\*\*/)).not.toBeInTheDocument();
  });

  it('keeps an env fallback number out of the value cell', () => {
    render(<ParameterProjectionList projection={projection()} />);
    const row = screen.getByText('墙钟上限').closest('[data-testid="parameter-item"]');
    const view = within(row as HTMLElement);
    expect(view.getByTestId('parameter-value')).toHaveTextContent('沿回落链');
    expect(view.getByTestId('parameter-value')).not.toHaveTextContent('300');
    expect(view.getByTestId('parameter-fallback')).toHaveTextContent('300');
  });

  it('keeps watcher policy and pending dispatch factors in their own sections', () => {
    render(<ParameterProjectionList projection={projection()} />);
    expect(within(screen.getByTestId('parameter-watcher')).getByText('异常采集策略独立于步骤参数')).toBeInTheDocument();
    const pending = screen.getByTestId('parameter-dispatch-decisions');
    expect(within(pending).getByText('wifi')).toBeInTheDocument();
    expect(within(pending).getByText('待派发确定')).toBeInTheDocument();
    expect(within(pending).getByText('资源池、设备主机')).toBeInTheDocument();
    expect(screen.queryByText('RAW_JSON_SENTINEL')).not.toBeInTheDocument();
  });

  it('does not fall back to raw parameters when the projection is missing or failed', () => {
    const { rerender } = render(
      <ParameterProjectionList projection={projection()} unavailable />,
    );
    expect(screen.getByTestId('parameter-projection-unavailable')).toHaveTextContent('没有安全投影时不展示原始参数');
    expect(screen.queryByText('SENTINEL_PASSWORD')).not.toBeInTheDocument();
    expect(screen.queryByText('WiFi 密码')).not.toBeInTheDocument();
    expect(screen.queryByText('RAW_JSON_SENTINEL')).not.toBeInTheDocument();

    rerender(<ParameterProjectionList projection={null} />);
    expect(screen.getByTestId('parameter-projection-unavailable')).toHaveTextContent('没有安全投影时不展示原始参数');
  });
});
