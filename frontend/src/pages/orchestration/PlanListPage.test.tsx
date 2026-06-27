import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { ConfirmProvider } from '@/hooks/useConfirm';
import PlanListPage from './PlanListPage';
import type { Plan } from '@/utils/api';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  listPlans: vi.fn(),
  createPlan: vi.fn(),
  deletePlan: vi.fn(),
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
    },
  },
}));

vi.mock('@/components/ui/toast', () => ({
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

const mockPlan = (overrides?: Partial<Plan>): Plan => ({
  id: 1,
  name: 'Smoke Test Plan',
  description: 'Daily smoke tests',
  failure_threshold: 0.05,
  next_plan_id: null,
  created_by: 'alice',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-02T00:00:00Z',
  steps: [
    {
      id: 1,
      step_key: 'step-1',
      script_name: 'smoke.sh',
      script_version: '1.0.0',
      stage: 'patrol',
      sort_order: 0,
      retry: 0,
      enabled: true,
    },
  ],
  ...overrides,
});

describe('PlanListPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listPlans.mockResolvedValue([]);
    mocks.deletePlan.mockResolvedValue({ deleted: 1 });
  });

  it('opens the new-plan editor without creating an empty Plan', async () => {
    renderPage();

    await screen.findByText('Plan 编排');
    const newPlanButtons = screen.getAllByRole('button', { name: /新建 Plan/ });
    fireEvent.click(newPlanButtons[0]);

    expect(mocks.navigate).toHaveBeenCalledWith('/orchestration/plans/new');
    await waitFor(() => expect(mocks.createPlan).not.toHaveBeenCalled());
  });

  it('renders plan names and details from the API', async () => {
    mocks.listPlans.mockResolvedValue([mockPlan()]);
    renderPage();

    await screen.findByText('Smoke Test Plan');
    expect(screen.getByText('Daily smoke tests')).toBeInTheDocument();
    expect(screen.getByText(/1 步骤/)).toBeInTheDocument();
    expect(screen.getByText(/阈值 5%/)).toBeInTheDocument();
  });
});
