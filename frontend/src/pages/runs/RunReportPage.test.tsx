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

async function renderWithRunStatus(
  status: string,
  overrides: Partial<RunReport> = {},
  search = '',
) {
  vi.mocked(api.runs.getCachedReport).mockResolvedValue({
    ...reportWithRunStatus(status),
    ...overrides,
  } as unknown as RunReport);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      {/* #2420：权威形状是 /jobs/:jobId/report（旧 /runs/:runId/report 只重定向） */}
      <MemoryRouter initialEntries={[`/jobs/3/report${search}`]}>
        <Routes>
          <Route path="/jobs/:jobId/report" element={<RunReportPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  // 徽标行 = 「状态」标签所在的那一行，用它限定查询。
  // 注意前提已经变了（ADR-0045 §6 明确要求删掉旧注释的说法）：以前"风险徽标恒显未知"
  // 是缺陷（RISK 表没有 S/A/B 键），#2494 之后这里查不到未知才是正常的；本 fixture
  // 之所以仍可能出现「未知」，是因为 risk_summary 为 null → 按 D2 合法渲染第四态 UNKNOWN。
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
        {/* 同上：#2420 之后权威形状是 /jobs/:jobId/report */}
        <MemoryRouter initialEntries={['/jobs/3/report']}>
          <Routes>
            <Route path="/jobs/:jobId/report" element={<RunReportPage />} />
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


/**
 * #2420 第 2 项：从 PlanRun 详情进报告页时带 `?planRun=`，页面必须把它转成端点的
 * 归属校验参数——过去报告端点对「这个 job 属于哪个 run」毫不把关，错配 id 会返回
 * **另一个 run 的 job 报告**，而页面只写 `Job #N`，用户无从发现。
 */
describe('RunReportPage 归属参数透传（#2420）', () => {
  it('带 planRun 时按 { planRunId } 调用 cached 报告', async () => {
    await renderWithRunStatus('FINISHED', {}, '?planRun=7');
    await waitFor(() =>
      expect(api.runs.getCachedReport).toHaveBeenCalledWith(3, { planRunId: 7 }),
    );
  });

  it('不带 planRun 时不硬造参数（老深链与脚本行为不变）', async () => {
    vi.mocked(api.runs.getCachedReport).mockClear();
    await renderWithRunStatus('FINISHED');
    await waitFor(() => expect(api.runs.getCachedReport).toHaveBeenCalledWith(3, {
      planRunId: undefined,
    }));
  });
});

/**
 * #2494 判据 2：报告页风险徽标必须用**级别词表**（S/A/B/UNKNOWN）。
 *
 * 收敛前的形态是同一张卡上徽标说「未知」（`RISK` 表没有 S 键 → 落 FALLBACK）、
 * 旁边的「S/A/B 分布」说 S:1 —— 与 #2418 完全同型：数据没错，键没对齐。
 */
describe('RunReportPage 风险徽标词表（#2494）', () => {
  async function renderWithRisk(riskSummary: Record<string, unknown>) {
    vi.mocked(api.runs.getCachedReport).mockResolvedValue({
      generated_at: '2026-09-16T12:00:00Z',
      run: { id: 3, status: 'FINISHED' },
      task: { id: 1, name: 'mtbf-suite', type: 'PLAN' },
      host: null,
      device: null,
      summary_metrics: {},
      risk_summary: riskSummary,
      alerts: [],
    } as unknown as RunReport);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // 路径用 #2506 之后的权威形状（`/jobs/:jobId/report`），与本文件其余用例一致。
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/jobs/3/report']}>
          <Routes>
            <Route path="/jobs/:jobId/report" element={<RunReportPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    return screen.findByText('风险摘要');
  }

  it('risk_level=S 渲染「高」，不再与同屏 S/A/B 计数互相打脸', async () => {
    await renderWithRisk({
      risk_level: 'S',
      counts: { events_total: 1, aee_entries: 1, by_severity: { S: 1, A: 0, B: 0 } },
    });
    expect(await screen.findByText('高')).toBeInTheDocument();
    expect(screen.queryByText('未知')).toBeNull();
    // 「1/0/0」被三个 span 与夹在中间的斜杠拆开，getByText 只看直接文本子节点
    const distributionRow = (await screen.findByText('S/A/B 分布')).closest('div') as HTMLElement;
    expect(distributionRow.textContent).toBe('S/A/B 分布1/0/0');
  });

  it('词表漂移时回显原文而不是吞成「未知」（fallbackToRaw，#2418 同一判据）', async () => {
    await renderWithRisk({
      risk_level: 'SSS',
      counts: { events_total: 0, aee_entries: 0, by_severity: { S: 0, A: 0, B: 0 } },
    });
    expect(await screen.findByText('SSS')).toBeInTheDocument();
    expect(screen.queryByText('未知')).toBeNull();
  });
});

// #2707：同一张卡里混用 Plan 与 Job 两个实体——`report.task` 是遗留命名（`type`
// 字面量至今是 "PLAN"），而页面标题 / 面包屑 / 状态都是 Job。用户会把「任务ID」当作
// 被引用对象的标识（JIRA 建单场景直接抄错）。本组钉住：卡片标题与行名不再用「任务」，
// 且两个 id 各自绑对实体——fixture 里 run.id=3 / task.id=1 本就不同，混绑会立刻红。
describe('RunReportPage 实体归属（#2707）', () => {
  it('「计划信息」块分别显示 Plan ID 与 Job ID，且各自绑对实体', async () => {
    await renderWithRunStatus('FINISHED');

    const card = (await screen.findByText('计划信息')).closest('div') as HTMLElement;

    const planRow = within(card).getByText('Plan ID').closest('div') as HTMLElement;
    expect(planRow).toHaveTextContent('1'); // task.id（Plan）

    const jobRow = within(card).getByText('Job ID').closest('div') as HTMLElement;
    expect(jobRow).toHaveTextContent('3'); // run.id（JobInstance）

    // 旧文案不得回潮：混用两个实体正是本单要消掉的形态
    expect(screen.queryByText('任务信息')).toBeNull();
    expect(screen.queryByText('任务ID')).toBeNull();
  });
});
