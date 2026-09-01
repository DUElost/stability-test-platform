import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import PlanRunListPage from './PlanRunListPage';

const listPageMock = vi.fn();

vi.mock('@/utils/api', async () => {
  const actual = await vi.importActual<typeof import('@/utils/api')>('@/utils/api');
  return {
    ...actual,
    api: {
      ...actual.api,
      planRuns: {
        ...actual.api.planRuns,
        listPage: (...args: unknown[]) => listPageMock(...args),
      },
    },
  };
});

vi.mock('@/hooks/useDebouncedValue', () => ({
  useDebouncedValue: <T,>(value: T) => value,
}));

vi.mock('@/components/project/ProjectFilterSelect', () => ({
  ProjectFilterSelect: () => <div data-testid="plan-run-project-filter" />,
  ProjectKeyBadge: ({ projectKey }: { projectKey?: string | null }) =>
    projectKey ? <span>{projectKey}</span> : null,
}));

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <PlanRunListPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

const sampleRuns = [
  {
    id: 101,
    plan_id: 1,
    plan_name: 'MTBF overnight',
    status: 'RUNNING',
    failure_threshold: 0,
    run_type: 'MANUAL',
    triggered_by: 'alice',
    started_at: '2026-09-01T01:00:00Z',
    ended_at: null,
    project_key: 'proj-a',
    device_count: 12,
    result_summary: null,
  },
  {
    id: 102,
    plan_id: 2,
    plan_name: 'Smoke suite',
    status: 'FAILED',
    failure_threshold: 0,
    run_type: 'SCHEDULE',
    triggered_by: 'scheduler',
    started_at: '2026-08-31T12:00:00Z',
    ended_at: '2026-08-31T13:00:00Z',
    project_key: 'proj-a',
    device_count: 4,
    result_summary: { pass_rate: 0.25, total: 4, failed: 3 },
  },
  {
    id: 103,
    plan_id: 3,
    plan_name: 'Idle check',
    status: 'SUCCESS',
    failure_threshold: 0,
    run_type: 'MANUAL',
    triggered_by: 'bob',
    started_at: '2026-08-30T08:00:00Z',
    ended_at: '2026-08-30T08:10:00Z',
    project_key: null,
    device_count: 1,
    result_summary: { pass_rate: 1, total: 1, completed: 1 },
  },
];

function pageOf(items = sampleRuns, overrides: Record<string, unknown> = {}) {
  return {
    items,
    total: items.length,
    skip: 0,
    limit: 50,
    stats: { total: items.length, running: 1, failed: 1 },
    ...overrides,
  };
}

describe('PlanRunListPage', () => {
  beforeEach(() => {
    listPageMock.mockReset();
    listPageMock.mockResolvedValue(pageOf());
  });

  it('renders table rows instead of card stack', async () => {
    renderPage();
    expect(await screen.findByText('MTBF overnight')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Plan' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '设备' })).toBeInTheDocument();
    expect(screen.getByText('#101')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('25%')).toBeInTheDocument();
  });

  it('filters by status tab via API', async () => {
    const user = userEvent.setup();
    listPageMock
      .mockResolvedValueOnce(pageOf())
      .mockResolvedValue(pageOf([sampleRuns[1]]));
    renderPage();
    await screen.findByText('MTBF overnight');

    await user.click(screen.getByTestId('plan-run-status-FAILED'));
    await waitFor(() => {
      expect(listPageMock).toHaveBeenCalledWith(
        expect.objectContaining({ status: 'FAILED', skip: 0 }),
      );
    });
  });

  it('passes search query to API', async () => {
    renderPage();
    await screen.findByText('MTBF overnight');

    fireEvent.change(screen.getByTestId('plan-run-search'), { target: { value: 'smoke' } });
    await waitFor(() => {
      expect(listPageMock).toHaveBeenCalledWith(
        expect.objectContaining({ q: 'smoke' }),
      );
    });
  });

  it('KPI click switches status filter', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('MTBF overnight');

    await user.click(screen.getByLabelText('筛选失败'));
    await waitFor(() => {
      expect(listPageMock).toHaveBeenCalledWith(
        expect.objectContaining({ status: 'FAILED' }),
      );
    });
  });

  it('paginates with skip/limit', async () => {
    const user = userEvent.setup();
    listPageMock.mockResolvedValue(
      pageOf(sampleRuns, { total: 120, skip: 0, limit: 50 }),
    );
    renderPage();
    await screen.findByText('MTBF overnight');
    expect(screen.getByText('共 120 条')).toBeInTheDocument();

    await user.click(screen.getByLabelText('下一页'));
    await waitFor(() => {
      expect(listPageMock).toHaveBeenCalledWith(
        expect.objectContaining({ skip: 50, limit: 50 }),
      );
    });
  });
});
