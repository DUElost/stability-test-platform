import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { HeaderSlotProvider } from '@/contexts/HeaderSlotContext';
import PlanEditPage from './PlanEditPage';
import { api } from '@/utils/api';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
  };
});

vi.mock('@/components/pipeline/PlanChainPanel', () => ({
  default: () => <div data-testid="plan-chain-panel" />,
}));

vi.mock('@/components/pipeline/PlanCanvas', () => ({
  default: (props: {
    planName: string;
    onPlanNameChange: (name: string) => void;
  }) => (
    <div data-testid="plan-canvas">
      <input
        data-testid="plan-name-input"
        aria-label="Plan 名称"
        value={props.planName}
        onChange={(e) => props.onPlanNameChange(e.target.value)}
      />
    </div>
  ),
}));

vi.mock('@/components/pipeline/PlanStepInspector', () => ({
  default: () => <div data-testid="plan-step-inspector" />,
}));

vi.mock('@/utils/api', () => ({
  api: {
    plans: {
      get: vi.fn(),
      list: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
    },
    scripts: {
      list: vi.fn(),
    },
  },
}));

vi.mock('@/components/ui/toast', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  }),
}));

function renderPage(path: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });

  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={queryClient}>
        <HeaderSlotProvider>
          <Routes>
            <Route path="/orchestration/plans/:id" element={<PlanEditPage />} />
          </Routes>
        </HeaderSlotProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe('PlanEditPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.plans.list as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (api.scripts.list as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  });

  it('shows loading spinner while an existing plan is fetched', () => {
    let resolveGet: (value: unknown) => void = () => {};
    (api.plans.get as ReturnType<typeof vi.fn>).mockImplementation(
      () => new Promise((resolve) => { resolveGet = resolve; }),
    );

    renderPage('/orchestration/plans/42');

    expect(document.querySelector('.animate-spin')).toBeInTheDocument();

    resolveGet({
      id: 42,
      name: 'Loaded Plan',
      description: '',
      failure_threshold: 0.05,
      steps: [],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    });
  });

  it('renders new-plan workspace with saved badge and disabled save until dirty', async () => {
    renderPage('/orchestration/plans/new');

    expect(await screen.findByText('新建 Plan')).toBeInTheDocument();
    expect(screen.getByText('已保存')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /创建/ })).toBeDisabled();
    expect(screen.getByTestId('plan-chain-panel')).toBeInTheDocument();
    expect(screen.getByTestId('plan-canvas')).toBeInTheDocument();
    expect(screen.getByTestId('plan-step-inspector')).toBeInTheDocument();

    fireEvent.change(screen.getByTestId('plan-name-input'), { target: { value: 'My Plan' } });

    await waitFor(() => {
      expect(screen.getByText('未保存')).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /创建/ })).not.toBeDisabled();
  });

  it('opens lifecycle JSON dialog from toolbar action', async () => {
    renderPage('/orchestration/plans/new');

    await screen.findByText('新建 Plan');
    fireEvent.click(screen.getByRole('button', { name: /查看 JSON/ }));

    expect(await screen.findByText('Plan Lifecycle JSON')).toBeInTheDocument();
    const jsonPre = document.querySelector('pre');
    expect(jsonPre?.textContent).toContain('step_init_1');
    expect(jsonPre?.textContent).toContain('script:check_device');
  });

  it('hydrates existing plan name and shows save label', async () => {
    (api.plans.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 7,
      name: 'Nightly',
      description: 'overnight',
      failure_threshold: 0.05,
      patrol_interval_seconds: 60,
      timeout_seconds: null,
      next_plan_id: null,
      steps: [
        {
          id: 1,
          plan_id: 7,
          step_key: 'step_init_1',
          script_name: 'check_device',
          script_version: '1.0.0',
          stage: 'init',
          sort_order: 0,
          timeout_seconds: 30,
          retry: 0,
          enabled: true,
        },
      ],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    });

    renderPage('/orchestration/plans/7');

    expect(await screen.findByText('Nightly')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /保存修改/ })).toBeDisabled();
    expect(screen.getByText('已保存')).toBeInTheDocument();
  });

  it('navigates back to plan list from header back button', async () => {
    renderPage('/orchestration/plans/new');

    await screen.findByText('新建 Plan');
    const backBtn = screen.getAllByRole('button')[0];
    fireEvent.click(backBtn);

    expect(mocks.navigate).toHaveBeenCalledWith('/orchestration/plans');
  });

  it('prompts to save before execute when plan has unsaved edits', async () => {
    (api.plans.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 9,
      name: 'Dirty Plan',
      description: '',
      failure_threshold: 0.05,
      steps: [],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    });
    (api.plans.update as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 9,
      name: 'Dirty Plan v2',
      description: '',
      failure_threshold: 0.05,
      steps: [],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    });

    renderPage('/orchestration/plans/9');

    await screen.findByText('Dirty Plan');
    fireEvent.change(screen.getByTestId('plan-name-input'), { target: { value: 'Dirty Plan v2' } });
    fireEvent.click(screen.getByRole('button', { name: /发起测试/ }));

    expect(await screen.findByText('有未保存的修改')).toBeInTheDocument();
    expect(screen.getByText('是否先保存当前 Plan 再发起测试？')).toBeInTheDocument();
  });
});
