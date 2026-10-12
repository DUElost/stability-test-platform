import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import PlanEditPage from './PlanEditPage';
import { HeaderSlotProvider, useHeaderSlot } from '@/contexts/HeaderSlotContext';
import { api } from '@/utils/api';
import type { ParameterProjection } from '@/utils/api/types';
import { planKeys } from '@/utils/api/queryKeys';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
  };
});

vi.mock('@/components/pipeline/PlanChainPanel', () => ({
  default: () => <div data-testid="plan-chain-panel" />,
}));

vi.mock('@/components/pipeline/PlanCanvas', () => ({
  default: (props: {
    planName: string;
    onPlanNameChange: (name: string) => void;
    description: string;
    onDescriptionChange: (description: string) => void;
    onSpecialtyKeyChange: (key: string) => void;
    onTimeoutChange: (value: number | null) => void;
    planSettings?: {
      barrierMaxWaitSeconds: number | null;
    };
    onPlanSettingsChange?: (next: {
      patrolIntervalSeconds: number | null;
      timeoutSeconds: number | null;
      barrierTimeoutSeconds: number | null;
      barrierMaxWaitSeconds: number | null;
      autoArchiveIntervalSeconds: number | null;
      watcherPolicy: Record<string, unknown> | null;
    }) => void;
  }) => (
    <div data-testid="plan-canvas">
      <button onClick={() => props.onTimeoutChange(11)}>草稿时长11</button>
      <button onClick={() => props.onTimeoutChange(22)}>草稿时长22</button>
      <button
        type="button"
        data-testid="edit-barrier-max-wait"
        onClick={() =>
          props.onPlanSettingsChange?.({
            patrolIntervalSeconds: null,
            timeoutSeconds: 22,
            barrierTimeoutSeconds: null,
            barrierMaxWaitSeconds: 900,
            autoArchiveIntervalSeconds: null,
            watcherPolicy: {},
          })
        }
      >
        草稿硬顶900
      </button>
      <input
        data-testid="plan-name-input"
        aria-label="Plan 名称"
        value={props.planName}
        onChange={(e) => props.onPlanNameChange(e.target.value)}
      />
      <input
        data-testid="plan-description-input"
        aria-label="Plan 描述"
        value={props.description}
        onChange={(e) => props.onDescriptionChange(e.target.value)}
      />
      <button
        type="button"
        onClick={() => props.onSpecialtyKeyChange('SPEC-DRAFT')}
      >
        选择专项草稿
      </button>
    </div>
  ),
}));

vi.mock('@/components/pipeline/PlanStepInspector', () => ({
  default: () => <div data-testid="plan-step-inspector" />,
}));

vi.mock('@/utils/api', () => ({
  // #3147：Plan 编辑器经 usePlanEditForm 拉全量计划（`fetchAllPlans`）
  fetchAllPlans: vi.fn().mockResolvedValue([]),
  api: {
    plans: {
      get: vi.fn(),
      parameterProjection: vi.fn().mockResolvedValue(undefined),
      draftParameterProjection: vi.fn().mockResolvedValue(undefined),
      list: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      listSpecialties: vi.fn(),
    },
    projects: {
      list: vi.fn(),
      listActive: vi.fn(),
    },
    suites: {
      list: vi.fn(),
    },
    scripts: {
      list: vi.fn(),
    },
  },
}));

vi.mock('@/hooks/useToast', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    promise: vi.fn(),
  }),
}));

/** 模拟 AppShell 消费 HeaderSlotContext,把页面注入的顶栏内容渲染到 DOM。 */
function HeaderSlotOutlet() {
  const { headerSlot } = useHeaderSlot();
  return <div>{headerSlot}</div>;
}

function renderPage(path: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });

  render(
    <HeaderSlotProvider>
      <MemoryRouter initialEntries={[path]}>
        <QueryClientProvider client={queryClient}>
          <HeaderSlotOutlet />
          <Routes>
            <Route path="/orchestration/plans/:id" element={<PlanEditPage />} />
          </Routes>
        </QueryClientProvider>
      </MemoryRouter>
    </HeaderSlotProvider>,
  );

  // 供 #967 用例模拟跨端失效（useCrossClientSync 对 planKeys 的 invalidate）。
  return { queryClient };
}

