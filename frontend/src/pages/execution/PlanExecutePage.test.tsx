import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import PlanExecutePage from './PlanExecutePage';
import { api, ApiError } from '@/utils/api';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
  };
});

vi.mock('@/components/ui/toast', () => ({
  useToast: () => mocks.toast,
}));

vi.mock('@/utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return {
    ...actual,
    api: {
      plans: {
        list: vi.fn(),
        previewRun: vi.fn(),
        run: vi.fn(),
      },
      planRuns: {
        retryDispatch: vi.fn(),
      },
      devices: {
        list: vi.fn(),
      },
    },
  };
});

function renderPage({
  plans = [
    {
      id: 7,
      name: 'Smoke Plan',
      description: null,
      steps: [{ step_key: 'check_device' }],
      failure_threshold: 0.05,
    },
  ],
  devices = [],
  plansFailure,
  devicesFailure,
}: {
  plans?: any[];
  devices?: any[];
  plansFailure?: Error;
  devicesFailure?: Error;
} = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  if (plansFailure) (api.plans.list as any).mockRejectedValue(plansFailure);
  else (api.plans.list as any).mockResolvedValue(plans);
  if (devicesFailure) (api.devices.list as any).mockRejectedValue(devicesFailure);
  else {
    (api.devices.list as any).mockResolvedValue({
      items: devices,
      total: devices.length,
    });
  }
  (api.plans.previewRun as any).mockResolvedValue({
    plan_name: 'Smoke Plan',
    device_count: 1,
    job_count: 1,
    total_steps: 1,
  });
  (api.plans.run as any).mockResolvedValue({ id: 88 });
  (api.planRuns.retryDispatch as any).mockResolvedValue({ plan_run_id: 88, status: 'RUNNING' });

  return render(
    <MemoryRouter initialEntries={['/execution/plan-execute?plan=7']}>
      <QueryClientProvider client={queryClient}>
        <PlanExecutePage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe('PlanExecutePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('disables BUSY devices and excludes them from available count', async () => {
    renderPage({
      devices: [
        {
          id: 2429,
          serial: '457854125444LMKJ',
          model: 'Infinix_X6851',
          host_id: 'auto-fdaf1d55e319',
          status: 'BUSY',
        },
        {
          id: 2430,
          serial: 'FREE123',
          model: 'Infinix_X6851',
          host_id: 'auto-fdaf1d55e319',
          status: 'ONLINE',
        },
      ],
    });

    const busyCheckbox = await screen.findByLabelText(/457854125444LMKJ/i);
    const freeCheckbox = screen.getByLabelText(/FREE123/i);

    expect(busyCheckbox).toBeDisabled();
    expect(screen.getByText(/已选 0 \/ 1 台可用/)).toBeInTheDocument();

    fireEvent.click(busyCheckbox);
    expect(screen.getByText(/已选 0 \/ 1 台可用/)).toBeInTheDocument();

    fireEvent.click(freeCheckbox);
    expect(screen.getByText(/已选 1 \/ 1 台可用/)).toBeInTheDocument();
  });

  it('prefers backend schedulable over raw device status', async () => {
    renderPage({
      devices: [
        { id: 1, serial: 'ONLINE-BLOCKED', host_id: 'h1', status: 'ONLINE', schedulable: false },
        { id: 2, serial: 'BUSY-ADMITTED', host_id: 'h1', status: 'BUSY', schedulable: true },
      ],
    });

    expect(await screen.findByLabelText(/ONLINE-BLOCKED/)).toBeDisabled();
    expect(screen.getByLabelText(/BUSY-ADMITTED/)).not.toBeDisabled();
    expect(screen.getByText(/已选 0 \/ 1 台可用/)).toBeInTheDocument();
  });

  it('renders Plan query failure instead of an empty selector', async () => {
    renderPage({ plansFailure: new Error('plans unavailable') });

    expect(await screen.findByText('加载 Plan 失败')).toBeInTheDocument();
    expect(screen.getByText('plans unavailable')).toBeInTheDocument();
  });

  it('rejects zero-step plans before preview', async () => {
    renderPage({
      plans: [{
        id: 7,
        name: 'Empty Plan',
        description: null,
        steps: [],
        failure_threshold: 0.05,
      }],
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });

    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    expect(screen.getByRole('button', { name: /预览并发起/ })).toBeDisabled();
    expect(screen.getByText(/没有已启用步骤，无法执行/)).toBeInTheDocument();
    expect(api.plans.previewRun).not.toHaveBeenCalled();
  });

  it('freezes preview device IDs for the confirmed run', async () => {
    renderPage({
      devices: [
        { id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'DEV-2', host_id: 'h1', status: 'ONLINE' },
      ],
    });

    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    fireEvent.click(screen.getByRole('button', { name: /预览并发起/ }));
    await screen.findByText('确认执行');

    fireEvent.click(screen.getByLabelText(/DEV-2/));
    fireEvent.click(screen.getByRole('button', { name: /确认发起/ }));

    await waitFor(() => {
      expect(api.plans.run).toHaveBeenCalledWith(7, { device_ids: [1] });
    });
  });

  it('offers detail navigation and retry for a 503 with plan_run_id', async () => {
    const error = new ApiError('DISPATCH_QUEUE_UNAVAILABLE', 'SAQ unavailable', {
      status: 503,
      details: {
        code: 'DISPATCH_QUEUE_UNAVAILABLE',
        message: 'SAQ unavailable',
        retryable: true,
        plan_run_id: 91,
      },
    });
    (api.plans.run as any).mockRejectedValueOnce(error);
    (api.planRuns.retryDispatch as any).mockResolvedValueOnce({
      plan_run_id: 91,
      status: 'RUNNING',
    });
    renderPage({
      devices: [{ id: 1, serial: 'DEV-1', host_id: 'h1', status: 'ONLINE' }],
    });

    fireEvent.click(await screen.findByLabelText(/DEV-1/));
    fireEvent.click(screen.getByRole('button', { name: /预览并发起/ }));
    fireEvent.click(await screen.findByRole('button', { name: /确认发起/ }));

    expect(await screen.findByText('PlanRun #91 派发失败')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /重试派发/ }));

    await waitFor(() => expect(api.planRuns.retryDispatch).toHaveBeenCalledWith(91));
    expect(mocks.navigate).toHaveBeenCalledWith('/execution/plan-runs/91');
  });
});
