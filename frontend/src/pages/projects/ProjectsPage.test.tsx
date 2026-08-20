import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import ProjectsPage from './ProjectsPage';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  listProjects: vi.fn(),
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
    projects: { list: mocks.listProjects },
  },
}));

function makeProject(overrides: Record<string, unknown> = {}) {
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
    device_count: 3,
    running_run_count: 1,
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ProjectsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('ProjectsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listProjects.mockResolvedValue([
      makeProject(),
      makeProject({
        project_key: 'LEGACY',
        display_name: 'Legacy',
        customer: null,
        platform: null,
        form_factor: null,
        product_line: null,
        device_count: 0,
        running_run_count: 0,
      }),
    ]);
  });

  it('renders project cards with facet badges and counts', async () => {
    renderPage();

    expect(await screen.findByText('Project A')).toBeInTheDocument();
    expect(screen.getByText('proj-a')).toBeInTheDocument();
    expect(screen.getByText('客户: CustA')).toBeInTheDocument();
    expect(screen.getByText('平台: MTK')).toBeInTheDocument();
    expect(screen.getByText('3 台设备')).toBeInTheDocument();
    expect(screen.getByText('Legacy')).toBeInTheDocument();
    // 顶部统计：2 项目 / 3 设备 / 1 在跑
    expect(await screen.findByText('2')).toBeInTheDocument();
  });

  it('filters cards by facet selection', async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Project A');
    // 选客户 = CustA → LEGACY（customer 为 NULL）被过滤
    await user.click(screen.getByTestId('facet-customer'));
    await user.click(await screen.findByText('CustA'));

    await waitFor(() => {
      expect(screen.getByText('Project A')).toBeInTheDocument();
      expect(screen.queryByText('Legacy')).not.toBeInTheDocument();
    });
  });

  it('navigates to project detail on card click', async () => {
    const user = userEvent.setup();
    renderPage();

    const card = (await screen.findByText('Project A')).closest('[data-testid="project-card"]');
    expect(card).not.toBeNull();
    await user.click(card as HTMLElement);

    expect(mocks.navigate).toHaveBeenCalledWith('/projects/proj-a');
  });

  it('shows empty state when no projects exist', async () => {
    mocks.listProjects.mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText('暂无项目')).toBeInTheDocument();
  });

  it('shows no-match state when facet filters out everything', async () => {
    const user = userEvent.setup();
    // 组合筛选筛空：proj-a 是 CustA/MTK，选客户=CustA + 平台=QCOM → 无交集
    mocks.listProjects.mockResolvedValue([
      makeProject(),
      makeProject({
        project_key: 'proj-b',
        display_name: 'Project B',
        customer: 'CustB',
        platform: 'QCOM',
      }),
    ]);
    renderPage();

    await screen.findByText('Project A');
    await user.click(screen.getByTestId('facet-customer'));
    await user.click(await screen.findByRole('option', { name: 'CustA' }));
    await user.click(screen.getByTestId('facet-platform'));
    await user.click(await screen.findByRole('option', { name: 'QCOM' }));

    expect(await screen.findByText('没有匹配的项目')).toBeInTheDocument();
  });
});
