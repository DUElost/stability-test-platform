import { render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import RunReportPage from './RunReportPage';
import { api } from '@/utils/api';
import type { RunReport } from '@/utils/api/types';

vi.mock('@/utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      runs: {
        ...actual.api.runs,
        getCachedReport: vi.fn(),
        getCachedJiraDraft: vi.fn().mockResolvedValue(null),
      },
    },
  };
});

/**
 * #2418：报告页「任务信息 → 状态」把后端**对外**词表喂给了库内词表那张徽标表
 * （`kind="job"`），FINISHED / CANCELED 缺键 → 一律渲染「未知」（生产同样命中）。
 *
 * 这里的判据是页面本身：改回 `kind="job"` 会立刻红，光看 status-badge 单测拦不住。
 */
function reportWithRunStatus(status: string): RunReport {
  return {
    generated_at: '2026-09-16T12:00:00Z',
    run: { id: 3, status },
    task: { id: 1, name: 'mtbf-suite', type: 'PLAN' },
    host: null,
    device: null,
    summary_metrics: {},
    risk_summary: null,
    alerts: [],
  } as unknown as RunReport;
}

async function renderWithRunStatus(status: string) {
  vi.mocked(api.runs.getCachedReport).mockResolvedValue(reportWithRunStatus(status));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/runs/3/report']}>
        <Routes>
          <Route path="/runs/:runId/report" element={<RunReportPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  // 徽标行 = 「状态」标签所在的那一行；用它限定查询，避免把风险徽标的「未知」算进来
  const label = await screen.findByText('状态');
  return within(label.closest('div') as HTMLElement);
}

describe('RunReportPage 状态徽标（#2418）', () => {
  it('COMPLETED 经后端映射为 FINISHED 后显示「完成」，不是「未知」', async () => {
    const row = await renderWithRunStatus('FINISHED');
    await waitFor(() => expect(row.getByText('完成')).toBeInTheDocument());
    expect(row.queryByText('未知')).toBeNull();
  });

  it('CANCELED 显示「已中止」——主动取消不能与真未知不可区分', async () => {
    const row = await renderWithRunStatus('CANCELED');
    await waitFor(() => expect(row.getByText('已中止')).toBeInTheDocument());
    expect(row.queryByText('未知')).toBeNull();
  });

  it('QUEUED 显示「排队中」', async () => {
    const row = await renderWithRunStatus('QUEUED');
    await waitFor(() => expect(row.getByText('排队中')).toBeInTheDocument());
  });

  it('未识别状态回显原文（fallbackToRaw），不留「未知」黑洞', async () => {
    const row = await renderWithRunStatus('SOMETHING_NEW');
    await waitFor(() => expect(row.getByText('SOMETHING_NEW')).toBeInTheDocument());
    expect(row.queryByText('未知')).toBeNull();
  });
});
