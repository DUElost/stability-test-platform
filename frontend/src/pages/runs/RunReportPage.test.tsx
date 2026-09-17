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

async function renderWithRunStatus(status: string, overrides: Partial<RunReport> = {}) {
  vi.mocked(api.runs.getCachedReport).mockResolvedValue({
    ...reportWithRunStatus(status),
    ...overrides,
  } as unknown as RunReport);
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

// #2419：报告页的「汇总指标」面板已删——它的数据源（RUN_COMPLETE 快照的
// `log_summary`）自 ADR-0025 起没有生产者，生产 0/40 恒空。这里钉住「即便后端
// 塞了值也不渲染」，提醒恢复面板前先有生产者与用例。
describe('RunReportPage 汇总指标面板（#2419）', () => {
  it('does not render a summary-metrics panel even when the API sends values', async () => {
    await renderWithRunStatus('FINISHED', { summary_metrics: { restarts: 2 } });
    expect(screen.queryByText('汇总指标')).not.toBeInTheDocument();
    expect(screen.queryByText('restarts')).not.toBeInTheDocument();
  });
});

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

/**
 * #2420（第 1 项）：#1082 的 UI 半边——cached 报告是 Job 完成时刻的**快照**，
 * 后端早已在响应体给 `cached_at` 并要求「UI 标注截至 xx 时刻」，但前端一直 0 消费，
 * 于是「快照」与「最新重算」在界面上同形（dev 与生产实测都只显「生成时间」）。
 */
describe('RunReportPage 快照标注（#2420 / #1082）', () => {
  function reportWith(partial: Partial<RunReport>): RunReport {
    return {
      generated_at: '2026-09-16T12:00:00Z',
      run: { id: 3, status: 'FINISHED' },
      task: { id: 1, name: 'mtbf-suite', type: 'PLAN' },
      host: null,
      device: null,
      summary_metrics: {},
      risk_summary: null,
      alerts: [],
      ...partial,
    } as unknown as RunReport;
  }

  async function renderReport(report: RunReport) {
    vi.mocked(api.runs.getCachedReport).mockResolvedValue(report);
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
    return screen.findByText('生成时间');
  }

  it('响应带 cached_at 时标注「快照截至」并本地化，不再只显生成时间', async () => {
    await renderReport(reportWith({ cached_at: '2026-09-16T11:49:18.577637+00:00' }));
    const row = await screen.findByTestId('report-cached-at');
    expect(row).toHaveTextContent('快照截至');
    expect(row.textContent).toMatch(/\d{4}-\d{2}-\d{2} \d{2}:\d{2}/);
    expect(row.textContent).not.toContain('11:49:18.577637');
  });

  it('无 cached_at（实时重算）时整行不渲染，而不是给出一个空的「截至 —」', async () => {
    await renderReport(reportWith({ cached_at: null }));
    expect(screen.queryByTestId('report-cached-at')).toBeNull();
    expect(screen.getByText('生成时间')).toBeInTheDocument();
  });
});
