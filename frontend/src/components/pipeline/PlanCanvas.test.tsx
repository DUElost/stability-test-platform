import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import PlanCanvas from './PlanCanvas';
import type { PipelineDef, PipelineStep, ScriptEntry } from '@/utils/api/types';

// PlanCanvas 是纯受控组件：所有编辑都以「新 lifecycle 对象」回吐给父级。
// 断言因此打在 onLifecycleChange 的入参上，而不是 DOM 顺序 —— DOM 只能证明
// 渲染了什么，证明不了写回父级的形状（patrol 键删没删、copy 号撞没撞）。

function makeStep(overrides: Partial<PipelineStep> & { step_id: string }): PipelineStep {
  return {
    action: `script:${overrides.step_id}`,
    version: '1.0',
    params: {},
    timeout_seconds: 30,
    retry: 0,
    enabled: true,
    ...overrides,
  };
}

function makeScript(overrides: Partial<ScriptEntry> & { name: string }): ScriptEntry {
  return {
    id: 1,
    script_type: 'python',
    version: '1.0',
    nfs_path: `/scripts/${overrides.name}/v1.0`,
    content_sha256: 'sha',
    param_schema: {},
    default_params: {},
    is_active: true,
    ...overrides,
  };
}

const SCRIPTS: ScriptEntry[] = [
  makeScript({ id: 1, name: 'retired_one', is_active: false }),
  makeScript({ id: 2, name: 'chosen_one', version: '2.3' }),
  makeScript({ id: 3, name: 'other_one', version: '9.9' }),
];

const BASE_LIFECYCLE: PipelineDef = {
  lifecycle: {
    init: [makeStep({ step_id: 'init_a' }), makeStep({ step_id: 'init_b' })],
    patrol: {
      interval_seconds: 120,
      steps: [makeStep({ step_id: 'patrol_a' })],
    },
    teardown: [makeStep({ step_id: 'td_a' })],
  },
};

interface HarnessProps {
  lifecycle?: PipelineDef;
  scripts?: ScriptEntry[];
  readOnly?: boolean;
  nextPlanName?: string | null;
  isCurrentEditing?: boolean;
  initialSelected?: string | null;
  onLifecycleChange?: (next: PipelineDef) => void;
  onSelectStep?: (key: string | null) => void;
  onPatrolIntervalChange?: (next: number | null) => void;
  onFailureThresholdChange?: (next: number) => void;
  onTimeoutChange?: (next: number | null) => void;
}

/** 把受控 props 接回本地 state，让多步交互（改完再点）能像真页面一样连续。 */
function Harness({
  lifecycle: initialLifecycle = BASE_LIFECYCLE,
  scripts = SCRIPTS,
  readOnly,
  nextPlanName = null,
  isCurrentEditing = false,
  initialSelected = null,
  onLifecycleChange,
  onSelectStep,
  onPatrolIntervalChange,
  onFailureThresholdChange,
  onTimeoutChange,
}: HarnessProps) {
  const [lifecycle, setLifecycle] = useState(initialLifecycle);
  const [selected, setSelected] = useState<string | null>(initialSelected);
  const [planName, setPlanName] = useState('冒烟计划');
  const [description, setDescription] = useState('');
  const [threshold, setThreshold] = useState(0.2);
  const [patrolInterval, setPatrolInterval] = useState<number | null>(120);
  const [timeout, setTimeoutSeconds] = useState<number | null>(3600);

  return (
    <PlanCanvas
      planName={planName}
      onPlanNameChange={setPlanName}
      description={description}
      onDescriptionChange={setDescription}
      failureThreshold={threshold}
      onFailureThresholdChange={(n) => {
        onFailureThresholdChange?.(n);
        setThreshold(n);
      }}
      patrolIntervalSeconds={patrolInterval}
      onPatrolIntervalChange={(n) => {
        onPatrolIntervalChange?.(n);
        setPatrolInterval(n);
      }}
      timeoutSeconds={timeout}
      onTimeoutChange={(n) => {
        onTimeoutChange?.(n);
        setTimeoutSeconds(n);
      }}
      nextPlanName={nextPlanName}
      isCurrentEditing={isCurrentEditing}
      lifecycle={lifecycle}
      onLifecycleChange={(next) => {
        onLifecycleChange?.(next);
        setLifecycle(next);
      }}
      selectedStepKey={selected}
      onSelectStep={(key) => {
        onSelectStep?.(key);
        setSelected(key);
      }}
      scripts={scripts}
      readOnly={readOnly}
    />
  );
}

