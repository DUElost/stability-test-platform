import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ProjectDetailPage from './ProjectDetailPage';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getProject: vi.fn(),
  updateProject: vi.fn(),
  listDevices: vi.fn(),
  listPlans: vi.fn(),
  modelsOf: vi.fn(),
  riskTrend: vi.fn(),
  removeRule: vi.fn(),
  renameProject: vi.fn(),
  archiveProject: vi.fn(),
  unarchiveProject: vi.fn(),
  customers: vi.fn(),
  authRole: 'admin',
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
  };
});

vi.mock('@/hooks/useAuthSession', () => ({
  useAuthSession: () => ({ data: { role: mocks.authRole } }),
}));

vi.mock('@/hooks/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}));

vi.mock('@/utils/api', () => ({
  api: {
    projects: { get: mocks.getProject, modelsOf: mocks.modelsOf, update: mocks.updateProject, removeRule: mocks.removeRule, rename: mocks.renameProject, archive: mocks.archiveProject, unarchive: mocks.unarchiveProject, customers: mocks.customers },
    devices: { list: mocks.listDevices },
    plans: { list: mocks.listPlans },
    results: { riskTrend: mocks.riskTrend },
  },
  toApiError: (error: unknown) => ({
    message: error instanceof Error ? error.message : '请求失败',
    status: (error as { status?: number })?.status,
  }),
}));

function makeDetail(overrides: Record<string, unknown> = {}) {
  return {
    project_key: 'proj-a',
    display_name: 'Project A',
    jira_project_key: null,
    product_line: 'Sonic',
    customer: 'CustA',
    platform: 'MTK',
    form_factor: 'PHONE',
    status: 'ACTIVE',
    source: 'USER',
    match_models: [],
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    device_count: 1,
    running_run_count: 0,
    plan_count: 1,
    total_run_count: 1,
    recent_runs: [],
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/projects/proj-a']}>
        <Routes>
          <Route path="/projects/:projectKey" element={<ProjectDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('ProjectDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.authRole = 'admin';
    mocks.getProject.mockResolvedValue(makeDetail());
    mocks.modelsOf.mockResolvedValue([
      { model: 'M1', device_count: 1, platforms: ['MTK'] },
    ]);
    mocks.listDevices.mockResolvedValue({
      items: [{ id: 1, serial: 'S-1', model: 'M1', status: 'ONLINE', project_key: 'proj-a' }],
      total: 1,
    });
    mocks.listPlans.mockResolvedValue({
      items: [{ id: 1, name: 'Plan A', steps: [], project_key: 'proj-a' }],
      total: 1,
    });
    mocks.riskTrend.mockResolvedValue({
      project_key: 'proj-a',
      days: 30,
      buckets: [],
    });
    mocks.customers.mockResolvedValue([
      { key: '荣耀', display_name: '荣耀', sort_order: 1 },
    ]);
  });

  it('renders four blocks with facet badges and jira placeholder', async () => {
    renderPage();

    expect(await screen.findByText('Project A')).toBeInTheDocument();
    expect(screen.getByText('客户: CustA')).toBeInTheDocument();
    expect(screen.getByTestId('jira-not-configured')).toBeInTheDocument();
    // 四块标题
    expect(screen.getByText(/设备（1）/)).toBeInTheDocument();
    expect(screen.getByText(/计划（1）/)).toBeInTheDocument();
    expect(screen.getByText('最近 30 天')).toBeInTheDocument();
    // P2-11：归属规则提为主块（JIRA 占位卡片已删，头部 badge 保留）
    expect(screen.getByTestId('detail-rules')).toBeInTheDocument();
    expect(screen.queryByText('JIRA 集成')).not.toBeInTheDocument();
    // 空态（暂无运行数据）
    expect(screen.getByText(/暂无运行数据/)).toBeInTheDocument();
  });

  it('shows jira key badge when configured', async () => {
    mocks.getProject.mockResolvedValue(makeDetail({ jira_project_key: 'STP' }));
    renderPage();

    expect(await screen.findByText('JIRA: STP')).toBeInTheDocument();
    expect(screen.queryByTestId('jira-not-configured')).not.toBeInTheDocument();
  });

  it('renames then updates with the new key, serially (#1708)', async () => {
    mocks.renameProject.mockResolvedValue(makeDetail({ project_key: 'HONOR-ELA2' }));
    mocks.updateProject.mockResolvedValue(makeDetail({ project_key: 'HONOR-ELA2' }));
    renderPage();
    await screen.findByText('Project A');

    fireEvent.click(screen.getByTestId('edit-project-open'));
    const keyInput = (await screen.findByTestId('edit-project-key')) as HTMLInputElement;
    fireEvent.change(keyInput, { target: { value: 'HONOR-ELA2' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(mocks.renameProject).toHaveBeenCalledWith('proj-a', 'HONOR-ELA2');
      expect(mocks.navigate).toHaveBeenCalledWith('/projects/HONOR-ELA2');
    });
    // 串行：rename 先落地，update 必须用**新 key**；并发/旧 key 会让字段静默丢失
    expect(mocks.updateProject).toHaveBeenCalledWith(
      'HONOR-ELA2',
      expect.objectContaining({ display_name: 'Project A' }),
    );
    expect(mocks.updateProject).not.toHaveBeenCalledWith('proj-a', expect.anything());
    expect(mocks.renameProject.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.updateProject.mock.invocationCallOrder[0],
    );
  });

  it('does not update fields when rename fails (#1708)', async () => {
    mocks.renameProject.mockRejectedValue(new Error('项目标识已存在'));
    renderPage();
    await screen.findByText('Project A');

    fireEvent.click(screen.getByTestId('edit-project-open'));
    const keyInput = (await screen.findByTestId('edit-project-key')) as HTMLInputElement;
    fireEvent.change(keyInput, { target: { value: 'TAKEN' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(mocks.renameProject).toHaveBeenCalled());
    expect(mocks.updateProject).not.toHaveBeenCalled();
    expect(mocks.navigate).not.toHaveBeenCalled();
    // 失败不关窗：用户输入保留，可修正 key 后重试
    expect(screen.getByTestId('edit-project-key')).toBeInTheDocument();
  });

  it('navigates to the new key when update fails after rename (#1708)', async () => {
    mocks.renameProject.mockResolvedValue(makeDetail({ project_key: 'HONOR-ELA2' }));
    mocks.updateProject.mockRejectedValue(new Error('server boom'));
    renderPage();
    await screen.findByText('Project A');

    fireEvent.click(screen.getByTestId('edit-project-open'));
    const keyInput = (await screen.findByTestId('edit-project-key')) as HTMLInputElement;
    fireEvent.change(keyInput, { target: { value: 'HONOR-ELA2' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    // rename 已生效：即便字段保存失败也必须跳新 key，不能停在失效的旧 URL
    await waitFor(() => {
      expect(mocks.updateProject).toHaveBeenCalledWith('HONOR-ELA2', expect.anything());
      expect(mocks.navigate).toHaveBeenCalledWith('/projects/HONOR-ELA2');
    });
  });

  it('archives project with confirm on admin', async () => {
    mocks.archiveProject.mockResolvedValue(makeDetail({ status: 'ARCHIVED' }));
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    renderPage();
    await screen.findByText('Project A');
    fireEvent.click(screen.getByTestId('archive-project-open'));

    expect(confirmSpy).toHaveBeenCalled();
    await waitFor(() => {
      expect(mocks.archiveProject).toHaveBeenCalledWith('proj-a');
    });
    confirmSpy.mockRestore();
  });

  it('unarchives an archived project on admin', async () => {
    mocks.getProject.mockResolvedValue(makeDetail({ status: 'ARCHIVED' }));
    mocks.unarchiveProject.mockResolvedValue(makeDetail({ status: 'ACTIVE' }));

    renderPage();
    await screen.findByText('Project A');
    // ARCHIVED 态：归档按钮消失、恢复按钮出现
    expect(screen.queryByTestId('archive-project-open')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('unarchive-project-open'));

    await waitFor(() => {
      expect(mocks.unarchiveProject).toHaveBeenCalledWith('proj-a');
    });
  });

  it('removes a rule with confirm on admin', async () => {
    mocks.getProject.mockResolvedValue(makeDetail({ match_models: ['M1'] }));
    mocks.removeRule.mockResolvedValue({ project_key: 'proj-a', model: 'M1' });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    renderPage();
    await screen.findByText('Project A');
    fireEvent.click(screen.getByLabelText('移除规则 M1'));

    expect(confirmSpy).toHaveBeenCalled();
    await waitFor(() => {
      expect(mocks.removeRule).toHaveBeenCalledWith('proj-a', 'M1');
    });
    confirmSpy.mockRestore();
  });

  it('skips remove when confirm dismissed', async () => {
    mocks.getProject.mockResolvedValue(makeDetail({ match_models: ['M1'] }));
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

    renderPage();
    await screen.findByText('Project A');
    fireEvent.click(screen.getByLabelText('移除规则 M1'));
    expect(mocks.removeRule).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('renders devices / plans / risk trend from scoped queries', async () => {
    renderPage();

    expect(await screen.findByText('S-1')).toBeInTheDocument();
    expect(await screen.findByText('Plan A')).toBeInTheDocument();
    expect(await screen.findByTestId('detail-kpi-strip')).toBeInTheDocument();
    expect(await screen.findByTestId('hanging-models')).toHaveTextContent(
      '当前归属此项目的设备型号：M1 (1)',
    );
    expect(screen.queryByTestId('seed-disclaimer')).not.toBeInTheDocument();
    await waitFor(() => {
      expect(mocks.listDevices).toHaveBeenCalledWith(0, 20, undefined, undefined, 'proj-a');
      expect(mocks.listPlans).toHaveBeenCalledWith(0, 20, 'proj-a');
      expect(mocks.riskTrend).toHaveBeenCalledWith('proj-a', 30);
      expect(mocks.modelsOf).toHaveBeenCalledWith('proj-a');
    });
  });

  it('navigates to plan run detail when clicking an S-level event row', async () => {
    mocks.riskTrend.mockResolvedValue({
      project_key: 'proj-a',
      days: 30,
      buckets: [],
      s_runs: [{ run_id: 77, started_at: '2026-06-01T00:00:00Z', status: 'FAILED' }],
    });
    renderPage();

    const row = await screen.findByText('#77');
    fireEvent.click(row.closest('button')!);

    expect(mocks.navigate).toHaveBeenCalledWith('/execution/plan-runs/77');
  });

  it('renders 404 as error state with back-to-list action, not empty data', async () => {
    mocks.getProject.mockRejectedValue(Object.assign(new Error('project not found'), { status: 404 }));
    renderPage();

    // 约束 2：未知 key 是路由错误，按错误态渲染
    expect(await screen.findByText('项目不存在')).toBeInTheDocument();
    expect(screen.getByText(/项目 "proj-a" 不存在/)).toBeInTheDocument();
    expect(screen.queryByText('该项目暂无设备')).not.toBeInTheDocument();
    // 返回列表入口
    const backButton = screen.getByRole('button', { name: /返回项目列表/ });
    expect(backButton).toBeInTheDocument();
    fireEvent.click(backButton);
    expect(mocks.navigate).toHaveBeenCalledWith('/projects');
  });

  it('shows seed disclaimer when opening a backfill key by URL', async () => {
    mocks.getProject.mockResolvedValue(makeDetail({
      project_key: 'HONOR-MLD',
      display_name: '荣耀 MLD 系列',
      source: 'SEED',
    }));
    renderPage();
    expect(await screen.findByTestId('seed-disclaimer')).toHaveTextContent(
      '不能代表客户、项目或机型',
    );
    expect(screen.queryByText('已映射型号：')).not.toBeInTheDocument();
  });

  it('admin opens prefilled edit dialog and submits updated jira key', async () => {
    mocks.getProject.mockResolvedValue(makeDetail({ jira_project_key: 'OLD' }));
    mocks.updateProject.mockResolvedValue(makeDetail({ jira_project_key: 'VFFCA' }));
    renderPage();

    fireEvent.click(await screen.findByTestId('edit-project-open'));
    const input = (await screen.findByTestId('edit-project-jira')) as HTMLInputElement;
    expect(input.value).toBe('OLD');
    fireEvent.change(input, { target: { value: 'VFFCA' } });
    // ADR-0029 D12：customer 编辑框带字典下拉建议
    const customerInput = screen.getByTestId('edit-project-customer');
    expect(customerInput).toHaveAttribute('list', 'edit-project-customer-options');
    await waitFor(() => {
      const option = document.querySelector(
        '#edit-project-customer-options option',
      );
      expect(option?.getAttribute('value')).toBe('荣耀');
    });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(mocks.updateProject).toHaveBeenCalledWith(
        'proj-a',
        expect.objectContaining({
          display_name: 'Project A',
          jira_project_key: 'VFFCA',
        }),
      );
    });
    // key 未改：不得再单独发 rename 请求（#1708 单回调语义）
    expect(mocks.renameProject).not.toHaveBeenCalled();
    // 成功后关窗并失效详情缓存 → getProject 至少重新拉取一次
    await waitFor(() => {
      expect(mocks.getProject.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
    await waitFor(() => {
      expect(screen.queryByTestId('edit-project-jira')).not.toBeInTheDocument();
    });
  });

  it('blank jira key submits explicit null (PUT fields_set 清空语义)', async () => {
    mocks.updateProject.mockResolvedValue(makeDetail());
    renderPage();

    fireEvent.click(await screen.findByTestId('edit-project-open'));
    const input = await screen.findByTestId('edit-project-jira');
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(mocks.updateProject).toHaveBeenCalledWith(
        'proj-a',
        expect.objectContaining({ jira_project_key: null }),
      );
    });
  });

  it('non-admin does not see edit entry', async () => {
    mocks.authRole = 'viewer';
    renderPage();
    await screen.findByText('Project A');
    expect(screen.queryByTestId('edit-project-open')).not.toBeInTheDocument();
  });

  it('#958: models list refreshes immediately after rule removal', async () => {
    mocks.getProject.mockResolvedValue(makeDetail({ match_models: ['M1'] }));
    mocks.removeRule.mockResolvedValue({ project_key: 'proj-a', model: 'M1' });
    mocks.modelsOf
      .mockResolvedValueOnce([{ model: 'M1', device_count: 1, platforms: ['MTK'] }])
      .mockResolvedValueOnce([]);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    renderPage();
    await screen.findByText('当前归属此项目的设备型号：M1 (1)');

    fireEvent.click(screen.getByLabelText('移除规则 M1'));

    await waitFor(() => {
      expect(
        screen.queryByText('当前归属此项目的设备型号：M1 (1)'),
      ).not.toBeInTheDocument();
    });
    confirmSpy.mockRestore();
  });

  // #3194：原始计数（含退役/legacy）>20 而过滤后 total ≤20 时，列表其实已全量——
  // 不得再报截断；标题保留原始计数必须注明口径。
  it('does not show the truncation banner when the filtered total fits even if the raw count exceeds it (#3194)', async () => {
    mocks.getProject.mockResolvedValue(makeDetail({ device_count: 25, plan_count: 25 }));
    mocks.listDevices.mockResolvedValue({
      items: [
        { id: 1, serial: 'S-1', model: 'M1', status: 'ONLINE', project_key: 'proj-a' },
        { id: 2, serial: 'S-2', model: 'M1', status: 'ONLINE', project_key: 'proj-a' },
      ],
      total: 18,
    });
    mocks.listPlans.mockResolvedValue({
      items: [{ id: 1, name: 'Plan A', steps: [], project_key: 'proj-a' }],
      total: 18,
    });
    renderPage();

    expect(await screen.findByText(/设备（25）/)).toBeInTheDocument();
    expect(screen.getByText(/计划（25）/)).toBeInTheDocument();
    // 过滤后 18 ≤ 20：列表已全量，无截断横幅
    expect(screen.queryByText(/此处最多显示/)).not.toBeInTheDocument();
    // 标题原始计数与列表口径不一致 → 注明含未显示部分
    expect(screen.getByTestId('devices-count-scope')).toHaveTextContent('含退役未显示');
    expect(screen.getByTestId('plans-count-scope')).toHaveTextContent('含 legacy 未显示');
  });

  it('shows the truncation banner when the filtered total exceeds the preview limit (#3194)', async () => {
    mocks.getProject.mockResolvedValue(makeDetail({ device_count: 25, plan_count: 25 }));
    mocks.listDevices.mockResolvedValue({
      items: [
        { id: 1, serial: 'S-1', model: 'M1', status: 'ONLINE', project_key: 'proj-a' },
      ],
      total: 25,
    });
    mocks.listPlans.mockResolvedValue({
      items: [{ id: 1, name: 'Plan A', steps: [], project_key: 'proj-a' }],
      total: 25,
    });
    renderPage();

    // 横幅数字 = 过滤后 total（与列表同口径）
    expect(await screen.findByText(/共 25 台，此处最多显示 20 台/)).toBeInTheDocument();
    expect(screen.getByText(/共 25 个，此处最多显示 20 个/)).toBeInTheDocument();
    // 过滤后与原始一致 → 无口径注
    expect(screen.queryByTestId('devices-count-scope')).not.toBeInTheDocument();
    expect(screen.queryByTestId('plans-count-scope')).not.toBeInTheDocument();
  });

  it('stays quiet when both lists fit within the preview limit', async () => {
    renderPage();

    expect(await screen.findByText(/设备（1）/)).toBeInTheDocument();
    expect(screen.queryByText(/此处最多显示/)).not.toBeInTheDocument();
    // 原始计数与过滤后一致 → 无口径注
    expect(screen.queryByTestId('devices-count-scope')).not.toBeInTheDocument();
    expect(screen.queryByTestId('plans-count-scope')).not.toBeInTheDocument();
  });

  // #3497 F1：查询失败不得读成确定事实——失败态显示失败提示 + 重试，不显示成功空态
  it('shows a load error with retry when the devices query fails, not the empty copy', async () => {
    mocks.listDevices.mockRejectedValue(new Error('network down'));
    renderPage();

    const errorBox = await screen.findByTestId('devices-load-error');
    expect(errorBox).toHaveTextContent('设备列表加载失败');
    expect(screen.queryByText('该项目暂无设备')).not.toBeInTheDocument();
    // 重试恢复：成功后错误态消失、空态按新数据渲染
    mocks.listDevices.mockResolvedValue({ items: [], total: 0 });
    fireEvent.click(within(errorBox).getByRole('button', { name: '重试' }));
    await waitFor(() => {
      expect(screen.queryByTestId('devices-load-error')).not.toBeInTheDocument();
    });
    expect(await screen.findByText('该项目暂无设备')).toBeInTheDocument();
  });

  it('shows a load error when the plans query fails, not the empty copy', async () => {
    mocks.listPlans.mockRejectedValue(new Error('network down'));
    renderPage();

    const errorBox = await screen.findByTestId('plans-load-error');
    expect(errorBox).toHaveTextContent('计划列表加载失败');
    expect(screen.queryByText('该项目暂无计划')).not.toBeInTheDocument();
    expect(within(errorBox).getByRole('button', { name: '重试' })).toBeInTheDocument();
  });

  it('shows a load error when the models query fails, not the empty copy', async () => {
    mocks.modelsOf.mockRejectedValue(new Error('network down'));
    renderPage();

    const errorBox = await screen.findByTestId('models-load-error');
    expect(errorBox).toHaveTextContent('归属型号加载失败');
    expect(screen.queryByText('当前没有设备归属此项目')).not.toBeInTheDocument();
    expect(within(errorBox).getByRole('button', { name: '重试' })).toBeInTheDocument();
  });

  it('shows a load error when the risk trend query fails, not 0 values', async () => {
    mocks.riskTrend.mockRejectedValue(new Error('network down'));
    renderPage();

    const errorBox = await screen.findByTestId('risk-trend-load-error');
    expect(errorBox).toHaveTextContent('结果数据加载失败');
    // 不渲染 0 值 KPI，也不渲染成功空态文案
    expect(screen.queryByTestId('success-rate-kpi')).not.toBeInTheDocument();
    expect(screen.queryByText('0%')).not.toBeInTheDocument();
    expect(screen.queryByText(/暂无运行数据/)).not.toBeInTheDocument();
    expect(within(errorBox).getByRole('button', { name: '重试' })).toBeInTheDocument();
  });

  // #3497 F3：0 Run 是「无数据」，成功率渲染成 0% 会与「全部失败」不可分 → 显示「—」
  it('renders an em dash for success rate when total_runs is 0, not 0%', async () => {
    mocks.riskTrend.mockResolvedValue({
      project_key: 'proj-a',
      days: 30,
      buckets: [],
      total_runs: 0,
      success_runs: 0,
      success_rate: 0,
      s_runs: [],
    });
    renderPage();

    expect(await screen.findByTestId('success-rate-kpi')).toHaveTextContent('—');
    expect(screen.queryByText('0%')).not.toBeInTheDocument();
  });

  it('renders the success rate percentage when runs exist', async () => {
    mocks.riskTrend.mockResolvedValue({
      project_key: 'proj-a',
      days: 30,
      buckets: [],
      total_runs: 3,
      success_runs: 2,
      success_rate: 2 / 3,
      s_runs: [],
    });
    renderPage();

    expect(await screen.findByTestId('success-rate-kpi')).toHaveTextContent('67%');
  });
});
