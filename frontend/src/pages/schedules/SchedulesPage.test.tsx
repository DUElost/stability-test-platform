import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const mocks = vi.hoisted(() => ({
  schedulesList: vi.fn(),
  plansList: vi.fn(),
  schedulesCreate: vi.fn(),
  schedulesUpdate: vi.fn(),
  schedulesToggle: vi.fn(),
  schedulesRunNow: vi.fn(),
  schedulesDelete: vi.fn(),
  confirm: vi.fn().mockResolvedValue(false),
}));

vi.mock('@/utils/api', () => ({
  api: {
    schedules: {
      list: (...a: unknown[]) => mocks.schedulesList(...a),
      create: (...a: unknown[]) => mocks.schedulesCreate(...a),
      update: (...a: unknown[]) => mocks.schedulesUpdate(...a),
      toggle: (...a: unknown[]) => mocks.schedulesToggle(...a),
      runNow: (...a: unknown[]) => mocks.schedulesRunNow(...a),
      delete: (...a: unknown[]) => mocks.schedulesDelete(...a),
    },
    plans: {
      list: (...a: unknown[]) => mocks.plansList(...a),
    },
  },
  toApiError: (e: unknown) => ({ message: String(e) }),
}));

vi.mock('@/hooks/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));

vi.mock('@/hooks/useConfirm', () => ({
  useConfirm: () => mocks.confirm,
}));

import SchedulesPage from './SchedulesPage';

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SchedulesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('SchedulesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.plansList.mockResolvedValue([]);
    mocks.schedulesList.mockResolvedValue({ items: [], total: 0 });
  });

  it('renders cron values from the cron_expr wire field', async () => {
    mocks.schedulesList.mockResolvedValue({
      items: [{
        id: 1,
        name: '夜跑',
        cron_expr: '0 2 * * *',
        plan_id: 7,
        device_ids: [1, 2],
        enabled: true,
        created_at: '2026-08-14T00:00:00Z',
      }],
      total: 1,
    });

    renderPage();

    expect(await screen.findByText('夜跑')).toBeInTheDocument();
    expect(screen.getByText('0 2 * * *')).toBeInTheDocument();
  });

  it('prefills the edit form from cron_expr without crashing', async () => {
    mocks.schedulesList.mockResolvedValue({
      items: [{
        id: 2,
        name: '早跑',
        cron_expr: '0 6 * * *',
        plan_id: 8,
        device_ids: [3],
        enabled: true,
        created_at: '2026-08-14T00:00:00Z',
      }],
      total: 1,
    });

    renderPage();

    const edit = await screen.findByRole('button', { name: '编辑' });
    fireEvent.click(edit);

    await waitFor(() => {
      expect(screen.getByDisplayValue('0 6 * * *')).toBeInTheDocument();
    });
  });
});
