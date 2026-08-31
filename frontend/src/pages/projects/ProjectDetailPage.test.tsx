import { render, screen, waitFor, fireEvent } from '@testing-library/react';
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
    projects: { get: mocks.getProject, modelsOf: mocks.modelsOf, update: mocks.updateProject, removeRule: mocks.removeRule, rename: mocks.renameProject, archive: mocks.archiveProject, customers: mocks.customers },
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
    mocks.listPlans.mockResolvedValue([
      { id: 1, name: 'Plan A', steps: [], project_key: 'proj-a' },
    ]);
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

  it('renames project via edit dialog key input', async () => {
    mocks.renameProject.mockResolvedValue(makeDetail({ project_key: 'HONOR-ELA2' }));
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
});
