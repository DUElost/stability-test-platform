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
      // #3147：`GET /plans` 现在返回 {items,total,skip,limit}；本文件各处 fixture 仍按
      // 行数组书写，在这一层统一包成新契约（断言看的是行，不该被传输形状污染）。
      list: (async (...a: unknown[]) => {
        const rows = await mocks.listPlans(...a);
        return Array.isArray(rows) ? { items: rows, total: rows.length, skip: 0, limit: 50 } : rows;
      }) as unknown as typeof mocks.listPlans,
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
    // 初始不传 specialty_key（#3195：页大小 = 后端 `le` 上限 200）
    expect(mocks.listPlans).toHaveBeenLastCalledWith(0, 200, undefined, undefined);

    fireEvent.change(screen.getByTestId('plan-specialty-filter'), {
      target: { value: 'mtbf' },
    });

    await waitFor(() =>
      expect(mocks.listPlans).toHaveBeenLastCalledWith(0, 200, undefined, 'mtbf'),
    );
  });

  // #3195（批次 B2 G3，#3497 §4）：带专项筛选后服务端 total 超一页时，翻页请求必须
  // 仍携带筛选参数——丢筛选参数的第二页会按「未过滤集偏移」取行，混入其他专项的计划。
  it('carries the specialty filter on every page when the filtered set spans multiple pages (#3195)', async () => {
    const rows = Array.from({ length: 260 }, (_, i) => ({
      id: i + 1,
      name: `PAGED-MTBF-${i}`,
      steps: [],
      specialty_key: 'mtbf',
      created_at: '2026-08-26T00:00:00Z',
      updated_at: '2026-08-26T00:00:00Z',
    }));
    mocks.listPlans.mockImplementation(async (skip = 0, limit = 50) => ({
      items: rows.slice(skip, skip + limit),
      total: rows.length,
      skip,
      limit,
    }));

    renderPage();
    await screen.findByText('PAGED-MTBF-0');

    fireEvent.change(screen.getByTestId('plan-specialty-filter'), {
      target: { value: 'mtbf' },
    });

    // 筛选生效后的第二页：页大小 200 + 偏移 200 + 原样透传的 specialty_key
    await waitFor(() =>
      expect(mocks.listPlans).toHaveBeenLastCalledWith(200, 200, undefined, 'mtbf'),
    );
    expect(await screen.findByText('PAGED-MTBF-259')).toBeInTheDocument();
  });

  // #748：表格化（606b4350）丢掉了卡片态的「创建者」信息行，数据一直在 Plan.created_by。
  it('renders the plan creator restored from the card layout (#748)', async () => {
    mocks.listPlans.mockResolvedValue([
      { id: 1, name: 'MTBF-CREATOR', created_by: 'alice', steps: [],
        created_at: '2026-08-26T00:00:00Z', updated_at: '2026-08-26T00:00:00Z' },
      { id: 2, name: 'MTBF-ANON', steps: [],
        created_at: '2026-08-26T00:00:00Z', updated_at: '2026-08-26T00:00:00Z' },
    ]);

    renderPage();

    expect(await screen.findByText('MTBF-CREATOR')).toBeInTheDocument();
    expect(screen.getByText('创建者: alice')).toBeInTheDocument();
    // 无 created_by 的 Plan 不渲染占位行（全表仅 1 处「创建者:」）
    expect(screen.getAllByText(/创建者: /)).toHaveLength(1);
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

  // #3195（批次 B2 G3，#3497 §4）：旧行为＝固定单页 `list(0, 100)` 把已取子集当全集——
  // total 越过页大小时尾部行静默消失、KPI 跟着少报（#3147 当时的解法是 KPI 单独读服务端
  // total）。修复＝按服务端 total 翻页取全量后，「已加载条数 ≡ 总数」由翻页本身保证。
  // 260 条 > 页大小 200：两页取全，第 260 条必须出现，KPI 报 260。
  it('fetches every page when the server total exceeds one page, rendering all rows', async () => {
    const rows = Array.from({ length: 260 }, (_, i) => ({
      id: i + 1,
      name: `PLAN-${i}`,
      steps: [],
      created_at: '2026-08-26T00:00:00Z',
      updated_at: '2026-08-26T00:00:00Z',
    }));
    mocks.listPlans.mockImplementation(async (skip = 0, limit = 50) => ({
      items: rows.slice(skip, skip + limit),
      total: rows.length,
      skip,
      limit,
    }));

    renderPage();

    expect(await screen.findByText('PLAN-0')).toBeInTheDocument();
    expect(screen.getByText('PLAN-259')).toBeInTheDocument();
    expect(mocks.listPlans).toHaveBeenCalledWith(0, 200, undefined, undefined);
    expect(mocks.listPlans).toHaveBeenCalledWith(200, 200, undefined, undefined);

    // label 与数值在卡片内是兄弟节点，故定位到 KPI 栅格再断言（不是 label 的直接父级）
    const kpiGrid = screen.getByText('Plan 总数').closest('div.grid');
    expect(kpiGrid).not.toBeNull();
    expect(within(kpiGrid as HTMLElement).getByText('260')).toBeInTheDocument();
  });
});