const safeProjection = (marker = 'step_init_1'): ParameterProjection => ({
  layer: 'L1', context: { read_at: '', authority: 'server' }, steps: [], plan_settings: [],
  watcher_policy: { note: '', items: [] }, dispatch_decisions: [],
  safe_debug: { steps: [{ step_key: marker, script_name: 'check_device' }], plan_settings: {}, watcher_policy: {} },
});

describe('PlanEditPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.plans.parameterProjection).mockReset().mockResolvedValue(safeProjection());
    vi.mocked(api.plans.draftParameterProjection).mockReset().mockResolvedValue(safeProjection());
    (api.plans.list as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (api.plans.listSpecialties as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (api.projects.list as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (api.projects.listActive as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (api.suites.list as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (api.scripts.list as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  });

  it('shows loading spinner while an existing plan is fetched', () => {
    let resolveGet: (value: unknown) => void = () => {};
    (api.plans.get as ReturnType<typeof vi.fn>).mockImplementation(
      () => new Promise((resolve) => { resolveGet = resolve; }),
    );

    renderPage('/orchestration/plans/42');

    expect(document.querySelector('.animate-spin')).toBeInTheDocument();

    resolveGet({
      id: 42,
      name: 'Loaded Plan',
      description: '',
      steps: [],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    });
  });

  it('renders detail query failure with a retry action', async () => {
    (api.plans.get as ReturnType<typeof vi.fn>)
      .mockRejectedValueOnce(new Error('plan unavailable'))
      .mockResolvedValueOnce({
        id: 42,
        name: 'Recovered Plan',
        description: '',
        steps: [],
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      });

    renderPage('/orchestration/plans/42');

    expect(await screen.findByText('加载 Plan 详情失败')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));

    expect(await screen.findByText('Recovered Plan')).toBeInTheDocument();
    expect(api.plans.get).toHaveBeenCalledTimes(2);
  });

  // #2384：新 Plan 服务端还没有这条记录——`!isDirty` 只说明「相对空模板没改过」。
  // 三态由本用例与下方两条已存在 Plan 的用例共同锁住：待创建 / 未保存 / 已保存。
  it('shows 待创建 (not 已保存) for a new plan and switches to 未保存 on first edit', async () => {
    renderPage('/orchestration/plans/new');

    expect(await screen.findByText('新建 Plan')).toBeInTheDocument();
    expect(screen.getByText('待创建')).toBeInTheDocument();
    expect(screen.queryByText('已保存')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /创建/ })).toBeDisabled();
    expect(screen.getByTestId('plan-chain-panel')).toBeInTheDocument();
    expect(screen.getByTestId('plan-canvas')).toBeInTheDocument();
    expect(screen.getByTestId('plan-step-inspector')).toBeInTheDocument();

    fireEvent.change(screen.getByTestId('plan-name-input'), { target: { value: 'My Plan' } });

    await waitFor(() => {
      expect(screen.getByText('未保存')).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /创建/ })).not.toBeDisabled();
  });

  it('opens lifecycle JSON dialog from toolbar action', async () => {
    renderPage('/orchestration/plans/new');

    await screen.findByText('新建 Plan');
    fireEvent.click(screen.getByRole('button', { name: /查看 JSON/ }));

    expect(await screen.findByText('Plan 安全参数 JSON')).toBeInTheDocument();
    const jsonPre = document.querySelector('pre');
    expect(jsonPre?.textContent).toContain('step_init_1');
    expect(jsonPre?.textContent).toContain('check_device');
  });

  it('hydrates existing plan name and shows save label', async () => {
    (api.plans.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 7,
      name: 'Nightly',
      description: 'overnight',
      patrol_interval_seconds: 60,
      timeout_seconds: null,
      next_plan_id: null,
      steps: [
        {
          id: 1,
          plan_id: 7,
          step_key: 'step_init_1',
          script_name: 'check_device',
          script_version: '1.0.0',
          stage: 'init',
          sort_order: 0,
          timeout_seconds: 30,
          retry: 0,
          enabled: true,
        },
      ],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    });

    renderPage('/orchestration/plans/7');

    expect(await screen.findByText('Nightly')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /保存修改/ })).toBeDisabled();
    expect(screen.getByText('已保存')).toBeInTheDocument();
  });

  it('navigates back to plan list from header back button', async () => {
    renderPage('/orchestration/plans/new');

    await screen.findByText('新建 Plan');
    fireEvent.click(screen.getByRole('button', { name: '返回 Plan 列表' }));

    expect(mocks.navigate).toHaveBeenCalledWith('/orchestration/plans');
  });

  it('prompts to save before execute when plan has unsaved edits', async () => {
    (api.plans.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 9,
      name: 'Dirty Plan',
      description: '',
      steps: [],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    });
    (api.plans.update as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 9,
      name: 'Dirty Plan v2',
      description: '',
      steps: [],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    });

    renderPage('/orchestration/plans/9');

    await screen.findByText('Dirty Plan');
    fireEvent.change(screen.getByTestId('plan-name-input'), { target: { value: 'Dirty Plan v2' } });
    fireEvent.click(screen.getByRole('button', { name: /发起测试/ }));

    expect(await screen.findByText('有未保存的修改')).toBeInTheDocument();
    expect(screen.getByText('是否先保存当前 Plan 再发起测试？')).toBeInTheDocument();
  });

  it('marks dirty and enables save when only the specialty binding changes (#966)', async () => {
    (api.plans.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 9,
      name: 'Bound Plan',
      description: '',
      specialty_key: 'SPEC-OLD',
      steps: [],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    });

    renderPage('/orchestration/plans/9');

    await screen.findByText('Bound Plan');
    expect(screen.getByText('已保存')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /保存修改/ })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: '选择专项草稿' }));

    await waitFor(() => expect(screen.getByText('未保存')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /保存修改/ })).not.toBeDisabled();
  });

  it('keeps the local draft and offers reload when the plan changes remotely (#967)', async () => {
    const initial = {
      id: 9,
      name: 'Plan v1',
      description: '',
      specialty_key: 'S1',
      steps: [],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    };
    const remote = { ...initial, name: 'Plan v2', description: 'peer edit', updated_at: '2026-01-02T00:00:00Z' };
    (api.plans.get as ReturnType<typeof vi.fn>).mockResolvedValue(initial);
    const { queryClient } = renderPage('/orchestration/plans/9');

    await screen.findByText('Plan v1');
    fireEvent.change(screen.getByTestId('plan-name-input'), { target: { value: 'My Draft' } });
    await waitFor(() => expect(screen.getByText('未保存')).toBeInTheDocument());

    (api.plans.get as ReturnType<typeof vi.fn>).mockResolvedValue(remote);
    await queryClient.invalidateQueries({ queryKey: planKeys.detail(9) });

    expect(await screen.findByText(/已在其他会话更新/)).toBeInTheDocument();
    expect(screen.getByTestId('plan-name-input')).toHaveValue('My Draft');

    fireEvent.click(screen.getByRole('button', { name: '重新加载远端版本' }));

    await waitFor(() => {
      expect(screen.getByTestId('plan-name-input')).toHaveValue('Plan v2');
      expect(screen.getByText('已保存')).toBeInTheDocument();
    });
    expect(screen.queryByText(/已在其他会话更新/)).not.toBeInTheDocument();
  });

  it('continues editing with the original lock base after dismissing the remote notice (#967)', async () => {
    const initial = {
      id: 9,
      name: 'Plan v1',
      description: '',
      specialty_key: 'S1',
      steps: [],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    };
    const remote = { ...initial, name: 'Plan v2', updated_at: '2026-01-02T00:00:00Z' };
    (api.plans.get as ReturnType<typeof vi.fn>).mockResolvedValue(initial);
    const { queryClient } = renderPage('/orchestration/plans/9');

    await screen.findByText('Plan v1');
    fireEvent.change(screen.getByTestId('plan-name-input'), { target: { value: 'My Draft' } });
    await waitFor(() => expect(screen.getByText('未保存')).toBeInTheDocument());

    (api.plans.get as ReturnType<typeof vi.fn>).mockResolvedValue(remote);
    await queryClient.invalidateQueries({ queryKey: planKeys.detail(9) });
    await screen.findByText(/已在其他会话更新/);

    fireEvent.click(screen.getByRole('button', { name: '继续编辑' }));
    expect(screen.queryByText(/已在其他会话更新/)).not.toBeInTheDocument();
    expect(screen.getByTestId('plan-name-input')).toHaveValue('My Draft');

    (api.plans.update as ReturnType<typeof vi.fn>).mockResolvedValue({ ...remote, name: 'My Draft' });
    fireEvent.click(screen.getByRole('button', { name: /保存修改/ }));

    // 草稿基准仍是加载时的版本：乐观锁令牌不因远端刷新而推进
    await waitFor(() => {
      expect(api.plans.update).toHaveBeenCalledWith(
        9,
        expect.objectContaining({ expected_updated_at: '2026-01-01T00:00:00Z' }),
      );
    });
  });

  it('applies a remote change silently when there is no local draft (#967)', async () => {
    const initial = {
      id: 9,
      name: 'Plan v1',
      description: '',
      specialty_key: 'S1',
      steps: [],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    };
    (api.plans.get as ReturnType<typeof vi.fn>).mockResolvedValue(initial);
    const { queryClient } = renderPage('/orchestration/plans/9');

    await screen.findByText('Plan v1');

    (api.plans.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...initial,
      name: 'Plan v2',
      updated_at: '2026-01-02T00:00:00Z',
    });
    await queryClient.invalidateQueries({ queryKey: planKeys.detail(9) });

    await waitFor(() => expect(screen.getByTestId('plan-name-input')).toHaveValue('Plan v2'));
    expect(screen.queryByText(/已在其他会话更新/)).not.toBeInTheDocument();
  });

  describe('描述清空（#1198）', () => {
    const planWithDescription = {
      id: 9,
      name: 'Desc Plan',
      description: '旧描述',
      specialty_key: 'S1',
      steps: [],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    };

    it('清空描述后保存提交空串（不再被省略）', async () => {
      (api.plans.get as ReturnType<typeof vi.fn>).mockResolvedValue(planWithDescription);
      (api.plans.update as ReturnType<typeof vi.fn>).mockResolvedValue({
        ...planWithDescription,
        description: '',
      });
      renderPage('/orchestration/plans/9');

      const descInput = await screen.findByTestId('plan-description-input');
      expect(descInput).toHaveValue('旧描述');
      fireEvent.change(descInput, { target: { value: '' } });
      fireEvent.click(screen.getByRole('button', { name: /保存修改/ }));

      await waitFor(() => {
        expect(api.plans.update).toHaveBeenCalledWith(
          9,
          expect.objectContaining({ description: '' }),
        );
      });
    });

    it('未改描述时保存不携带 description（不误写、不 trim 回写原值）', async () => {
      (api.plans.get as ReturnType<typeof vi.fn>).mockResolvedValue(planWithDescription);
      (api.plans.update as ReturnType<typeof vi.fn>).mockResolvedValue(planWithDescription);
      renderPage('/orchestration/plans/9');

      const nameInput = await screen.findByTestId('plan-name-input');
      fireEvent.change(nameInput, { target: { value: 'Desc Plan v2' } });
      fireEvent.click(screen.getByRole('button', { name: /保存修改/ }));

      await waitFor(() => expect(api.plans.update).toHaveBeenCalled());
      const payload = (api.plans.update as ReturnType<typeof vi.fn>).mock.calls[0][1] as Record<string, unknown>;
      expect(payload.description).toBeUndefined();
    });

    it('新建 Plan 时描述语义不变（空仍省略）', async () => {
      (api.plans.create as ReturnType<typeof vi.fn>).mockResolvedValue({
        id: 42,
        name: 'New Plan',
        description: null,
        steps: [],
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      });
      renderPage('/orchestration/plans/new');

      fireEvent.change(await screen.findByTestId('plan-name-input'), { target: { value: 'New Plan' } });
      fireEvent.click(screen.getByRole('button', { name: '选择专项草稿' }));
      fireEvent.click(screen.getByRole('button', { name: /创建/ }));

      await waitFor(() => expect(api.plans.create).toHaveBeenCalled());
      const payload = (api.plans.create as ReturnType<typeof vi.fn>).mock.calls[0][0] as Record<string, unknown>;
      expect(payload.description).toBeUndefined();
    });
  });
});