/**
 * 步骤行没有 testid，靠脚本名定位到最近的 role="button" 容器。
 * 副本与原步骤同名，nth 用来区分（默认取第一行）。
 */
function rowOf(scriptName: string, nth = 0): HTMLElement {
  const el = screen.getAllByText(scriptName)[nth]?.closest('[role="button"]');
  if (!el) throw new Error(`step row not found: ${scriptName}#${nth}`);
  return el as HTMLElement;
}

function lastLifecycle(spy: ReturnType<typeof vi.fn>): PipelineDef {
  return spy.mock.calls[spy.mock.calls.length - 1][0] as PipelineDef;
}

describe('PlanCanvas', () => {
  describe('渲染', () => {
    it('三个 phase 各自带标题与步骤计数', () => {
      render(<Harness />);
      expect(screen.getByText('Init')).toBeInTheDocument();
      expect(screen.getByText('Patrol')).toBeInTheDocument();
      expect(screen.getByText('Teardown')).toBeInTheDocument();
      expect(screen.getByText('一次性初始化 · 2 Steps')).toBeInTheDocument();
      // patrol 副标题用的是 props 上的间隔，不是 lifecycle.patrol.interval_seconds
      expect(screen.getByText('↻ 每 120s 循环 · 1 Step')).toBeInTheDocument();
      expect(screen.getByText('收尾清理 · 1 Step')).toBeInTheDocument();
    });

    it('总步骤数把 patrol.steps 一起算进去', () => {
      render(<Harness />);
      const meta = screen.getByText('总步骤').parentElement!;
      expect(within(meta).getByText('4')).toBeInTheDocument();
    });

    it('单数步骤用 Step、复数用 Steps', () => {
      render(
        <Harness
          lifecycle={{ lifecycle: { init: [makeStep({ step_id: 'only' })], teardown: [] } }}
        />,
      );
      expect(screen.getByText('一次性初始化 · 1 Step')).toBeInTheDocument();
      expect(screen.getByText('收尾清理 · 0 Steps')).toBeInTheDocument();
    });

    it('step 行剥掉 script: 前缀、归一化 v 前缀、标注禁用与无限超时', () => {
      render(
        <Harness
          lifecycle={{
            lifecycle: {
              init: [
                makeStep({
                  step_id: 'x',
                  action: 'script:install_apk',
                  version: 'v3.1',
                  enabled: false,
                  timeout_seconds: null as unknown as number,
                }),
              ],
              teardown: [],
            },
          }}
        />,
      );
      const row = rowOf('install_apk');
      // 标题只显示脚本名，原始 action 仍在副行保留
      expect(within(row).getByText('script:install_apk')).toBeInTheDocument();
      // version 已带 v 时不再重复成 vv3.1
      expect(within(row).getByText('v3.1')).toBeInTheDocument();
      expect(within(row).getByText('已禁用')).toBeInTheDocument();
      expect(within(row).getByText('∞')).toBeInTheDocument();
    });

    it('nextPlanName 存在时展示链式提示，isCurrentEditing 展示当前编辑徽标', () => {
      render(<Harness nextPlanName="回归计划" isCurrentEditing />);
      expect(screen.getByText('回归计划')).toBeInTheDocument();
      expect(screen.getByText('当前编辑')).toBeInTheDocument();
    });

    it('无链式 Plan 且非当前编辑时不渲染这两块', () => {
      render(<Harness />);
      expect(screen.queryByText('当前编辑')).not.toBeInTheDocument();
      expect(screen.queryByText(/自动执行/)).not.toBeInTheDocument();
    });
  });

  describe('添加步骤', () => {
    it('追加到末尾并选中新步骤，脚本取第一个 is_active 的', () => {
      const onLifecycleChange = vi.fn();
      const onSelectStep = vi.fn();
      render(<Harness onLifecycleChange={onLifecycleChange} onSelectStep={onSelectStep} />);

      fireEvent.click(screen.getByText('+ 添加 Init 步骤'));

      const init = lastLifecycle(onLifecycleChange).lifecycle.init;
      expect(init).toHaveLength(3);
      // 跳过 is_active=false 的 retired_one
      expect(init[2].action).toBe('script:chosen_one');
      expect(init[2].version).toBe('2.3');
      expect(init[2].timeout_seconds).toBe(30);
      expect(init[2].enabled).toBe(true);
      expect(onSelectStep).toHaveBeenCalledWith(init[2].step_id);
    });

    it('脚本目录为空时留下占位 action，交给 Inspector 补全', () => {
      const onLifecycleChange = vi.fn();
      render(<Harness scripts={[]} onLifecycleChange={onLifecycleChange} />);

      fireEvent.click(screen.getByText('+ 添加 Teardown 步骤'));

      const teardown = lastLifecycle(onLifecycleChange).lifecycle.teardown;
      expect(teardown[teardown.length - 1].action).toBe('script:');
      expect(teardown[teardown.length - 1].version).toBe('');
    });

    it('往空的 patrol 里加步骤会补出默认 60s 间隔', () => {
      const onLifecycleChange = vi.fn();
      render(
        <Harness
          lifecycle={{ lifecycle: { init: [], teardown: [] } }}
          onLifecycleChange={onLifecycleChange}
        />,
      );

      fireEvent.click(screen.getByText('+ 添加 Patrol 步骤'));

      const patrol = lastLifecycle(onLifecycleChange).lifecycle.patrol;
      expect(patrol?.interval_seconds).toBe(60);
      expect(patrol?.steps).toHaveLength(1);
    });

    it('已有 patrol 时保留原间隔', () => {
      const onLifecycleChange = vi.fn();
      render(<Harness onLifecycleChange={onLifecycleChange} />);

      fireEvent.click(screen.getByText('+ 添加 Patrol 步骤'));

      expect(lastLifecycle(onLifecycleChange).lifecycle.patrol?.interval_seconds).toBe(120);
    });
  });

  describe('移动步骤', () => {
    it('上移与相邻步骤交换', () => {
      const onLifecycleChange = vi.fn();
      render(<Harness onLifecycleChange={onLifecycleChange} />);

      fireEvent.click(within(rowOf('init_b')).getByLabelText('上移'));

      expect(lastLifecycle(onLifecycleChange).lifecycle.init.map((s) => s.step_id)).toEqual([
        'init_b',
        'init_a',
      ]);
    });

    it('下移与相邻步骤交换', () => {
      const onLifecycleChange = vi.fn();
      render(<Harness onLifecycleChange={onLifecycleChange} />);

      fireEvent.click(within(rowOf('init_a')).getByLabelText('下移'));

      expect(lastLifecycle(onLifecycleChange).lifecycle.init.map((s) => s.step_id)).toEqual([
        'init_b',
        'init_a',
      ]);
    });

    it('首尾步骤的越界方向按钮被禁用', () => {
      render(<Harness />);
      expect(within(rowOf('init_a')).getByLabelText('上移')).toBeDisabled();
      expect(within(rowOf('init_b')).getByLabelText('下移')).toBeDisabled();
      // 单步骤 phase 两个方向都禁用
      expect(within(rowOf('td_a')).getByLabelText('上移')).toBeDisabled();
      expect(within(rowOf('td_a')).getByLabelText('下移')).toBeDisabled();
    });
  });

  describe('复制步骤', () => {
    it('副本插在原步骤之后并被选中', () => {
      const onLifecycleChange = vi.fn();
      const onSelectStep = vi.fn();
      render(<Harness onLifecycleChange={onLifecycleChange} onSelectStep={onSelectStep} />);

      fireEvent.click(within(rowOf('init_a')).getByLabelText('复制'));

      expect(lastLifecycle(onLifecycleChange).lifecycle.init.map((s) => s.step_id)).toEqual([
        'init_a',
        'init_a_copy_1',
        'init_b',
      ]);
      expect(onSelectStep).toHaveBeenCalledWith('init_a_copy_1');
    });

    it('副本沿用原步骤的全部字段', () => {
      const onLifecycleChange = vi.fn();
      render(
        <Harness
          lifecycle={{
            lifecycle: {
              init: [
                makeStep({
                  step_id: 'src',
                  action: 'script:reboot',
                  version: '4.2',
                  params: { mode: 'fast' },
                  timeout_seconds: 900,
                  retry: 3,
                  enabled: false,
                }),
              ],
              teardown: [],
            },
          }}
          onLifecycleChange={onLifecycleChange}
        />,
      );

      fireEvent.click(within(rowOf('reboot')).getByLabelText('复制'));

      const copy = lastLifecycle(onLifecycleChange).lifecycle.init[1];
      expect(copy).toEqual({
        step_id: 'src_copy_1',
        action: 'script:reboot',
        version: '4.2',
        params: { mode: 'fast' },
        timeout_seconds: 900,
        retry: 3,
        enabled: false,
      });
    });

    it('副本号跨 phase 递增，不与其它 phase 的既有 ID 相撞', () => {
      const onLifecycleChange = vi.fn();
      render(
        <Harness
          lifecycle={{
            lifecycle: {
              init: [makeStep({ step_id: 'shared', action: 'script:shared' })],
              // 占位副本落在另一个 phase：只扫本 phase 的实现会在这里撞 ID
              teardown: [makeStep({ step_id: 'shared_copy_1', action: 'script:placeholder' })],
            },
          }}
          onLifecycleChange={onLifecycleChange}
        />,
      );

      fireEvent.click(within(rowOf('shared')).getByLabelText('复制'));

      expect(lastLifecycle(onLifecycleChange).lifecycle.init[1].step_id).toBe('shared_copy_2');
    });

    it('连续复制时副本号继续递增', () => {
      const onLifecycleChange = vi.fn();
      render(
        <Harness
          lifecycle={{
            lifecycle: {
              init: [makeStep({ step_id: 'base', action: 'script:base' })],
              teardown: [],
            },
          }}
          onLifecycleChange={onLifecycleChange}
        />,
      );

      fireEvent.click(within(rowOf('base')).getByLabelText('复制'));
      fireEvent.click(within(rowOf('base')).getByLabelText('复制'));

      expect(lastLifecycle(onLifecycleChange).lifecycle.init.map((s) => s.step_id)).toEqual([
        'base',
        'base_copy_2',
        'base_copy_1',
      ]);
    });
  });

  describe('删除步骤', () => {
    it('删除未选中的步骤不影响当前选中项', () => {
      const onLifecycleChange = vi.fn();
      const onSelectStep = vi.fn();
      render(
        <Harness
          initialSelected="init_a"
          onLifecycleChange={onLifecycleChange}
          onSelectStep={onSelectStep}
        />,
      );

      fireEvent.click(within(rowOf('init_b')).getByLabelText('删除'));

      expect(lastLifecycle(onLifecycleChange).lifecycle.init.map((s) => s.step_id)).toEqual([
        'init_a',
      ]);
      expect(onSelectStep).not.toHaveBeenCalled();
    });

    it('删除当前选中的步骤会清空选中，避免 Inspector 指向幽灵步骤', () => {
      const onSelectStep = vi.fn();
      render(<Harness initialSelected="init_a" onSelectStep={onSelectStep} />);

      fireEvent.click(within(rowOf('init_a')).getByLabelText('删除'));

      expect(onSelectStep).toHaveBeenCalledWith(null);
    });

    it('删掉最后一个 patrol 步骤会摘掉整个 patrol 键', () => {
      const onLifecycleChange = vi.fn();
      render(<Harness onLifecycleChange={onLifecycleChange} />);

      fireEvent.click(within(rowOf('patrol_a')).getByLabelText('删除'));

      // 后端 pipeline schema 只认「有 patrol 就必须有 steps」，留空对象会 422
      expect(lastLifecycle(onLifecycleChange).lifecycle).not.toHaveProperty('patrol');
    });
  });

  describe('选中', () => {
    it('点击步骤行选中它', () => {
      const onSelectStep = vi.fn();
      render(<Harness onSelectStep={onSelectStep} />);

      fireEvent.click(rowOf('init_b'));

      expect(onSelectStep).toHaveBeenCalledWith('init_b');
    });

    it('Enter / 空格键盘选中', () => {
      const onSelectStep = vi.fn();
      render(<Harness onSelectStep={onSelectStep} />);

      fireEvent.keyDown(rowOf('init_a'), { key: 'Enter' });
      fireEvent.keyDown(rowOf('init_b'), { key: ' ' });

      expect(onSelectStep).toHaveBeenNthCalledWith(1, 'init_a');
      expect(onSelectStep).toHaveBeenNthCalledWith(2, 'init_b');
    });

    it('点击行内操作按钮不会顺带触发选中', () => {
      const onSelectStep = vi.fn();
      render(<Harness onSelectStep={onSelectStep} />);

      fireEvent.click(within(rowOf('init_a')).getByLabelText('下移'));

      expect(onSelectStep).not.toHaveBeenCalled();
    });
  });

  describe('meta 输入', () => {
    it('patrol 间隔留空视为不开启', () => {
      const onPatrolIntervalChange = vi.fn();
      render(<Harness onPatrolIntervalChange={onPatrolIntervalChange} />);

      const input = screen.getByPlaceholderText('不开启');
      fireEvent.change(input, { target: { value: '' } });

      expect(onPatrolIntervalChange).toHaveBeenCalledWith(null);
    });

    it('patrol 间隔下限 5 秒', () => {
      const onPatrolIntervalChange = vi.fn();
      render(<Harness onPatrolIntervalChange={onPatrolIntervalChange} />);

      fireEvent.change(screen.getByPlaceholderText('不开启'), { target: { value: '3' } });

      expect(onPatrolIntervalChange).toHaveBeenCalledWith(5);
    });

    it('patrol 间隔填 0 落回默认 60 秒', () => {
      const onPatrolIntervalChange = vi.fn();
      render(<Harness onPatrolIntervalChange={onPatrolIntervalChange} />);

      // parseInt('0') 为 falsy，走 || 60 的默认分支——不是 clamp 到 5
      fireEvent.change(screen.getByPlaceholderText('不开启'), { target: { value: '0' } });

      expect(onPatrolIntervalChange).toHaveBeenCalledWith(60);
    });

    it('失败阈值夹在 0..1 之间并同步百分比', () => {
      const onFailureThresholdChange = vi.fn();
      render(<Harness onFailureThresholdChange={onFailureThresholdChange} />);
      const input = screen.getByDisplayValue('0.2');

      fireEvent.change(input, { target: { value: '1.5' } });
      expect(onFailureThresholdChange).toHaveBeenLastCalledWith(1);
      expect(screen.getByText('100%')).toBeInTheDocument();

      fireEvent.change(screen.getByDisplayValue('1'), { target: { value: '-0.3' } });
      expect(onFailureThresholdChange).toHaveBeenLastCalledWith(0);
      expect(screen.getByText('0%')).toBeInTheDocument();
    });

    it('全局超时留空视为不限、负数夹到 0', () => {
      const onTimeoutChange = vi.fn();
      render(<Harness onTimeoutChange={onTimeoutChange} />);

      fireEvent.change(screen.getByDisplayValue('3600'), { target: { value: '-5' } });
      expect(onTimeoutChange).toHaveBeenLastCalledWith(0);

      fireEvent.change(screen.getByPlaceholderText('不限'), { target: { value: '' } });
      expect(onTimeoutChange).toHaveBeenLastCalledWith(null);
    });
  });

  describe('只读模式', () => {
    it('不渲染任何添加按钮', () => {
      render(<Harness readOnly />);
      expect(screen.queryByText(/\+ 添加/)).not.toBeInTheDocument();
    });

    it('行内操作按钮全部禁用', () => {
      render(<Harness readOnly />);
      const row = within(rowOf('init_a'));
      for (const label of ['上移', '下移', '复制', '删除']) {
        expect(row.getByLabelText(label)).toBeDisabled();
      }
    });

    it('名称与 meta 输入不可编辑', () => {
      render(<Harness readOnly />);
      expect(screen.getByDisplayValue('冒烟计划')).toHaveAttribute('readonly');
      expect(screen.getByPlaceholderText('不开启')).toBeDisabled();
      expect(screen.getByPlaceholderText('不限')).toBeDisabled();
    });

    it('仍可点击步骤查看详情', () => {
      const onSelectStep = vi.fn();
      render(<Harness readOnly onSelectStep={onSelectStep} />);

      fireEvent.click(rowOf('init_b'));

      expect(onSelectStep).toHaveBeenCalledWith('init_b');
    });
  });
});
