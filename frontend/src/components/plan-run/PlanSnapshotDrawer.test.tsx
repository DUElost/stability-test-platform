import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import PlanSnapshotDrawer from './PlanSnapshotDrawer';
import type { PlanSnapshot } from '@/utils/api/types';

/** #3350（ADR-0023 D4）：快照浏览抽屉——排序 / 字段 / 展开 / 深链 / 空态。 */

const SNAPSHOT: PlanSnapshot = {
  schema_version: 1,
  plan: {
    id: 7,
    name: '周期回归-GPU',
    patrol_interval_seconds: 1800,
    timeout_seconds: 7200,
    watcher_policy: { enabled: true },
  },
  steps: [
    {
      stage: 'teardown', step_key: 'cleanup', script_name: 'clear_recents', script_version: '1.0.4',
      nfs_path: '/nfs/clear_recents', param_schema: { type: 'object' }, default_params: { keep: 1 },
      timeout_seconds: 300, retry: 0, enabled: true, sort_order: 2,
    },
    {
      stage: 'init', step_key: 'prepare', script_name: 'device_prepare', script_version: '1.2.3',
      nfs_path: '/nfs/device_prepare', param_schema: { type: 'object' }, default_params: { mode: 'fast' },
      timeout_seconds: 600, retry: 1, enabled: true, sort_order: 1,
    },
  ],
} as PlanSnapshot;

const render_ = (ui: React.ReactElement) => render(ui, { wrapper: MemoryRouter });

describe('PlanSnapshotDrawer', () => {
  it('open=false 不渲染；打开后渲染步骤卡片', () => {
    const first = render_(<PlanSnapshotDrawer open={false} onClose={vi.fn()} snapshot={SNAPSHOT} />);
    expect(screen.queryByTestId('plan-snapshot-drawer')).not.toBeInTheDocument();
    first.unmount();

    render_(<PlanSnapshotDrawer open onClose={vi.fn()} snapshot={SNAPSHOT} />);
    expect(screen.getByTestId('plan-snapshot-drawer')).toBeInTheDocument();
    expect(screen.getByTestId('snapshot-step-init-prepare')).toBeInTheDocument();
    expect(screen.getByTestId('snapshot-step-teardown-cleanup')).toBeInTheDocument();
  });

  it('步骤按 (stage, sort_order) 排序：init 在 teardown 之前', () => {
    render_(<PlanSnapshotDrawer open onClose={vi.fn()} snapshot={SNAPSHOT} />);
    const cards = screen.getAllByText(/^(prepare|cleanup)$/);
    expect(cards[0]).toHaveTextContent('prepare');
    expect(cards[1]).toHaveTextContent('cleanup');
  });

  it('每行显示 script_name@version 并深链到脚本库（含 version query）', () => {
    render_(<PlanSnapshotDrawer open onClose={vi.fn()} snapshot={SNAPSHOT} />);
    const link = screen.getByText('device_prepare@1.2.3').closest('a');
    expect(link).toHaveAttribute('href', '/script-management?name=device_prepare&version=1.2.3');
  });

  it('展开后显示 default_params 与 param_schema', () => {
    render_(<PlanSnapshotDrawer open onClose={vi.fn()} snapshot={SNAPSHOT} />);
    expect(screen.queryByTestId('snapshot-step-body-init-prepare')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('snapshot-step-toggle-init-prepare'));
    const body = screen.getByTestId('snapshot-step-body-init-prepare');
    expect(body).toHaveTextContent('default_params');
    expect(body).toHaveTextContent('"mode": "fast"');
    expect(body).toHaveTextContent('param_schema');
  });

  it('快照缺失/无 steps → 空态而不是报错', () => {
    render_(<PlanSnapshotDrawer open onClose={vi.fn()} snapshot={null} />);
    expect(screen.getByTestId('plan-snapshot-empty')).toBeInTheDocument();
  });

  it('关闭按钮触发 onClose', () => {
    const onClose = vi.fn();
    render_(<PlanSnapshotDrawer open onClose={onClose} snapshot={SNAPSHOT} />);
    fireEvent.click(screen.getByTestId('plan-snapshot-close'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
