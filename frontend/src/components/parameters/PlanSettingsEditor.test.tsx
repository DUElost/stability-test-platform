import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
    {
      path: ['device_lease_ttl_seconds'],
      label: '设备租约 TTL',
      meaning: '只读登记项',
      fallback_chain: '主机默认',
      state: 'env_fallback',
      value: 3600,
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
        fallback_chain: '代码默认 true',
        sensitive: false,
        is_set: false,
      },
      {
        path: ['required_categories'],
        label: '必需日志分类',
        meaning: '派发前必须具备的日志分类',
        state: 'unset_definite',
        fallback_chain: '代码默认空',
        sensitive: false,
        is_set: false,
      },
      {
        path: ['batch_interval_seconds'],
        label: '批量上报间隔',
        meaning: '只读登记项',
        state: 'env_fallback',
        value: 5,
        fallback_chain: '代码默认 5',
        sensitive: false,
        is_set: false,
      },
    ],
  },
  dispatch_decisions: [],
  safe_debug: {
    steps: [],
    plan_settings: { raw: 'RAW_PLAN_SENTINEL' },
    watcher_policy: { secret: 'RAW_WATCHER_SENTINEL' },
  },
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

  it('长释义挂标签旁帮助图标；默认不常显为静态段落', async () => {
    const user = userEvent.setup();
    const longMeaning =
      '从首次等待开始计时。正整数为计划显式硬顶；未设时继承主机安全策略，不是关闭硬顶。';
    render(
      <PlanSettingsEditor values={baseValues()} onChange={() => {}} projection={projection} />,
    );

    const hardMax = screen.getByTestId('plan-setting-barrier-max-wait');
    expect(within(hardMax).queryByText(longMeaning)).not.toBeInTheDocument();
    expect(hardMax.querySelectorAll('p').length).toBeLessThanOrEqual(2);

    const help = within(hardMax).getByRole('button', {
      name: '初始化同步累计等待硬顶的说明',
    });
    await user.hover(help);
    expect(await screen.findByRole('tooltip')).toHaveTextContent(longMeaning);

    const patrol = screen.getByTestId('plan-setting-patrol-interval');
    expect(within(patrol).queryByText('轮间等待')).not.toBeInTheDocument();
    expect(
      within(patrol).getByRole('button', { name: '巡检间隔的说明' }),
    ).toBeInTheDocument();

    expect(
      screen.getByRole('button', { name: '异常采集开关的说明' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('是否希望本计划采集异常')).not.toBeInTheDocument();
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
    fireEvent.change(screen.getByRole('textbox', { name: '初始化同步累计等待硬顶' }), {
      target: { value: '900' },
    });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ barrierMaxWaitSeconds: 900 }),
    );
    onChange.mockClear();
    fireEvent.change(screen.getByRole('textbox', { name: '初始化同步累计等待硬顶' }), {
      target: { value: '' },
    });
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
    fireEvent.change(screen.getByRole('textbox', { name: '初始化同步等待预算' }), {
      target: { value: '1.5' },
    });
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole('textbox', { name: '初始化同步等待预算' }), {
      target: { value: '-3' },
    });
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

  it('编辑四键时保留未知 watcher 子键；只读项经安全投影展示', () => {
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
    // 只读登记项走投影清单（值/状态/含义），不再只列原始 key。
    expect(screen.getByTestId('plan-settings-readonly-projection')).toHaveTextContent('批量上报间隔');
    expect(screen.getByTestId('plan-settings-readonly-projection')).toHaveTextContent('沿回落链');
    expect(screen.getByTestId('plan-settings-readonly-projection')).toHaveTextContent('设备租约 TTL');
    fireEvent.click(screen.getByTestId('plan-watcher-enabled'));
    const next = onChange.mock.calls[onChange.mock.calls.length - 1]?.[0] as PlanSettingsValues;
    expect(next.watcherPolicy).toMatchObject({
      enabled: false,
      batch_interval_seconds: 5,
      nfs_quota_mb: 2048,
    });
  });

  it('路径分类清空时保留未知 CUSTOM；已知分类可单独清空', () => {
    const onChange = vi.fn();
    render(
      <PlanSettingsEditor
        values={baseValues({
          watcherPolicy: { paths: { AEE: ['/data/aee_exp'], CUSTOM: ['/legacy'] } },
        })}
        onChange={onChange}
        projection={projection}
      />,
    );
    fireEvent.change(screen.getByLabelText('AEE 路径'), { target: { value: '' } });
    const next = onChange.mock.calls[onChange.mock.calls.length - 1]?.[0] as PlanSettingsValues;
    expect(next.watcherPolicy?.paths).toEqual({ CUSTOM: ['/legacy'] });
  });

  it('切换已知必需分类时保留原始未知项；取消最后已知且无未知时发 []', () => {
    const onChange = vi.fn();
    const safeProjection: ParameterProjection = {
      ...projection,
      watcher_policy: {
        ...projection.watcher_policy,
        items: projection.watcher_policy.items.map((item) =>
          item.path[0] === 'required_categories'
            ? {
                ...item,
                state: 'explicit',
                is_set: true,
                // 安全投影值与 Plan 原始哨兵不同
                value: ['SAFE_REQUIRED_DISPLAY', 'AEE'],
              }
            : item,
        ),
      },
    };
    const { rerender } = render(
      <PlanSettingsEditor
        values={baseValues({
          watcherPolicy: { required_categories: ['RAW_REQUIRED_SENTINEL', 'AEE'] },
        })}
        onChange={onChange}
        projection={safeProjection}
      />,
    );
    // 只读展示取安全投影，不列原始哨兵
    expect(screen.getByTestId('plan-watcher-required-unknown')).toHaveTextContent(
      'SAFE_REQUIRED_DISPLAY',
    );
    expect(screen.getByTestId('plan-watcher-required-unknown').textContent).not.toContain(
      'RAW_REQUIRED_SENTINEL',
    );
    fireEvent.click(screen.getByLabelText('必需分类 AEE'));
    let next = onChange.mock.calls[onChange.mock.calls.length - 1]?.[0] as PlanSettingsValues;
    // 保存/编辑事实仍保留原始未知类别
    expect(next.watcherPolicy?.required_categories).toEqual(['RAW_REQUIRED_SENTINEL']);

    onChange.mockClear();
    rerender(
      <PlanSettingsEditor
        values={baseValues({
          watcherPolicy: { required_categories: ['AEE'] },
        })}
        onChange={onChange}
        projection={safeProjection}
      />,
    );
    fireEvent.click(screen.getByLabelText('必需分类 AEE'));
    next = onChange.mock.calls[onChange.mock.calls.length - 1]?.[0] as PlanSettingsValues;
    expect(next.watcherPolicy).toHaveProperty('required_categories');
    expect(next.watcherPolicy?.required_categories).toEqual([]);
  });

  it('C3 只读出口：投影可用时只展示安全值；缺失/unavailable 不列原始哨兵', () => {
    const RAW = 'RAW_REQUIRED_SENTINEL';
    const SAFE = 'SAFE_REQUIRED_DISPLAY';
    const safeProjection: ParameterProjection = {
      ...projection,
      watcher_policy: {
        ...projection.watcher_policy,
        items: projection.watcher_policy.items.map((item) =>
          item.path[0] === 'required_categories'
            ? {
                ...item,
                state: 'explicit',
                is_set: true,
                value: [SAFE, 'AEE'],
              }
            : item,
        ),
      },
    };
    const values = baseValues({
      watcherPolicy: { required_categories: [RAW, 'AEE'] },
    });

    const { rerender, container } = render(
      <PlanSettingsEditor values={values} onChange={() => {}} projection={safeProjection} />,
    );
    expect(screen.getByTestId('plan-watcher-required-unknown')).toHaveTextContent(SAFE);
    expect(container.textContent).not.toContain(RAW);

    rerender(<PlanSettingsEditor values={values} onChange={() => {}} unavailable />);
    expect(screen.getByTestId('plan-watcher-required-unknown-gap')).toHaveTextContent(
      '未知分类已保留',
    );
    expect(container.textContent).not.toContain(RAW);
    expect(screen.queryByTestId('plan-watcher-required-unknown')).not.toBeInTheDocument();

    rerender(<PlanSettingsEditor values={values} onChange={() => {}} />);
    expect(screen.getByTestId('plan-watcher-required-unknown-gap')).toHaveTextContent(
      '未知分类已保留',
    );
    expect(container.textContent).not.toContain(RAW);
  });

  it('未设 enabled/required 时展示投影状态，不把控件显示成确定关/全未选', () => {
    render(
      <PlanSettingsEditor values={baseValues({ watcherPolicy: null })} onChange={() => {}} projection={projection} />,
    );
    expect(screen.getByTestId('projection-state-enabled')).toHaveTextContent('沿回落链');
    expect(screen.getByTestId('plan-watcher-enabled')).toHaveAttribute('data-explicit', '0');
    expect(screen.getByTestId('plan-watcher-required-unset')).toBeInTheDocument();
    expect(screen.getByTestId('projection-state-required_categories')).toHaveTextContent('未设置');
  });

  it('投影失败时不回退原始 JSON/敏感值', () => {
    const { container } = render(
      <PlanSettingsEditor
        values={baseValues({
          watcherPolicy: {
            enabled: true,
            password: 'RAW_SECRET',
            batch_interval_seconds: 5,
            required_categories: ['RAW_REQUIRED_SENTINEL', 'AEE'],
          },
        })}
        onChange={() => {}}
        unavailable
      />,
    );
    expect(screen.getByTestId('plan-settings-projection-unavailable')).toBeInTheDocument();
    expect(screen.getByTestId('plan-watcher-required-unknown-gap')).toHaveTextContent(
      '未知分类已保留',
    );
    expect(container.textContent).not.toContain('RAW_SECRET');
    expect(container.textContent).not.toContain('RAW_REQUIRED_SENTINEL');
    expect(container.textContent).not.toContain('RAW_WATCHER_SENTINEL');
    expect(container.textContent).not.toContain('RAW_PLAN_SENTINEL');
    expect(container.querySelector('textarea[name=json]')).toBeNull();
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
    expect(container.textContent).not.toContain('RAW_WATCHER_SENTINEL');
    expect(container.querySelector('textarea[name=json]')).toBeNull();
  });
});
