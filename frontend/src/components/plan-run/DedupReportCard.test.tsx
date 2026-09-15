import type { ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import DedupReportCard from './DedupReportCard';
import { api } from '@/utils/api';

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

describe('DedupReportCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
  });

  it('shows host completeness, no-ack and scan_failed warning', async () => {
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

    expect(await screen.findByTestId('dedup-host-completeness')).toBeTruthy();
    expect(screen.getByText(/host 完成度 0\/3/)).toBeTruthy();
    expect(screen.getByText('扫描未产生任何报表')).toBeTruthy();
    expect(screen.getByText(/未回执 1 台/)).toBeTruthy();
  });

  it('hides completeness when archive is absent', async () => {
    (api.planRuns.getDedupStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      plan_run_id: 1,
      artifacts: [],
    });

    render(<DedupReportCard runId={1} />, { wrapper });

    expect(
      await screen.findByText('暂无去重产物。归档完成后点击「扫描」开始。'),
    ).toBeTruthy();
    expect(screen.queryByTestId('dedup-host-completeness')).toBeNull();
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
