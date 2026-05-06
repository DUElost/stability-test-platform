import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import PlanRunMatrixPage from './PlanRunMatrixPage';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getPlanRun: vi.fn(),
  listJobs: vi.fn(),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
    useParams: () => ({ runId: '12' }),
  };
});

vi.mock('@/utils/api', () => ({
  api: {
    planRuns: {
      get: mocks.getPlanRun,
      listJobs: mocks.listJobs,
    },
  },
}));

vi.mock('@/hooks/useSocketIO', () => ({
  useSocketIO: () => ({ lastMessage: null }),
}));

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <PlanRunMatrixPage />
    </QueryClientProvider>,
  );
}

describe('PlanRunMatrixPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getPlanRun.mockResolvedValue({
      id: 12,
      plan_id: 5,
      run_type: 'MANUAL',
      status: 'SUCCESS',
    });
    mocks.listJobs.mockResolvedValue([
      {
        id: 3064,
        device_id: 9,
        device_serial: 'DEV-3064',
        host_id: 1,
        status: 'COMPLETED',
        step_traces: [],
      },
    ]);
  });

  it('opens the Job report instead of the legacy task details route', async () => {
    renderPage();

    fireEvent.click(await screen.findByTitle(/Job #3064/));
    fireEvent.click(screen.getByRole('button', { name: /查看运行报告/ }));

    expect(mocks.navigate).toHaveBeenCalledWith('/runs/3064/report');
    expect(mocks.navigate).not.toHaveBeenCalledWith('/tasks/3064');
  });
});
