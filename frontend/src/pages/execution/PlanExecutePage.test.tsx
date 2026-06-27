import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import PlanExecutePage from './PlanExecutePage';
import { api } from '@/utils/api';

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

vi.mock('@/utils/api', () => ({
  api: {
    plans: {
      list: vi.fn(),
      previewRun: vi.fn(),
      run: vi.fn(),
    },
    devices: {
      list: vi.fn(),
    },
  },
}));

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
}: {
  plans?: any[];
  devices?: any[];
} = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  (api.plans.list as any).mockResolvedValue(plans);
  (api.devices.list as any).mockResolvedValue({
    items: devices,
    total: devices.length,
  });
  (api.plans.previewRun as any).mockResolvedValue({
    plan_name: 'Smoke Plan',
    device_count: 1,
    job_count: 1,
    total_steps: 1,
  });
  (api.plans.run as any).mockResolvedValue({ id: 88 });

  return render(
    <MemoryRouter>
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
});
