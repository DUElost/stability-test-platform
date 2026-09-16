import type { ReactNode } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import DedupReportCard from './DedupReportCard';
import { api } from '@/utils/api';
import type { RunContextExtractSummary } from '@/utils/api/types';

vi.mock('@/utils/api', () => ({
  api: {
    planRuns: {
      getDedupStatus: vi.fn(),
      triggerScan: vi.fn(),
      triggerMerge: vi.fn(),
      triggerExtract: vi.fn(),
    },
  },
}));

vi.mock('@/hooks/useToast', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    promise: vi.fn(),
    action: vi.fn(),
  }),
}));

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: 0 } },
});
const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
);

/**
 * #2186 的两个新键尚未登记进 `types.ts`（该目录在窗被其他 Execution 声明），
 * 组件侧是就地收窄读取，故测试用断言构造（并保留"未登记"这一事实的可见性）。
 */
const extractSummaryWith = (over: Record<string, unknown>) =>
  ({
    targets: 3,
    copied: 1,
    missing: 2,
    existing: 0,
    merge_xls_copied: 1,
    archived: 0,
    ...over,
  }) as RunContextExtractSummary;

describe('DedupReportCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
  });

  it('#2185: 扫描阶段含完成度、未回执与零报表位（失败态走 destructive）', async () => {
    (api.planRuns.getDedupStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      plan_run_id: 1,
      artifacts: [],
      archive: {
        hosts_triggered: 3,
        hosts_with_artifacts: 0,
        scan_artifacts_registered: 0,
        hosts_not_acked: 1,
      },
      scan_failed: true,
    });

    render(<DedupReportCard runId={1} />, { wrapper });

    const scan = await screen.findByTestId('pipeline-scan');
    expect(scan.textContent).toContain('host 完成度 0/3');
    expect(scan.textContent).toContain('未回执 1 台');
    expect(scan.textContent).toContain('扫描未产生任何报表');
    expect(scan.querySelector('.text-destructive')).not.toBeNull();
  });

  it('#2185: 状态缺失时明说缺什么，不再静默隐藏', async () => {
    (api.planRuns.getDedupStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      plan_run_id: 1,
      artifacts: [],
    });

    render(<DedupReportCard runId={1} />, { wrapper });

    // 此前 archive 缺失＝整块完成度静默消失；现在明说"未开始"而不是显示成 0/0
    expect(await screen.findByTestId('archive-pipeline')).toBeTruthy();
    expect(screen.getByTestId('pipeline-scan').textContent).toContain('未开始（无本轮 host 计数）');
    expect(screen.getByTestId('pipeline-upload').textContent).toContain('upload_summary 缺失');
    expect(screen.getByTestId('pipeline-extract').textContent).toContain('extract 缺失');
    // 产物空态 CTA 仍在（与"状态缺失"是两件事）
    expect(screen.getByText('暂无去重产物。归档完成后点击「扫描」开始。')).toBeTruthy();
  });

  it('#2185: 勾选「最终轮」后触发扫描带 is_final=true', async () => {
    (api.planRuns.getDedupStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      plan_run_id: 1,
      artifacts: [],
    });
    (api.planRuns.triggerScan as ReturnType<typeof vi.fn>).mockResolvedValue({
      plan_run_id: 1,
      triggered_hosts: [],
      skipped_offline: [],
    });

    render(<DedupReportCard runId={1} />, { wrapper });
    const scanBtn = await screen.findByTestId('dedup-scan-btn');

    fireEvent.click(scanBtn);
    await waitFor(() => expect(api.planRuns.triggerScan).toHaveBeenCalledWith(1, false));

    fireEvent.click(screen.getByTestId('dedup-scan-final'));
    fireEvent.click(scanBtn);
    await waitFor(() => expect(api.planRuns.triggerScan).toHaveBeenLastCalledWith(1, true));
  });

  it('#2185: 合并阶段按产物判定、提取阶段露出缺失', async () => {
    (api.planRuns.getDedupStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      plan_run_id: 1,
      artifacts: [
        {
          id: 1,
          host_id: 'h1',
          storage_uri: '/nfs/dedup/1/h1_Result_org.xls',
          artifact_type: 'scan_result_xls',
          size_bytes: 10,
          created_at: null,
        },
      ],
    });

    render(
      <DedupReportCard
        runId={1}
        extractSummary={{
          targets: 5,
          copied: 4,
          missing: 1,
          existing: 0,
          merge_xls_copied: 1,
          archived: 0,
        }}
      />,
      { wrapper },
    );

    const merge = await screen.findByTestId('pipeline-merge');
    expect(merge.textContent).toContain('未合并（本轮 scan 产物 1 份）');
    const extract = screen.getByTestId('pipeline-extract');
    expect(extract.textContent).toContain('已拷贝 4/5');
    expect(extract.textContent).toContain('缺失 1');
    expect(extract.querySelector('.text-destructive')).not.toBeNull();
  });

  it('I-7: 未等齐时露出 incomplete_reason（此前只说「未等齐」）', async () => {
    (api.planRuns.getDedupStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      plan_run_id: 1,
      artifacts: [],
    });

    render(
      <DedupReportCard
        runId={1}
        uploadSummary={{
          total: 10,
          detected: 10,
          pull_failed: 0,
          local: 0,
          upload_pending: 0,
          pending: 2,
          uploading: 0,
          upload_failed: 0,
          failed: 0,
          remote: 8,
          archived: 0,
          pruned: 0,
          ready: false,
          mark_ready: false,
          events_ready: true,
          incomplete_reason: 'upload_mark_timeout',
          compensation: 'best_effort_extract',
        }}
      />,
      { wrapper },
    );

    const el = await screen.findByTestId('upload-not-ready');
    expect(el.textContent).toContain('未等齐');
    expect(el.textContent).toContain('上送标记未确认');
    expect(el.getAttribute('title')).toBe('upload_mark_timeout');
  });

  it('I-7: 未知原因代码原样露出（不静默吞掉后端新增原因）', async () => {
    (api.planRuns.getDedupStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      plan_run_id: 1,
      artifacts: [],
    });

    render(
      <DedupReportCard
        runId={1}
        uploadSummary={{
          total: 1,
          detected: 1,
          pull_failed: 0,
          local: 0,
          upload_pending: 0,
          pending: 1,
          uploading: 0,
          upload_failed: 0,
          failed: 0,
          remote: 0,
          archived: 0,
          pruned: 0,
          ready: false,
          incomplete_reason: 'brand_new_reason',
        }}
      />,
      { wrapper },
    );

    const el = await screen.findByTestId('upload-not-ready');
    expect(el.textContent).toContain('brand_new_reason');
  });

  it('#2186: 缺失清单可下钻（此前只有「缺失 N」）', async () => {
    (api.planRuns.getDedupStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      plan_run_id: 1,
      artifacts: [],
    });

    render(
      <DedupReportCard
        runId={1}
        extractSummary={extractSummaryWith({
          missing: 2,
          missing_total: 2,
          missing_items: ['/nfs/devices/1/2026_0908_a.NE', '/nfs/devices/1/2026_0908_b.JE'],
        })}
      />,
      { wrapper },
    );

    const block = await screen.findByTestId('extract-missing-items');
    expect(block.textContent).toContain('提取缺失 2 项');
    expect(block.textContent).toContain('/nfs/devices/1/2026_0908_a.NE');
    expect(block.textContent).toContain('/nfs/devices/1/2026_0908_b.JE');
    expect(screen.queryByTestId('extract-missing-more')).toBeNull();
  });

  it('#2186: 清单被截断时明说还有多少条', async () => {
    (api.planRuns.getDedupStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      plan_run_id: 1,
      artifacts: [],
    });

    render(
      <DedupReportCard
        runId={1}
        extractSummary={extractSummaryWith({
          missing: 26,
          missing_total: 26,
          missing_items: ['/nfs/devices/1/m00', '/nfs/devices/1/m01'],
        })}
      />,
      { wrapper },
    );

    expect(await screen.findByTestId('extract-missing-more')).toHaveTextContent(
      '还有 24 条未列出（仅列前 2 条）',
    );
  });

  it('#2186: 有缺口但无清单（旧数据）不得读成「没有缺口」', async () => {
    (api.planRuns.getDedupStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      plan_run_id: 1,
      artifacts: [],
    });

    render(
      <DedupReportCard runId={1} extractSummary={extractSummaryWith({ missing: 3 })} />,
      { wrapper },
    );

    const block = await screen.findByTestId('extract-missing-items');
    expect(block.textContent).toContain('提取缺失 3 项');
    expect(block.textContent).toContain('该次运行未记录清单');
  });

  it('#2185: 逐平台 merge 结果可见，且 no_input 不显示为失败', async () => {
    (api.planRuns.getDedupStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      plan_run_id: 1,
      artifacts: [
        {
          id: 1,
          host_id: 'h1',
          storage_uri: '/nfs/dedup/1/h1_Result_org.xls',
          artifact_type: 'scan_result_xls',
          size_bytes: 10,
          created_at: null,
        },
      ],
    });

    render(
      <DedupReportCard
        runId={1}
        mergePlatforms={{
          platforms: { mtk: 'ok', unisoc: 'no_input' },
          recorded_at: '2026-09-16T00:00:00Z',
        }}
      />,
      { wrapper },
    );

    const merge = await screen.findByTestId('pipeline-merge');
    expect(merge.textContent).toContain('mtk=ok');
    expect(merge.textContent).toContain('unisoc=no_input');

    // no_input = 该平台本轮没有输入（不是失败）→ muted，且悬停说明写清"不是失败"
    const uni = screen.getByTestId('merge-platform-unisoc');
    expect(uni.className).toContain('text-muted-foreground');
    expect(uni.className).not.toContain('destructive');
    expect(uni.getAttribute('title')).toContain('不是失败');
    // ok → success
    expect(screen.getByTestId('merge-platform-mtk').className).toContain('text-success');
  });

  it('#2185: 平台被 skip 时「合并」整行降为 warn（不被「有产物」盖住）', async () => {
    (api.planRuns.getDedupStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      plan_run_id: 1,
      artifacts: [
        {
          id: 1,
          host_id: null,
          storage_uri: '/nfs/dedup/1/merge/mtk/Result_MergeFiles.xls',
          artifact_type: 'merge_result_xls',
          size_bytes: 20,
          created_at: null,
        },
      ],
    });

    render(
      <DedupReportCard runId={1} mergePlatforms={{ platforms: { mtk: 'skipped_failed' } }} />,
      { wrapper },
    );

    const skipped = await screen.findByTestId('merge-platform-mtk');
    expect(skipped.className).toContain('text-warning');
    expect(screen.getByTestId('pipeline-merge').querySelector('.bg-warning')).not.toBeNull();
  });

  it('#2185: 未知平台结果码原样露出（不静默吞掉后端新加的结果类型）', async () => {
    (api.planRuns.getDedupStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      plan_run_id: 1,
      artifacts: [],
    });

    render(
      <DedupReportCard runId={1} mergePlatforms={{ platforms: { qcom: 'brand_new_outcome' } }} />,
      { wrapper },
    );

    expect(await screen.findByTestId('merge-platform-qcom')).toHaveTextContent(
      'qcom=brand_new_outcome',
    );
  });

  it('#1195: query failure shows error state, not the scan-empty CTA', async () => {
    (api.planRuns.getDedupStatus as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('boom'),
    );

    render(<DedupReportCard runId={1} />, { wrapper });

    expect(await screen.findByText(/去重状态加载失败/)).toBeTruthy();
    expect(screen.queryByText(/暂无去重产物/)).toBeNull();
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy();
  });
});
