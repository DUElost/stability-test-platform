import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { ConfirmProvider } from '@/hooks/useConfirm';
import PlanListPage from './PlanListPage';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  listPlans: vi.fn(),
  createPlan: vi.fn(),
  deletePlan: vi.fn(),
  listProjects: vi.fn(),
  listSpecialties: vi.fn(),
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
    plans: {
      list: mocks.listPlans,
      create: mocks.createPlan,
      delete: mocks.deletePlan,
      listSpecialties: mocks.listSpecialties,
    },
    // ADR-0029：项目筛选下拉（ProjectFilterSelect）依赖
    projects: { list: mocks.listProjects },
  },
  toApiError: (error: unknown) => ({
    message: error instanceof Error ? error.message : '请求失败',
    status: undefined,
  }),
}));

vi.mock('@/hooks/useToast', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
  }),
}));

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <ConfirmProvider>
        <PlanListPage />
      </ConfirmProvider>
    </QueryClientProvider>,
  );
}

describe('PlanListPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listPlans.mockResolvedValue([]);
    mocks.deletePlan.mockResolvedValue({ deleted: 1 });
    mocks.listProjects.mockResolvedValue([]);
    mocks.listSpecialties.mockResolvedValue([
      { key: 'mtbf', display_name: 'MTBF', sort_order: 1 },
    ]);
  });

  it('opens the new-plan editor without creating an empty Plan', async () => {
    renderPage();

    await screen.findByText('Plan 编排');
    const newPlanButtons = screen.getAllByRole('button', { name: /新建 Plan/ });
    fireEvent.click(newPlanButtons[0]);

    expect(mocks.navigate).toHaveBeenCalledWith('/orchestration/plans/new');
    await waitFor(() => expect(mocks.createPlan).not.toHaveBeenCalled());
  });

  it('renders query errors with a retry instead of an empty list', async () => {
    mocks.listPlans
      .mockRejectedValueOnce(new Error('database unavailable'))
      .mockResolvedValueOnce([]);

    renderPage();

    expect(await screen.findByText('加载 Plan 列表失败')).toBeInTheDocument();
    expect(screen.queryByText('还没有 Plan')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));

    await waitFor(() => expect(mocks.listPlans).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('还没有 Plan')).toBeInTheDocument();
  });

  it('filters the list by specialty via the native select (#448)', async () => {
    mocks.listPlans.mockResolvedValue([
      { id: 1, name: 'MLD-MTBF', steps: [], created_at: '2026-08-26T00:00:00Z', updated_at: '2026-08-26T00:00:00Z', specialty_key: 'mtbf' },
      { id: 2, name: '裸 Plan', steps: [], created_at: '2026-08-26T00:00:00Z', updated_at: '2026-08-26T00:00:00Z', specialty_key: null },
    ]);

    renderPage();
    expect(await screen.findByText('MLD-MTBF')).toBeInTheDocument();
    // 初始不传 specialty_key
    expect(mocks.listPlans).toHaveBeenLastCalledWith(0, 100, undefined, undefined);

    fireEvent.change(screen.getByTestId('plan-specialty-filter'), {
      target: { value: 'mtbf' },
    });

    await waitFor(() =>
      expect(mocks.listPlans).toHaveBeenLastCalledWith(0, 100, undefined, 'mtbf'),
    );
  });
});

describe('PlanListPage grouping', () => {
  it('groups plans by project key with group headers', async () => {
    mocks.listPlans.mockResolvedValue([
      { id: 1, name: 'MTBF-A', steps: [], project_key: 'V552AA', specialty_key: 'mtbf',
        created_at: '2026-08-26T00:00:00Z', updated_at: '2026-08-26T00:00:00Z' },
      { id: 2, name: 'MTBF-B', steps: [], project_key: 'V552AA', specialty_key: 'mtbf',
        created_at: '2026-08-26T00:00:00Z', updated_at: '2026-08-26T00:00:00Z' },
      { id: 3, name: 'Ops-C', steps: [], project_key: 'A57', specialty_key: 'ops',
        created_at: '2026-08-26T00:00:00Z', updated_at: '2026-08-26T00:00:00Z' },
    ]);
    renderPage();

    expect(await screen.findByText('MTBF-A')).toBeInTheDocument();
    // 二维分组：#448——两个组标题 + 组内计数
    const groupV = screen.getByTestId('plan-group-V552AA');
    expect(groupV).toHaveTextContent('V552AA（2）');
    expect(within(groupV).getByText('MTBF-A')).toBeInTheDocument();
    expect(within(groupV).getByText('MTBF-B')).toBeInTheDocument();
    const groupA = screen.getByTestId('plan-group-A57');
    expect(groupA).toHaveTextContent('A57（1）');
    expect(within(groupA).getByText('Ops-C')).toBeInTheDocument();
  });
});
