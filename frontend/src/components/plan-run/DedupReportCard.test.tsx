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
});