describe('B5 U3 编辑器投影代次与安全保存', () => {
  const secret = 'SYNTHETIC_PLAN_EDIT_SECRET_3656';
  const plan = {
    id: 42, name: '安全计划', description: '', specialty_key: 'GENERIC', updated_at: '2026-10-11T00:00:00Z',
    steps: [{ id: 1, plan_id: 42, step_key: 'init_1', script_name: 'connect_wifi', script_version: '1.0.2',
      stage: 'init', sort_order: 0, timeout_seconds: 30, retry: 0, enabled: true,
      params: { password: secret, nested: { token: secret, untouched: 0 } } }],
  };
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.plans.get).mockResolvedValue(plan as never);
    vi.mocked(api.plans.parameterProjection).mockReset().mockResolvedValue(safeProjection('SAVED_SERVER'));
    vi.mocked(api.plans.draftParameterProjection).mockReset().mockResolvedValue(safeProjection('DRAFT_SERVER'));
    vi.mocked(api.plans.update).mockResolvedValue(plan as never);
    vi.mocked(api.plans.list).mockResolvedValue([] as never);
    vi.mocked(api.plans.listSpecialties).mockResolvedValue([]);
    vi.mocked(api.scripts.list).mockResolvedValue([]);
    vi.mocked(api.suites.list).mockResolvedValue([] as never);
    vi.mocked(api.projects.listActive).mockResolvedValue([]);
  });
  it('已保存 JSON 读 L1；错误不回退内部 raw；保存其它字段不改变 secret', async () => {
    renderPage('/orchestration/plans/42');
    await screen.findByText('安全计划');
    await waitFor(() => expect(api.plans.parameterProjection).toHaveBeenCalledWith(42));
    fireEvent.click(screen.getByRole('button', { name: /查看 JSON/ }));
    await waitFor(() => expect(document.querySelector('pre')).toHaveTextContent('SAVED_SERVER'));
    expect(document.body.innerHTML).not.toContain(secret);
    fireEvent.click(screen.getByRole('button', { name: '关闭' }));
    vi.mocked(api.plans.draftParameterProjection).mockRejectedValueOnce(new Error('synthetic projection failure'));
    fireEvent.change(screen.getByTestId('plan-description-input'), { target: { value: '新说明' } });
    await waitFor(() => expect(api.plans.draftParameterProjection).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: /查看 JSON/ }));
    await waitFor(() => expect(document.querySelector('pre')).toHaveTextContent('安全投影不可用'));
    expect(document.body.innerHTML).not.toContain(secret);
    expect(document.querySelector('pre')).not.toHaveTextContent('SAVED_SERVER');
    fireEvent.click(screen.getByRole('button', { name: '关闭' }));
    fireEvent.click(screen.getByRole('button', { name: /保存/ }));
    await waitFor(() => expect(api.plans.update).toHaveBeenCalled());
    const payload = vi.mocked(api.plans.update).mock.calls[0][1];
    // U6：只改说明时省略 steps，避免全量重建；未编辑的 secret 事实由后端保留。
    expect(payload).not.toHaveProperty('steps');
    expect(payload.description).toBe('新说明');
    expect(payload.expected_updated_at).toBe(plan.updated_at);
    expect(document.body.innerHTML).not.toContain(secret);
  });
  it('较旧 draft 响应不覆盖新草稿，等待期间也不显示旧 JSON', async () => {
    let resolveOld!: (value: ParameterProjection) => void;
    let resolveNew!: (value: ParameterProjection) => void;
    renderPage('/orchestration/plans/42');
    await screen.findByText('安全计划');
    await waitFor(() => expect(api.plans.parameterProjection).toHaveBeenCalled());
    vi.mocked(api.plans.draftParameterProjection)
      .mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }))
      .mockImplementationOnce(() => new Promise(resolve => { resolveNew = resolve; }));
    fireEvent.click(screen.getByText('草稿时长11'));
    await waitFor(() => expect(api.plans.draftParameterProjection).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByText('草稿时长22'));
    await waitFor(() => expect(api.plans.draftParameterProjection).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole('button', { name: /查看 JSON/ }));
    expect(document.querySelector('pre')).toHaveTextContent('正在读取安全投影');
    resolveNew(safeProjection('NEW_GENERATION'));
    await waitFor(() => expect(document.querySelector('pre')).toHaveTextContent('NEW_GENERATION'));
    resolveOld(safeProjection('OLD_GENERATION'));
    await waitFor(() => expect(document.querySelector('pre')).not.toHaveTextContent('OLD_GENERATION'));
    expect(document.querySelector('pre')).toHaveTextContent('NEW_GENERATION');
    expect(screen.getByText('L1草稿，未保存')).toBeVisible();
  });
});
