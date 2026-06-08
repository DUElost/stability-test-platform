import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { ConfirmProvider } from '@/hooks/useConfirm';
import PlanListPage from './PlanListPage';

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

describe('PlanListPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listPlans.mockResolvedValue([]);
    mocks.deletePlan.mockResolvedValue({ deleted: 1 });
  });

  it('opens the new-plan editor without creating an empty Plan', async () => {
    renderPage();

    await screen.findByText('Plan 编排');
    fireEvent.click(screen.getByRole('button', { name: /新建 Plan/ }));

    expect(mocks.navigate).toHaveBeenCalledWith('/orchestration/plans/new');
    await waitFor(() => expect(mocks.createPlan).not.toHaveBeenCalled());
  });
});
