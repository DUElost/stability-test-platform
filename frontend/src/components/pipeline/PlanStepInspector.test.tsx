import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import PlanStepInspector from './PlanStepInspector';
import type { PipelinePhase, PipelineStep, ScriptEntry } from '@/utils/api/types';

// Inspector 是受控组件：每次编辑都整体回吐 PipelineStep。
// 断言主要打在 onUpdateStep 的入参上——参数表单的核心语义（三层取值、等于
// 默认值就删键）只在回吐的 params 里看得见，DOM 上看不出来。

function makeStep(overrides: Partial<PipelineStep> = {}): PipelineStep {
  return {
    step_id: 'init_1',
    action: 'script:install_apk',
    version: '2.0',
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
    category: 'setup',
    version: '1.0',
    nfs_path: `/scripts/${overrides.name}`,
    content_sha256: 'sha',
    param_schema: {},
    default_params: {},
    is_active: true,
    ...overrides,
  };
}

const INSTALL_V2 = makeScript({
  id: 1,
  name: 'install_apk',
  version: '2.0',
  category: 'setup',
  param_schema: {
    // 故意把 required 项放在非首位，验证排序把它提前
    retries: { type: 'integer', minimum: 0, default: 2 },
    package: { type: 'string', required: true, label: '目标包名', description: 'com.example' },
    mode: { type: 'string', enum: ['fast', 'safe'] },
    verbose: { type: 'boolean' },
  },
  default_params: { retries: 5 },
});

const INSTALL_V1 = makeScript({
  id: 2,
  name: 'install_apk',
  version: '1.0',
  param_schema: { package: { type: 'string' } },
  default_params: {},
});

const REBOOT = makeScript({
  id: 3,
  name: 'reboot_device',
  version: '3.0',
  category: 'utility',
  param_schema: { wait: { type: 'integer' } },
  default_params: { wait: 10 },
});

const SCRIPTS: ScriptEntry[] = [INSTALL_V2, INSTALL_V1, REBOOT];

interface HarnessProps {
  step?: PipelineStep | null;
  phase?: PipelinePhase | null;
  index?: number | null;
  scripts?: ScriptEntry[];
  readOnly?: boolean;
  onUpdateStep?: (next: PipelineStep) => void;
}

function Harness({
  step: initialStep = makeStep(),
  phase = 'init',
  index = 0,
  scripts = SCRIPTS,
  readOnly,
  onUpdateStep,
}: HarnessProps) {
  const [step, setStep] = useState<PipelineStep | null>(initialStep);
  return (
    <MemoryRouter>
      <PlanStepInspector
        step={step}
        phase={phase}
        index={index}
        scripts={scripts}
        onUpdateStep={(next) => {
          onUpdateStep?.(next);
          setStep(next);
        }}
        readOnly={readOnly}
      />
    </MemoryRouter>
  );
}

/** Row / FieldGroup 都是「label span + 控件」的兄弟结构，没有 htmlFor。 */
function fieldOf(label: string): HTMLElement {
  const el = screen.getByText(label).parentElement;
  if (!el) throw new Error(`field not found: ${label}`);
  return el;
}

function lastStep(spy: ReturnType<typeof vi.fn>): PipelineStep {
  return spy.mock.calls[spy.mock.calls.length - 1][0] as PipelineStep;
}

