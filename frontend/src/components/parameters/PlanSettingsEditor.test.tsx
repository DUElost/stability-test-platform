import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { PlanSettingsEditor, type PlanSettingsValues } from './PlanSettingsEditor';
import type { ParameterProjection } from '@/utils/api/types';

const projection: ParameterProjection = {
  layer: 'L1',
  context: { read_at: '2026-10-12T00:00:00Z', authority: '测试服务端' },
  steps: [],
  plan_settings: [
    {
      path: ['barrier_max_wait_seconds'],
      label: '初始化同步累计等待硬顶',
      meaning: '从首次等待开始计时。正整数为计划显式硬顶；未设时继承主机安全策略，不是关闭硬顶。',
      fallback_chain: 'Plan 值 → 主机 STP_BARRIER_MAX_WAIT_SECONDS → 1800',
      unit: '秒',
      state: 'env_fallback',
      sensitive: false,
      is_set: false,
    },
    {
      path: ['patrol_interval_seconds'],
      label: '巡检间隔',
      meaning: '轮间等待',
      unit: '秒',
      state: 'unset_definite',
      sensitive: false,
      is_set: false,
    },
  ],
  watcher_policy: {
    note: '独立策略',
    items: [
      {
        path: ['enabled'],
        label: '异常采集开关',
        meaning: '是否希望本计划采集异常',
        state: 'env_fallback',
        sensitive: false,
        is_set: false,
      },
    ],
  },
  dispatch_decisions: [],
  safe_debug: { steps: [], plan_settings: {}, watcher_policy: {} },
};

function baseValues(over: Partial<PlanSettingsValues> = {}): PlanSettingsValues {
  return {
    patrolIntervalSeconds: null,
    timeoutSeconds: null,
    barrierTimeoutSeconds: null,
    barrierMaxWaitSeconds: null,
    autoArchiveIntervalSeconds: null,
    watcherPolicy: null,
    ...over,
  };
}

describe('PlanSettingsEditor', () => {
  it('渲染五个计划数字设置与 watcher 四键，无自由 JSON 编辑器', () => {
    render(
      <PlanSettingsEditor values={baseValues()} onChange={() => {}} projection={projection} isDraft />,
    );
    expect(screen.getByTestId('plan-settings-editor')).toHaveTextContent('L1草稿，未保存');
    expect(screen.getByTestId('plan-setting-patrol-interval')).toBeInTheDocument();
    expect(screen.getByTestId('plan-setting-timeout')).toBeInTheDocument();
    expect(screen.getByTestId('plan-setting-barrier-timeout')).toBeInTheDocument();
    expect(screen.getByTestId('plan-setting-barrier-max-wait')).toBeInTheDocument();
    expect(screen.getByTestId('plan-setting-auto-archive')).toBeInTheDocument();
    expect(screen.getByTestId('plan-watcher-editor')).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: /json/i })).not.toBeInTheDocument();
  });

  it('硬顶清空提交 null，并提示继承主机而非关闭', () => {
    const onChange = vi.fn();
    render(
      <PlanSettingsEditor
        values={baseValues({ barrierMaxWaitSeconds: null })}
        onChange={onChange}
        projection={projection}
      />,
    );
    const field = screen.getByTestId('plan-setting-barrier-max-wait');
    expect(field).toHaveTextContent('清空表示继承主机安全策略，不是关闭硬顶。');
    fireEvent.change(screen.getByLabelText('初始化同步累计等待硬顶'), { target: { value: '900' } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ barrierMaxWaitSeconds: 900 }),
    );
    onChange.mockClear();
    fireEvent.change(screen.getByLabelText('初始化同步累计等待硬顶'), { target: { value: '' } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ barrierMaxWaitSeconds: null }),
    );
  });

  it('拒绝小数与负值，不把非法输入提交为显式数字', () => {
    const onChange = vi.fn();
    render(
      <PlanSettingsEditor
        values={baseValues({ barrierTimeoutSeconds: 600 })}
        onChange={onChange}
        projection={projection}
      />,
    );
    fireEvent.change(screen.getByLabelText(/初始化同步等待预算/), { target: { value: '1.5' } });
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/初始化同步等待预算/), { target: { value: '-3' } });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('watcher 非法 enum 不可选自控件；清空策略提交空对象', () => {
    const onChange = vi.fn();
    render(
      <PlanSettingsEditor
        values={baseValues({ watcherPolicy: { enabled: true, custom_key: 1 } })}
        onChange={onChange}
        projection={projection}
      />,
    );
    const select = screen.getByTestId('plan-watcher-on-unavailable');
    expect(Array.from(select.querySelectorAll('option')).map((o) => o.value)).toEqual([
      '',
      'fail',
      'degraded',
      'skip',
    ]);
    fireEvent.click(screen.getByTestId('plan-watcher-clear'));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ watcherPolicy: {} }));
  });

  it('未知 watcher 子键只读展示并在编辑四键时保留', () => {
    const onChange = vi.fn();
    render(
      <PlanSettingsEditor
        values={baseValues({
          watcherPolicy: { enabled: true, batch_interval_seconds: 5, nfs_quota_mb: 2048 },
        })}
        onChange={onChange}
        projection={projection}
      />,
    );
    expect(screen.getByTestId('plan-watcher-unknown-keys')).toHaveTextContent('batch_interval_seconds');
    expect(screen.getByTestId('plan-watcher-unknown-keys')).toHaveTextContent('nfs_quota_mb');
    fireEvent.click(screen.getByTestId('plan-watcher-enabled'));
    const next = onChange.mock.calls[onChange.mock.calls.length - 1]?.[0] as PlanSettingsValues;
    expect(next.watcherPolicy).toMatchObject({
      enabled: false,
      batch_interval_seconds: 5,
      nfs_quota_mb: 2048,
    });
  });

  it('路径分类清空时提示按分类回落默认', () => {
    const onChange = vi.fn();
    render(
      <PlanSettingsEditor
        values={baseValues({
          watcherPolicy: { paths: { AEE: ['/data/aee_exp'] } },
        })}
        onChange={onChange}
        projection={projection}
      />,
    );
    fireEvent.change(screen.getByLabelText('AEE 路径'), { target: { value: '' } });
    const next = onChange.mock.calls[onChange.mock.calls.length - 1]?.[0] as PlanSettingsValues;
    expect(next.watcherPolicy?.paths).toBeUndefined();
  });

  it('不渲染原始敏感对象或 free-form JSON', () => {
    const { container } = render(
      <PlanSettingsEditor
        values={baseValues({
          watcherPolicy: { enabled: true, password: 'RAW_SECRET' },
        })}
        onChange={() => {}}
        projection={projection}
      />,
    );
    expect(container.textContent).not.toContain('RAW_SECRET');
    expect(container.querySelector('textarea[name=json]')).toBeNull();
  });
});
