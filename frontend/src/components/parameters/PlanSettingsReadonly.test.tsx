import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PlanSettingsReadonly } from './PlanSettingsReadonly';
import type { ParameterProjection } from '@/utils/api/types';

const projection: ParameterProjection = {
  layer: 'L1', context: { read_at: '', authority: '测试服务端' }, steps: [],
  plan_settings: [{ path: ['barrier_max_wait_seconds'], label: '绝对硬顶', meaning: '留空继承主机策略',
    state: 'env_fallback', value: null, sensitive: false, is_set: false, fallback_chain: 'STP_BARRIER_MAX_WAIT_SECONDS → 1800s' }],
  watcher_policy: { note: '主机覆盖待派发', items: [] }, dispatch_decisions: [],
  safe_debug: { steps: [], plan_settings: {}, watcher_policy: {} },
};
it('设置与采集策略只读、env 回落不当作确定数字', () => {
  const { container } = render(<PlanSettingsReadonly projection={projection} isDraft />);
  expect(screen.getByLabelText('计划设置只读区')).toHaveTextContent('L1草稿，未保存');
  expect(screen.getByText('留空继承主机策略')).toBeVisible();
  expect(screen.getByText(/STP_BARRIER_MAX_WAIT_SECONDS/)).toBeVisible();
  expect(screen.getByTestId('parameter-value')).toHaveTextContent('沿回落链');
  expect(container.querySelector('input,select,textarea,button')).toBeNull();
});
describe('投影空态', () => {
  it('不显示猜测默认值', () => {
    render(<PlanSettingsReadonly unavailable />);
    expect(screen.getByTestId('parameter-projection-unavailable')).toHaveTextContent('不可用');
    expect(screen.queryByText('1800')).not.toBeInTheDocument();
  });
});
