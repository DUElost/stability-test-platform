import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import PlanSnapshotDrawer from './PlanSnapshotDrawer';
import type {
  DeviceMatrixItem,
  ParameterItem,
  ParameterProjection,
  ParameterState,
  PlanSnapshot,
} from '@/utils/api/types';

/** #3350 + #3653 U4：快照身份 + L2/L3 安全投影，不渲染原始参数 JSON。 */

const SNAPSHOT: PlanSnapshot = {
  schema_version: 1,
  plan: {
    id: 7,
    name: '周期回归-GPU',
    patrol_interval_seconds: 1800,
    timeout_seconds: 7200,
    watcher_policy: { enabled: true, secret: 'RAW_WATCHER_SENTINEL' },
  },
  steps: [
    {
      stage: 'teardown', step_key: 'cleanup', script_name: 'clear_recents', script_version: '1.0.4',
      nfs_path: '/nfs/clear_recents', param_schema: { type: 'object' }, default_params: { keep: 1, password: 'RAW_STEP_PASSWORD' },
      timeout_seconds: 300, retry: 0, enabled: true, sort_order: 2,
    },
    {
      stage: 'init', step_key: 'prepare', script_name: 'device_prepare', script_version: '1.2.3',
      nfs_path: '/nfs/device_prepare', param_schema: { type: 'object' }, default_params: { mode: 'fast', password: 'RAW_STEP_PASSWORD' },
      timeout_seconds: 600, retry: 1, enabled: true, sort_order: 1,
    },
  ],
} as PlanSnapshot;

