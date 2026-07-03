import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { JiraRunRecord } from '@/utils/api/types';

vi.mock('@/components/console/LiveConsole', () => ({
  default: ({ consoleRunId }: { consoleRunId: string }) => (
    <div data-testid="live-console-stub">{consoleRunId}</div>
  ),
}));

const listRuns = vi.fn();
vi.mock('@/utils/api/dedup', () => ({
  dedup: {
    listRuns: (...a: unknown[]) => listRuns(...a),
  },
}));

import JiraRunHistory from './JiraRunHistory';

function renderWithClient() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <JiraRunHistory />
    </QueryClientProvider>,
  );
}

const sampleRun: JiraRunRecord = {
  id: 1,
  console_run_id: 'con-abc',
  vendor: 'transsion',
  stage: 'create',
  dry_run: false,
  reporter: 'bob',
  input_source: 'Result.xls',
  status: 'SUCCESS',
  started_at: '2026-06-01T00:00:00Z',
  ended_at: '2026-06-01T00:01:00Z',
  exit_code: 0,
  issue_keys: ['ABC-1', 'ABC-2'],
  error: null,
  created_at: '2026-06-01T00:00:00Z',
};

describe('JiraRunHistory', () => {
  beforeEach(() => {
    listRuns.mockReset();
  });

  it('renders empty state when there are no runs', async () => {
    listRuns.mockResolvedValue([]);
    renderWithClient();
    expect(await screen.findByText('暂无提单记录')).toBeInTheDocument();
  });

  it('renders error state when the query fails', async () => {
    listRuns.mockRejectedValue(new Error('network error'));
    renderWithClient();
    expect(await screen.findByText(/历史记录加载失败/)).toBeInTheDocument();
  });

  it('renders a run row with vendor/status/issue count', async () => {
    listRuns.mockResolvedValue([sampleRun]);
    renderWithClient();

    const row = await screen.findByTestId('jira-run-row-con-abc');
    expect(row).toBeInTheDocument();
    expect(within(row).getByText('SUCCESS')).toBeInTheDocument();
    expect(within(row).getByText('已建 2 条 issue')).toBeInTheDocument();
    expect(row).toHaveTextContent('transsion');
  });

  it('expands a row to show LiveConsole and issue keys on click', async () => {
    listRuns.mockResolvedValue([sampleRun]);
    renderWithClient();

    const row = await screen.findByTestId('jira-run-row-con-abc');
    expect(screen.queryByTestId('live-console-stub')).not.toBeInTheDocument();

    fireEvent.click(row);

    expect(await screen.findByTestId('live-console-stub')).toHaveTextContent('con-abc');
    expect(screen.getByText('ABC-1')).toBeInTheDocument();
    expect(screen.getByText('ABC-2')).toBeInTheDocument();
  });

  it('refetches with vendor/status filters when changed', async () => {
    listRuns.mockResolvedValue([]);
    renderWithClient();
    await screen.findByText('暂无提单记录');

    fireEvent.change(screen.getByLabelText('按厂商过滤'), { target: { value: 'tinno' } });

    await waitFor(() =>
      expect(listRuns).toHaveBeenLastCalledWith({ vendor: 'tinno', status: undefined, limit: 50 }),
    );
  });
});
