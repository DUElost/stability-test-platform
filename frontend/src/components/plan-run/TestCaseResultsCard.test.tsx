/**
 * #1194 — TestCaseResultsCard：终态逐条用例结果（分页/计数/加载更多）。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TestCaseResultsCard from './TestCaseResultsCard';

const mocks = vi.hoisted(() => ({
  getTestCaseResults: vi.fn(),
}));

vi.mock('@/utils/api', () => ({
  api: {
    planRuns: {
      getTestCaseResults: mocks.getTestCaseResults,
    },
  },
}));

function renderCard(runId: number, isTerminal: boolean) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <TestCaseResultsCard runId={runId} isTerminal={isTerminal} />
    </QueryClientProvider>,
  );
}

const row = (i: number) => ({
  id: i,
  plan_run_id: 5,
  job_id: 100 + i,
  suite_id: null,
  case_id: null,
  case_name: `case_${i}`,
  status: 'FAILURE',
  detail: null,
  artifact_uri: null,
  run_dir: null,
  created_at: '2026-07-25T17:57:07+08:00',
  device_id: 1,
});

const summary = (total: number) => ({ total, passed: 0, failed: total, error: 0 });

beforeEach(() => {
  vi.clearAllMocks();
});

describe('TestCaseResultsCard (#1194)', () => {
  it('RUNNING 时不触发请求', () => {
    renderCard(5, false);
    expect(mocks.getTestCaseResults).not.toHaveBeenCalled();
    expect(screen.getByText('PlanRun 结束后展示逐条用例结果。')).toBeInTheDocument();
  });

  it('超过单页时显示计数与「加载更多」，点击后放大窗口（第 501 条可见）', async () => {
    mocks.getTestCaseResults
      .mockResolvedValueOnce({
        items: Array.from({ length: 500 }, (_, i) => row(i + 1)),
        total: 501,
        summary: summary(501),
      })
      .mockResolvedValueOnce({
        items: Array.from({ length: 501 }, (_, i) => row(i + 1)),
        total: 501,
        summary: summary(501),
      });

    renderCard(5, true);

    expect(await screen.findByTestId('test-case-results-count')).toHaveTextContent('已显示 500 / 501');
    // 第 501 条此前超出单页、不可见
    expect(screen.queryByTestId('tcr-row-501')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /加载更多/ }));

    await waitFor(() =>
      expect(mocks.getTestCaseResults).toHaveBeenLastCalledWith(5, { limit: 1000 }),
    );
    await waitFor(() => expect(screen.getByTestId('tcr-row-501')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /加载更多/ })).not.toBeInTheDocument();
  });

  it('空结果时显示空态', async () => {
    mocks.getTestCaseResults.mockResolvedValue({ items: [], total: 0, summary: summary(0) });
    renderCard(5, true);
    await waitFor(() => expect(screen.getByText(/暂无逐条用例结果/)).toBeInTheDocument());
  });
});
