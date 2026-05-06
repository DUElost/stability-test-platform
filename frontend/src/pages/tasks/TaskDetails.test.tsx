import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import TaskDetails from './TaskDetails';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getPlan: vi.fn(),
  listPlanRuns: vi.fn(),
  listJobs: vi.fn(),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
    useParams: () => ({ taskId: '3064' }),
  };
});

vi.mock('../../utils/api', () => ({
  api: {
    plans: {
      get: mocks.getPlan,
    },
    planRuns: {
      list: mocks.listPlanRuns,
      listJobs: mocks.listJobs,
    },
    logs: {
      queryAgent: vi.fn(),
    },
  },
}));

vi.mock('../../utils/api/client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('../../hooks/useSocketIO', () => ({
  useSocketIO: () => ({ lastMessage: null }),
}));

vi.mock('../../components/log/LogViewer', () => ({
  LogViewer: () => <div data-testid="log-viewer" />,
}));

vi.mock('../../components/log/XTerminal', () => ({
  XTerminal: () => <div data-testid="x-terminal" />,
}));

vi.mock('../../components/pipeline/PipelineStepTree', () => ({
  PipelineStepTree: () => <div data-testid="pipeline-step-tree" />,
}));

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <TaskDetails />
    </QueryClientProvider>,
  );
}

describe('TaskDetails', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getPlan.mockRejectedValue(new Error('not found'));
  });

  it('shows a not-found state instead of staying on Loading when the Plan lookup fails', async () => {
    renderPage();

    await screen.findByText('未找到 Plan');
    expect(screen.queryByText('Loading...')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /查看运行报告/ }));
    expect(mocks.navigate).toHaveBeenCalledWith('/runs/3064/report');
  });
});
