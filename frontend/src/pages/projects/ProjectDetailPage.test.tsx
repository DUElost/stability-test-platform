import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ProjectDetailPage from './ProjectDetailPage';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getProject: vi.fn(),
  listDevices: vi.fn(),
  listPlans: vi.fn(),
  resultsSummary: vi.fn(),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
  };
});

vi.mock('@/utils/api', () => ({
  api: {
    projects: { get: mocks.getProject },
    devices: { list: mocks.listDevices },
    plans: { list: mocks.listPlans },
    results: { summary: mocks.resultsSummary },
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
    mocks.getProject.mockResolvedValue(makeDetail());
    mocks.listDevices.mockResolvedValue({
      items: [{ id: 1, serial: 'S-1', model: 'M1', status: 'ONLINE', project_key: 'proj-a' }],
      total: 1,
    });
    mocks.listPlans.mockResolvedValue([
      { id: 1, name: 'Plan A', steps: [], project_key: 'proj-a' },
    ]);
    mocks.resultsSummary.mockResolvedValue({
      runs_by_status: { finished: 1, failed: 0, canceled: 0, running: 0, total: 1 },
      test_type_stats: [],
      risk_distribution: { high: 0, medium: 0, low: 0, unknown: 1 },
      recent_runs: [{
        run_id: 1,
        task_name: 'Plan A',
        task_type: 'Plan A',
        status: 'FINISHED',
        risk_level: 'UNKNOWN',
        project_key: 'proj-a',
        duration_seconds: null,
        started_at: '2026-08-01T00:00:00Z',
        finished_at: null,
      }],
    });
  });

  it('renders four blocks with facet badges and jira placeholder', async () => {
    renderPage();

    expect(await screen.findByText('Project A')).toBeInTheDocument();
    expect(screen.getByText('客户: CustA')).toBeInTheDocument();
    expect(screen.getByTestId('jira-not-configured')).toBeInTheDocument();
    // 四块标题
    expect(screen.getByText(/设备（1）/)).toBeInTheDocument();
    expect(screen.getByText(/计划（1）/)).toBeInTheDocument();
    expect(screen.getByText('最近运行（快照语义：按 plan_run.project_id 归属）')).toBeInTheDocument();
    expect(screen.getByText('JIRA 集成')).toBeInTheDocument();
    // jira 占位文案（约束 3：未配置占位）
    expect(screen.getByTestId('jira-placeholder')).toHaveTextContent('尚未配置 JIRA 项目关键字');
  });

  it('shows jira key badge when configured', async () => {
    mocks.getProject.mockResolvedValue(makeDetail({ jira_project_key: 'STP' }));
    renderPage();

    expect(await screen.findByText('JIRA: STP')).toBeInTheDocument();
    expect(screen.queryByTestId('jira-not-configured')).not.toBeInTheDocument();
  });

  it('renders devices / plans / recent runs from scoped queries', async () => {
    renderPage();

    expect(await screen.findByText('S-1')).toBeInTheDocument();
    // 「Plan A」同时出现在计划块与结果块 recent_runs 里——按数量断言
    expect((await screen.findAllByText('Plan A')).length).toBeGreaterThanOrEqual(2);
    expect(await screen.findByText('#1')).toBeInTheDocument();
    // 各块都走后端 project_key 参数（不是全量再前端过滤）
    await waitFor(() => {
      expect(mocks.listDevices).toHaveBeenCalledWith(0, 20, undefined, undefined, 'proj-a');
      expect(mocks.listPlans).toHaveBeenCalledWith(0, 20, 'proj-a');
      expect(mocks.resultsSummary).toHaveBeenCalledWith(5, 'proj-a');
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
});