describe('PlanStepInspector', () => {
  describe('空态', () => {
    it('未选择步骤时只渲染占位，不渲染任何编辑卡片', () => {
      render(<Harness step={null} phase={null} index={null} />);
      expect(screen.getAllByText('未选择步骤').length).toBeGreaterThan(0);
      expect(screen.getByText('在中央画布点击任意步骤以查看其属性。')).toBeInTheDocument();
      expect(screen.queryByText('脚本信息')).not.toBeInTheDocument();
      expect(screen.queryByText('执行配置')).not.toBeInTheDocument();
    });
  });

  describe('头尾信息', () => {
    it('标题剥掉 script: 前缀并归一化 v 前缀', () => {
      render(<Harness step={makeStep({ version: 'v2.0' })} />);
      expect(screen.getByText('install_apk / v2.0')).toBeInTheDocument();
    });

    it('缺版本时标题降级为占位符', () => {
      render(<Harness step={makeStep({ action: 'script:', version: '' })} />);
      expect(screen.getByText('— / v—')).toBeInTheDocument();
    });

    it('脚注给出步骤在 pipeline 中的位置（下标从 1 起）', () => {
      render(<Harness phase="patrol" index={2} />);
      expect(
        screen.getByText('位于 patrol #3。脚本的参数和默认值在脚本管理页面维护。'),
      ).toBeInTheDocument();
    });
  });

  describe('脚本选择', () => {
    it('脚本名下拉只列已激活脚本、去重并按名排序', () => {
      render(
        <Harness
          scripts={[
            ...SCRIPTS,
            makeScript({ id: 9, name: 'zz_retired', is_active: false }),
          ]}
        />,
      );
      const options = within(fieldOf('脚本名'))
        .getAllByRole('option')
        .map((o) => o.textContent);
      expect(options).toEqual(['— 选择脚本 —', 'install_apk', 'reboot_device']);
    });

    it('版本下拉按版本号倒序，并标出已停用版本', () => {
      render(
        <Harness
          scripts={[
            INSTALL_V1,
            INSTALL_V2,
            makeScript({ id: 4, name: 'install_apk', version: '1.5', is_active: false }),
          ]}
        />,
      );
      const options = within(fieldOf('版本'))
        .getAllByRole('option')
        .map((o) => o.textContent);
      expect(options).toEqual(['—', '2.0', '1.5 (已停用)', '1.0']);
    });

    it('展示匹配脚本的类型与分类', () => {
      render(<Harness />);
      expect(within(fieldOf('类型 / 分类')).getByText('python / setup')).toBeInTheDocument();
    });

    it('换脚本时丢弃旧参数（新脚本的 schema 与旧的无关）', () => {
      const onUpdateStep = vi.fn();
      render(
        <Harness step={makeStep({ params: { package: 'com.a' } })} onUpdateStep={onUpdateStep} />,
      );

      fireEvent.change(within(fieldOf('脚本名')).getByRole('combobox'), {
        target: { value: 'reboot_device' },
      });

      expect(lastStep(onUpdateStep)).toMatchObject({
        action: 'script:reboot_device',
        version: '3.0',
        params: {},
      });
    });

    it('同脚本换版本时保留新版仍认识的参数键、丢掉不认识的', () => {
      const onUpdateStep = vi.fn();
      render(
        <Harness
          step={makeStep({ params: { package: 'com.a', mode: 'fast' } })}
          onUpdateStep={onUpdateStep}
        />,
      );

      // v1.0 的 schema 只有 package，mode 应被剔除
      fireEvent.change(within(fieldOf('版本')).getByRole('combobox'), {
        target: { value: '1.0' },
      });

      expect(lastStep(onUpdateStep).version).toBe('1.0');
      expect(lastStep(onUpdateStep).params).toEqual({ package: 'com.a' });
    });

    it('选中的脚本名无任何激活版本时版本置空，交由使用者补选', () => {
      const onUpdateStep = vi.fn();
      render(
        <Harness
          scripts={[INSTALL_V2, makeScript({ id: 7, name: 'legacy', is_active: false })]}
          step={makeStep({ action: 'script:legacy', version: '' })}
          onUpdateStep={onUpdateStep}
        />,
      );

      fireEvent.change(within(fieldOf('脚本名')).getByRole('combobox'), {
        target: { value: 'legacy' },
      });

      expect(lastStep(onUpdateStep).version).toBe('');
    });
  });

  describe('脚本异常告警', () => {
    it('脚本目录里找不到时提示重新选择', () => {
      render(<Harness step={makeStep({ action: 'script:ghost', version: '1.0' })} />);
      expect(screen.getByText(/未在已激活脚本中找到/)).toBeInTheDocument();
      expect(screen.getByText('ghost@1.0')).toBeInTheDocument();
      // 未匹配的名字仍留在下拉里，避免静默改写用户已保存的 Plan
      expect(screen.getByText('ghost (未匹配)')).toBeInTheDocument();
    });

    it('版本存在但已停用时提示换激活版本，而不是报"找不到"', () => {
      render(
        <Harness
          scripts={[INSTALL_V2, makeScript({ id: 5, name: 'install_apk', version: '0.9', is_active: false })]}
          step={makeStep({ version: '0.9' })}
        />,
      );
      expect(screen.getByText(/版本已停用/)).toBeInTheDocument();
      expect(screen.queryByText(/未在已激活脚本中找到/)).not.toBeInTheDocument();
    });

    it('脚本与版本都匹配且激活时不出告警', () => {
      render(<Harness />);
      expect(screen.queryByText(/未在已激活脚本中找到/)).not.toBeInTheDocument();
      expect(screen.queryByText(/版本已停用/)).not.toBeInTheDocument();
    });
  });

  describe('参数表单', () => {
    it('必填项排在前面并带 * 标记', () => {
      render(<Harness />);
      const labels = screen
        .getAllByText(/^(目标包名|retries|mode|verbose)( \*)?$/)
        .map((el) => el.textContent);
      expect(labels[0]).toBe('目标包名 *');
    });

    it('取值优先级：step.params > default_params > schema.default', () => {
      const { unmount } = render(<Harness step={makeStep({ params: { retries: 9 } })} />);
      expect(within(fieldOf('retries')).getByRole('spinbutton')).toHaveValue(9);
      unmount();

      // step.params 无此键 → 落到 default_params 的 5，而不是 schema 的 2
      render(<Harness />);
      expect(within(fieldOf('retries')).getByRole('spinbutton')).toHaveValue(5);
    });

    it('三层都没值时留空，用 description 当 placeholder', () => {
      render(<Harness />);
      const input = within(fieldOf('目标包名 *')).getByRole('textbox');
      expect(input).toHaveValue('');
      expect(input).toHaveAttribute('placeholder', 'com.example');
    });

    it('改成与 default_params 相同的值会把键删掉，保持 payload 最小', () => {
      const onUpdateStep = vi.fn();
      render(<Harness step={makeStep({ params: { retries: 9 } })} onUpdateStep={onUpdateStep} />);

      fireEvent.change(within(fieldOf('retries')).getByRole('spinbutton'), {
        target: { value: '5' },
      });

      expect(lastStep(onUpdateStep).params).toEqual({});
    });

    it('与默认值不同的值会写进 params', () => {
      const onUpdateStep = vi.fn();
      render(<Harness onUpdateStep={onUpdateStep} />);

      fireEvent.change(within(fieldOf('retries')).getByRole('spinbutton'), {
        target: { value: '7' },
      });

      expect(lastStep(onUpdateStep).params).toEqual({ retries: 7 });
    });

    it('enum 字段渲染为下拉，清空会删键', () => {
      const onUpdateStep = vi.fn();
      render(<Harness step={makeStep({ params: { mode: 'fast' } })} onUpdateStep={onUpdateStep} />);
      const select = within(fieldOf('mode')).getByRole('combobox');
      expect(select).toHaveValue('fast');

      fireEvent.change(select, { target: { value: 'safe' } });
      expect(lastStep(onUpdateStep).params).toEqual({ mode: 'safe' });

      fireEvent.change(within(fieldOf('mode')).getByRole('combobox'), { target: { value: '' } });
      expect(lastStep(onUpdateStep).params).toEqual({});
    });

    it('boolean 字段渲染为开关并回写真布尔值', () => {
      const onUpdateStep = vi.fn();
      render(<Harness onUpdateStep={onUpdateStep} />);
      const toggle = screen.getByLabelText('verbose: 否');
      expect(toggle).toHaveAttribute('aria-pressed', 'false');

      fireEvent.click(toggle);

      expect(lastStep(onUpdateStep).params).toEqual({ verbose: true });
      expect(screen.getByLabelText('verbose: 是')).toHaveAttribute('aria-pressed', 'true');
    });

    it('string 字段清空即删键', () => {
      const onUpdateStep = vi.fn();
      render(
        <Harness step={makeStep({ params: { package: 'com.a' } })} onUpdateStep={onUpdateStep} />,
      );

      fireEvent.change(within(fieldOf('目标包名 *')).getByRole('textbox'), {
        target: { value: '' },
      });

      expect(lastStep(onUpdateStep).params).toEqual({});
    });

    it('无 schema 但有 default_params 时降级为只读标签', () => {
      render(
        <Harness
          scripts={[makeScript({ id: 8, name: 'legacy', version: '1.0', default_params: { host: 'x', port: 22 } })]}
          step={makeStep({ action: 'script:legacy', version: '1.0' })}
        />,
      );
      expect(screen.getByText('无 schema')).toBeInTheDocument();
      expect(screen.getByText('host')).toBeInTheDocument();
      expect(screen.getByText('port')).toBeInTheDocument();
    });

    it('schema 与 default_params 都为空时整张参数卡不渲染', () => {
      render(
        <Harness
          scripts={[makeScript({ id: 8, name: 'bare', version: '1.0' })]}
          step={makeStep({ action: 'script:bare', version: '1.0' })}
        />,
      );
      expect(screen.queryByText('脚本参数')).not.toBeInTheDocument();
    });
  });

  describe('执行配置', () => {
    it('超时留空记 0（不限），其余按下限 1 夹取', () => {
      const onUpdateStep = vi.fn();
      render(<Harness onUpdateStep={onUpdateStep} />);

      fireEvent.change(within(fieldOf('超时 (秒)')).getByRole('spinbutton'), {
        target: { value: '-5' },
      });
      expect(lastStep(onUpdateStep).timeout_seconds).toBe(1);
    });

    it('清空超时不落库——0 与 null 都存不进后端，此前会静默写 0', () => {
      const onUpdateStep = vi.fn();
      render(<Harness onUpdateStep={onUpdateStep} />);
      const input = within(fieldOf('超时 (秒)')).getByRole('spinbutton');

      fireEvent.change(input, { target: { value: '' } });

      expect(onUpdateStep).not.toHaveBeenCalled();
      // 编辑期允许显示为空，不强行折算成默认值（否则接着输入会拼成 306）
      expect(input).toHaveValue(null);
    });

    it('清空后重新输入按新值提交', () => {
      const onUpdateStep = vi.fn();
      render(<Harness onUpdateStep={onUpdateStep} />);
      const input = within(fieldOf('超时 (秒)')).getByRole('spinbutton');

      fireEvent.change(input, { target: { value: '' } });
      fireEvent.change(input, { target: { value: '600' } });

      expect(lastStep(onUpdateStep).timeout_seconds).toBe(600);
    });

    it('留空失焦后回落到最后一次提交的值', () => {
      render(<Harness step={makeStep({ timeout_seconds: 45 })} />);
      const input = within(fieldOf('超时 (秒)')).getByRole('spinbutton');

      fireEvent.change(input, { target: { value: '' } });
      fireEvent.blur(input);

      expect(input).toHaveValue(45);
    });

    it('超时填 0 落回默认 30 秒（想要不限须清空输入框）', () => {
      const onUpdateStep = vi.fn();
      render(<Harness onUpdateStep={onUpdateStep} />);

      // parseInt('0') 为 falsy，走 || 30 分支
      fireEvent.change(within(fieldOf('超时 (秒)')).getByRole('spinbutton'), {
        target: { value: '0' },
      });

      expect(lastStep(onUpdateStep).timeout_seconds).toBe(30);
    });

    it('重试次数夹在 0..5', () => {
      const onUpdateStep = vi.fn();
      render(<Harness onUpdateStep={onUpdateStep} />);

      fireEvent.change(within(fieldOf('重试次数')).getByRole('spinbutton'), {
        target: { value: '9' },
      });
      expect(lastStep(onUpdateStep).retry).toBe(5);

      fireEvent.change(within(fieldOf('重试次数')).getByRole('spinbutton'), {
        target: { value: '-2' },
      });
      expect(lastStep(onUpdateStep).retry).toBe(0);
    });

    it('启用开关在 enabled 缺省时视为已启用', () => {
      const onUpdateStep = vi.fn();
      render(<Harness step={makeStep({ enabled: undefined })} onUpdateStep={onUpdateStep} />);

      fireEvent.click(screen.getByLabelText('禁用步骤'));

      expect(lastStep(onUpdateStep).enabled).toBe(false);
      expect(screen.getByLabelText('启用步骤')).toBeInTheDocument();
    });

    it('step_id 可直接编辑', () => {
      const onUpdateStep = vi.fn();
      render(<Harness onUpdateStep={onUpdateStep} />);

      fireEvent.change(within(fieldOf('step_id')).getByRole('textbox'), {
        target: { value: 'init_renamed' },
      });

      expect(lastStep(onUpdateStep).step_id).toBe('init_renamed');
    });

    it('编辑执行配置不会丢掉编辑器不暴露的停滞钟', () => {
      const onUpdateStep = vi.fn();
      render(<Harness step={makeStep({ stall_seconds: 120 })} onUpdateStep={onUpdateStep} />);

      fireEvent.change(within(fieldOf('重试次数')).getByRole('spinbutton'), {
        target: { value: '2' },
      });

      expect(lastStep(onUpdateStep).stall_seconds).toBe(120);
    });
  });

  describe('超时语义提示', () => {
    it('常规正数超时不出提示', () => {
      render(<Harness step={makeStep({ timeout_seconds: 600 })} />);
      expect(screen.queryByTestId('step-timeout-hint')).not.toBeInTheDocument();
    });

    it('0 提示为不限，并说明本编辑器存不下去', () => {
      render(<Harness step={makeStep({ timeout_seconds: 0 })} />);
      const hint = screen.getByTestId('step-timeout-hint');
      expect(within(hint).getByText('∞')).toBeInTheDocument();
      expect(hint).toHaveTextContent('不限');
      expect(hint).toHaveTextContent('本编辑器不提供停滞钟字段');
    });

    it('缺省提示为回落默认，而不是不限', () => {
      render(
        <Harness step={makeStep({ timeout_seconds: undefined as unknown as number })} />,
      );
      const hint = screen.getByTestId('step-timeout-hint');
      expect(within(hint).getByText('默认')).toBeInTheDocument();
      expect(hint).toHaveTextContent('STP_STEP_WALL_CLOCK_SECONDS');
      expect(hint).not.toHaveTextContent('本编辑器不提供停滞钟字段');
    });
  });

  describe('跳转链接', () => {
    it('有脚本名时给出脚本管理页深链', () => {
      render(<Harness />);
      expect(screen.getByText('在脚本管理中编辑参数').closest('a')).toHaveAttribute(
        'href',
        '/scripts?name=install_apk',
      );
    });

    it('没有脚本名时不渲染链接', () => {
      render(<Harness step={makeStep({ action: 'script:', version: '' })} />);
      expect(screen.queryByText('在脚本管理中编辑参数')).not.toBeInTheDocument();
    });
  });

  describe('只读模式', () => {
    it('所有输入控件禁用', () => {
      render(<Harness readOnly />);
      expect(within(fieldOf('脚本名')).getByRole('combobox')).toBeDisabled();
      expect(within(fieldOf('版本')).getByRole('combobox')).toBeDisabled();
      expect(within(fieldOf('retries')).getByRole('spinbutton')).toBeDisabled();
      expect(within(fieldOf('超时 (秒)')).getByRole('spinbutton')).toBeDisabled();
      expect(within(fieldOf('step_id')).getByRole('textbox')).toBeDisabled();
      expect(screen.getByLabelText('禁用步骤')).toBeDisabled();
    });
  });
});
