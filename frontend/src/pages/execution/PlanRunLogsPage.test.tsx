import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import PlanRunLogsPage from './PlanRunLogsPage';
import { HeaderSlotProvider, useHeaderSlot } from '@/contexts/HeaderSlotContext';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getRun: vi.fn(),
  getEvents: vi.fn(),
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
      get: mocks.getRun,
      getEvents: mocks.getEvents,
    },
  },
}));

/** 模拟 AppShell 消费 HeaderSlotContext,把页面注入的顶栏内容渲染到 DOM。 */
function HeaderSlotOutlet() {
  const { headerSlot } = useHeaderSlot();
  return <>{headerSlot}</>;
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <HeaderSlotProvider>
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <HeaderSlotOutlet />
          <PlanRunLogsPage />
        </QueryClientProvider>
      </MemoryRouter>
    </HeaderSlotProvider>,
  );
}

function eventsPayload(overrides: Record<string, unknown> = {}) {
  return {
    plan_run_id: 12,
    total: 150,
    events: [
      {
        ts: '2026-05-08T12:30:00Z',
        stage: 'patrol',
        severity: 'err',
        category: 'step',
        title: 'monkey_check 步骤失败',
        description: 'DEV-3064 连续失败 3 次',
        device_serial: 'DEV-3064',
        job_id: 3064,
      },
    ],
    facets: {
      by_stage: { all: 150, patrol: 150, init: 0, teardown: 0, trigger: 0, system: 0 },
      by_severity: { all: 150, err: 150, warn: 0, info: 0, ok: 0 },
    },
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getRun.mockResolvedValue({ id: 12, plan_id: 7, status: 'RUNNING' });
  mocks.getEvents.mockResolvedValue(eventsPayload());
});

describe('PlanRunLogsPage', () => {
  it('renders the logs tab + paginated event stream', async () => {
    renderPage();
    expect(await screen.findByTestId('plan-run-event-stream')).toBeInTheDocument();
    expect(screen.getByTestId('plan-run-tabs')).toBeInTheDocument();
    expect(await screen.findByText('monkey_check 步骤失败')).toBeInTheDocument();
  });

  it('requests the first page with limit/offset, then offsets on next page', async () => {
    renderPage();
    await waitFor(() => expect(mocks.getEvents).toHaveBeenCalled());
    expect(mocks.getEvents).toHaveBeenCalledWith(
      12,
      expect.objectContaining({ limit: 50, offset: 0 }),
    );
    fireEvent.click(await screen.findByTestId('event-page-next'));
    await waitFor(() =>
      expect(mocks.getEvents).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ offset: 50 }),
      ),
    );
  });

  it('resets to the first page and re-queries when a stage filter changes', async () => {
    renderPage();
    await waitFor(() => expect(mocks.getEvents).toHaveBeenCalled());
    fireEvent.click(await screen.findByTestId('event-filter-stage-patrol'));
    await waitFor(() =>
      expect(mocks.getEvents).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ stage: 'patrol', offset: 0 }),
      ),
    );
  });
});