const DEVICES: DeviceMatrixItem[] = [
  {
    device_id: 11,
    device_serial: 'DEV-A',
    host_id: 'host-a',
    job_id: 101,
    job_status: 'RUNNING',
    ui_status: 'running',
    current_stage: 'patrol',
    patrol_cycle_count: 1,
    patrol_success_cycle_count: 1,
    patrol_failed_cycle_count: 0,
    current_failure_streak: 0,
    log_signal_count: 0,
  },
  {
    device_id: 22,
    device_serial: 'DEV-B',
    host_id: 'host-b',
    job_id: 202,
    job_status: 'RUNNING',
    ui_status: 'running',
    current_stage: 'patrol',
    patrol_cycle_count: 1,
    patrol_success_cycle_count: 1,
    patrol_failed_cycle_count: 0,
    current_failure_streak: 0,
    log_signal_count: 0,
  },
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

function makeProjection(
  layer: 'L2' | 'L3',
  overrides: Partial<ParameterProjection> = {},
): ParameterProjection {
  const wifiValue = layer === 'L3' && overrides.context?.device_id === 22
    ? 'pool-b-ssid'
    : layer === 'L3'
      ? 'pool-a-ssid'
      : null;
  const wifiState: ParameterState = layer === 'L3' ? 'actual' : 'pending_dispatch';
  return {
    layer,
    context: {
      plan_id: 7,
      plan_run_id: 12,
      job_id: layer === 'L3' ? 101 : null,
      device_id: layer === 'L3' ? 11 : null,
      host_id: layer === 'L3' ? 'host-a' : null,
      read_at: '2026-10-11T00:00:00.000000Z',
      authority: layer === 'L2'
        ? 'plan_snapshot 与 run_context 冻结决定；未回读当前 PlanStep 或 Script'
        : 'claim 同源；值来自 Job.pipeline_def 与 ResourceAllocation',
    },
    steps: [{
      step_key: 'prepare',
      script_name: 'device_prepare',
      script_version: '1.2.3',
      stage: 'init',
      sort_order: 0,
      enabled: true,
      executes: true,
      metadata_missing: false,
      params: [
        item({
          path: ['wifi', 'ssid'],
          label: 'WiFi SSID',
          state: wifiState,
          source: layer === 'L3' ? 'dispatch_injection' : 'dispatch_injection',
          value: wifiValue,
          is_set: wifiValue != null,
        }),
        item({
          path: ['password'],
          label: 'WiFi 密码',
          state: 'explicit',
          sensitive: true,
          is_set: true,
          value: 'SHOULD_NOT_RENDER',
        }),
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
      settings: [],
    }],
    plan_settings: [
      item({
        path: ['barrier_max_wait_seconds'],
        label: 'barrier 硬顶',
        state: 'env_fallback',
        source: null,
        value: null,
        is_set: false,
        fallback_chain: 'Plan → STP_BARRIER_MAX_WAIT_SECONDS → 1800',
      }),
    ],
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
    dispatch_decisions: layer === 'L2'
      ? [{ kind: 'wifi', state: 'pending_dispatch', factors: ['尚未选择资源池'] }]
      : [{
          kind: 'wifi',
          state: 'explicit',
          job_id: overrides.context?.job_id ?? 101,
          device_id: overrides.context?.device_id ?? 11,
          host_id: overrides.context?.host_id ?? 'host-a',
          factors: ['WiFi 取该 job 的 ResourceAllocation，不读当前池配置'],
        }],
    safe_debug: {
      steps: [{ params: { password: '***' } }],
      plan_settings: { barrier_max_wait_seconds: null },
      watcher_policy: { enabled: true },
    },
    ...overrides,
  };
}

const render_ = (ui: React.ReactElement) => render(ui, { wrapper: MemoryRouter });

describe('PlanSnapshotDrawer', () => {
  it('open=false 不渲染；打开后渲染步骤身份与 L2 投影', () => {
    const first = render_(
      <PlanSnapshotDrawer
        open={false}
        onClose={vi.fn()}
        snapshot={SNAPSHOT}
        l2Projection={makeProjection('L2')}
      />,
    );
    expect(screen.queryByTestId('plan-snapshot-drawer')).not.toBeInTheDocument();
    first.unmount();

    render_(
      <PlanSnapshotDrawer
        open
        onClose={vi.fn()}
        snapshot={SNAPSHOT}
        l2Projection={makeProjection('L2')}
      />,
    );
    expect(screen.getByTestId('plan-snapshot-drawer')).toBeInTheDocument();
    expect(screen.getByTestId('snapshot-step-init-prepare')).toBeInTheDocument();
    expect(screen.getByTestId('snapshot-step-teardown-cleanup')).toBeInTheDocument();
    expect(screen.getByTestId('parameter-layer')).toHaveTextContent('L2');
  });

  it('步骤按 (stage, sort_order) 排序：init 在 teardown 之前', () => {
    render_(
      <PlanSnapshotDrawer
        open
        onClose={vi.fn()}
        snapshot={SNAPSHOT}
        l2Projection={makeProjection('L2')}
      />,
    );
    const cards = screen.getAllByText(/^(prepare|cleanup)$/);
    expect(cards[0]).toHaveTextContent('prepare');
    expect(cards[1]).toHaveTextContent('cleanup');
  });

  it('每行显示 script_name@version 并深链到脚本库（含 version query）', () => {
    render_(
      <PlanSnapshotDrawer
        open
        onClose={vi.fn()}
        snapshot={SNAPSHOT}
        l2Projection={makeProjection('L2')}
      />,
    );
    const link = screen.getByText('device_prepare@1.2.3').closest('a');
    expect(link).toHaveAttribute('href', '/script-management?name=device_prepare&version=1.2.3');
  });

  it('不渲染原始 default_params / param_schema / watcher JSON；敏感清单与 safe JSON 同掩码', () => {
    render_(
      <PlanSnapshotDrawer
        open
        onClose={vi.fn()}
        snapshot={SNAPSHOT}
        l2Projection={makeProjection('L2')}
      />,
    );
    expect(screen.queryByText('default_params')).not.toBeInTheDocument();
    expect(screen.queryByText('param_schema')).not.toBeInTheDocument();
    expect(screen.queryByText('RAW_STEP_PASSWORD')).not.toBeInTheDocument();
    expect(screen.queryByText('RAW_WATCHER_SENTINEL')).not.toBeInTheDocument();
    expect(screen.queryByText('SHOULD_NOT_RENDER')).not.toBeInTheDocument();
    expect(screen.queryByText('"mode": "fast"')).not.toBeInTheDocument();

    const password = screen.getByText('WiFi 密码').closest('[data-testid="parameter-item"]');
    expect(within(password as HTMLElement).getByTestId('parameter-value')).toHaveTextContent('已设置');

    fireEvent.click(screen.getByTestId('snapshot-safe-debug-toggle'));
    const body = screen.getByTestId('snapshot-safe-debug-body');
    expect(body).toHaveTextContent('"password": "***"');
    expect(body).not.toHaveTextContent('SHOULD_NOT_RENDER');
    expect(body).not.toHaveTextContent('RAW_STEP_PASSWORD');
  });

  it('L2 清单保持 watcher 独立分区，且 env_fallback 不把默认数字填进值格', () => {
    render_(
      <PlanSnapshotDrawer
        open
        onClose={vi.fn()}
        snapshot={SNAPSHOT}
        l2Projection={makeProjection('L2')}
      />,
    );
    expect(within(screen.getByTestId('parameter-watcher')).getByText('异常采集策略独立于步骤参数')).toBeInTheDocument();
    const wall = screen.getByText('墙钟上限').closest('[data-testid="parameter-item"]');
    expect(within(wall as HTMLElement).getByTestId('parameter-value')).toHaveTextContent('沿回落链');
    expect(within(wall as HTMLElement).getByTestId('parameter-value')).not.toHaveTextContent('300');
    expect(within(wall as HTMLElement).getByTestId('parameter-fallback')).toHaveTextContent('300');
  });

  it('切换两台设备时展示各自 L3 上下文与不同下发值，不串值', () => {
    const onSelectJobId = vi.fn();
    const l3A = makeProjection('L3', {
      context: {
        plan_id: 7,
        plan_run_id: 12,
        job_id: 101,
        device_id: 11,
        host_id: 'host-a',
        read_at: '2026-10-11T01:00:00.000000Z',
        authority: 'job-a',
      },
    });
    const { rerender } = render_(
      <PlanSnapshotDrawer
        open
        onClose={vi.fn()}
        snapshot={SNAPSHOT}
        l2Projection={makeProjection('L2')}
        devices={DEVICES}
        selectedJobId={101}
        onSelectJobId={onSelectJobId}
        l3Projection={l3A}
      />,
    );
    expect(screen.getByTestId('snapshot-projection-layer')).toHaveTextContent('L3');
    expect(screen.getByTestId('snapshot-context-job')).toHaveTextContent('101');
    expect(screen.getByTestId('snapshot-context-device')).toHaveTextContent('11');
    expect(screen.getByTestId('snapshot-context-host')).toHaveTextContent('host-a');
    expect(screen.getByText('pool-a-ssid')).toBeInTheDocument();
    expect(screen.queryByText('pool-b-ssid')).not.toBeInTheDocument();

    fireEvent.change(screen.getByTestId('snapshot-job-select'), { target: { value: '202' } });
    expect(onSelectJobId).toHaveBeenCalledWith(202);

    const l3B = makeProjection('L3', {
      context: {
        plan_id: 7,
        plan_run_id: 12,
        job_id: 202,
        device_id: 22,
        host_id: 'host-b',
        read_at: '2026-10-11T01:05:00.000000Z',
        authority: 'job-b',
      },
    });
    rerender(
      <PlanSnapshotDrawer
        open
        onClose={vi.fn()}
        snapshot={SNAPSHOT}
        l2Projection={makeProjection('L2')}
        devices={DEVICES}
        selectedJobId={202}
        onSelectJobId={onSelectJobId}
        l3Projection={l3B}
      />,
    );
    expect(screen.getByTestId('snapshot-context-job')).toHaveTextContent('202');
    expect(screen.getByTestId('snapshot-context-device')).toHaveTextContent('22');
    expect(screen.getByTestId('snapshot-context-host')).toHaveTextContent('host-b');
    expect(screen.getByText('pool-b-ssid')).toBeInTheDocument();
    expect(screen.queryByText('pool-a-ssid')).not.toBeInTheDocument();
  });

  it('快照缺失、L2 不可用、L3 读取失败分别展示缺口，不回退原始参数', () => {
    const { rerender } = render_(
      <PlanSnapshotDrawer open onClose={vi.fn()} snapshot={null} l2Unavailable />,
    );
    expect(screen.getByTestId('plan-snapshot-empty')).toBeInTheDocument();
    expect(screen.getByTestId('parameter-projection-unavailable')).toBeInTheDocument();
    expect(screen.queryByText('RAW_STEP_PASSWORD')).not.toBeInTheDocument();

    rerender(
      <PlanSnapshotDrawer
        open
        onClose={vi.fn()}
        snapshot={SNAPSHOT}
        l2Projection={null}
        l2Unavailable
      />,
    );
    expect(screen.getByTestId('parameter-projection-unavailable')).toHaveTextContent('没有安全投影时不展示原始参数');
    expect(screen.queryByText('RAW_STEP_PASSWORD')).not.toBeInTheDocument();
    expect(screen.queryByText('"mode": "fast"')).not.toBeInTheDocument();

    rerender(
      <PlanSnapshotDrawer
        open
        onClose={vi.fn()}
        snapshot={SNAPSHOT}
        l2Projection={makeProjection('L2')}
        devices={DEVICES}
        selectedJobId={101}
        l3Projection={null}
        l3Unavailable
      />,
    );
    expect(screen.getByTestId('parameter-projection-unavailable')).toBeInTheDocument();
    expect(screen.queryByText('pool-a-ssid')).not.toBeInTheDocument();
    expect(screen.queryByText('RAW_STEP_PASSWORD')).not.toBeInTheDocument();
  });

  it('无 Job 列表时提示不能伪造下发值；关闭按钮触发 onClose', () => {
    const onClose = vi.fn();
    render_(
      <PlanSnapshotDrawer
        open
        onClose={onClose}
        snapshot={SNAPSHOT}
        l2Projection={makeProjection('L2')}
        devices={[]}
      />,
    );
    expect(screen.getByTestId('snapshot-no-jobs')).toHaveTextContent('不能伪造下发值');
    fireEvent.click(screen.getByTestId('plan-snapshot-close'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
