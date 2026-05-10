import { fireEvent, render, screen, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import HostHotUpdateConfirmDialog from './HostHotUpdateConfirmDialog';

const mocks = vi.hoisted(() => ({
  getDetail: vi.fn(),
}));

vi.mock('@/utils/api', () => ({
  api: {
    hosts: {
      getDetail: mocks.getDetail,
    },
  },
}));

function renderDialog(props: {
  hostId: number | string | null;
  onClose?: () => void;
  onConfirm?: (hostId: number | string, opts: { abortRunningJobs: boolean }) => void;
  isHotUpdatePending?: boolean;
  retryAfterSeconds?: number;
}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <HostHotUpdateConfirmDialog
        hostId={props.hostId}
        onClose={props.onClose ?? vi.fn()}
        onConfirm={props.onConfirm ?? vi.fn()}
        isHotUpdatePending={props.isHotUpdatePending}
        retryAfterSeconds={props.retryAfterSeconds}
      />
    </QueryClientProvider>,
  );
}

describe('HostHotUpdateConfirmDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing when hostId is null', () => {
    renderDialog({ hostId: null });
    expect(screen.queryByTestId('host-hot-update-dialog')).not.toBeInTheDocument();
  });

  it('opens with green "no active jobs" banner and confirms direct hot-update', async () => {
    mocks.getDetail.mockResolvedValueOnce({
      id: 'host-101',
      active_job_count: 0,
      active_jobs: [],
    });
    const onConfirm = vi.fn();
    renderDialog({ hostId: 'host-101', onConfirm });

    expect(await screen.findByTestId('host-no-active-jobs')).toBeInTheDocument();

    const confirm = screen.getByTestId('host-hot-update-confirm');
    expect(confirm).not.toBeDisabled();
    expect(confirm).toHaveTextContent('执行热更新');

    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledWith('host-101', { abortRunningJobs: false });
  });

  it('blocks confirm until the abort toggle is checked when active_jobs > 0', async () => {
    mocks.getDetail.mockResolvedValueOnce({
      id: 'host-202',
      active_job_count: 2,
      active_jobs: [
        {
          id: 3001,
          plan_run_id: 12,
          plan_id: 7,
          device_id: 5,
          status: 'RUNNING',
          started_at: '2026-05-08T12:00:00Z',
        },
        {
          id: 3002,
          plan_run_id: 12,
          plan_id: 7,
          device_id: 6,
          status: 'PENDING',
          started_at: null,
        },
      ],
    });
    const onConfirm = vi.fn();
    renderDialog({ hostId: 'host-202', onConfirm });

    // Active job count + rows visible
    expect(await screen.findByTestId('host-active-job-count')).toHaveTextContent('2');
    expect(screen.getByTestId('hot-update-active-job-3001')).toHaveTextContent('PlanRun #12');
    expect(screen.getByTestId('hot-update-active-job-3002')).toHaveTextContent('Device #6');

    // Confirm starts disabled with the "需先勾选确认" hint
    const confirm = screen.getByTestId('host-hot-update-confirm');
    expect(confirm).toBeDisabled();
    expect(confirm).toHaveTextContent('需先勾选确认');

    // Toggle abort opt-in
    fireEvent.click(screen.getByTestId('host-hot-update-abort-toggle'));
    expect(confirm).not.toBeDisabled();
    expect(confirm).toHaveTextContent('中止 Job 并热更新');

    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledWith('host-202', { abortRunningJobs: true });
  });

  it('shows loading skeleton while host detail is in flight, confirm disabled', async () => {
    mocks.getDetail.mockReturnValueOnce(new Promise(() => undefined)); // never resolves
    renderDialog({ hostId: 'host-303' });

    expect(screen.getByTestId('host-detail-loading')).toBeInTheDocument();
    expect(screen.getByTestId('host-hot-update-confirm')).toBeDisabled();
  });

  it('cancels the dialog without firing onConfirm', async () => {
    mocks.getDetail.mockResolvedValueOnce({
      id: 'host-404',
      active_job_count: 0,
      active_jobs: [],
    });
    const onClose = vi.fn();
    const onConfirm = vi.fn();
    renderDialog({ hostId: 'host-404', onClose, onConfirm });

    await waitFor(() => screen.getByTestId('host-no-active-jobs'));
    fireEvent.click(screen.getByTestId('host-hot-update-cancel'));
    expect(onClose).toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('resets the abort toggle when reopened for a different host', async () => {
    // First open with active jobs, opt in
    mocks.getDetail.mockResolvedValueOnce({
      id: 'host-A',
      active_job_count: 1,
      active_jobs: [
        { id: 1, plan_run_id: 10, plan_id: 1, device_id: 1, status: 'RUNNING' },
      ],
    });
    const onConfirm = vi.fn();
    const { rerender } = renderDialog({ hostId: 'host-A', onConfirm });
    await waitFor(() => screen.getByTestId('host-hot-update-abort-toggle'));
    fireEvent.click(screen.getByTestId('host-hot-update-abort-toggle'));
    expect(screen.getByTestId('host-hot-update-confirm')).not.toBeDisabled();

    // Reopen for host-B (no active jobs) — toggle MUST reset.
    mocks.getDetail.mockResolvedValueOnce({
      id: 'host-B',
      active_job_count: 0,
      active_jobs: [],
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    rerender(
      <QueryClientProvider client={queryClient}>
        <HostHotUpdateConfirmDialog
          hostId="host-B"
          onClose={vi.fn()}
          onConfirm={onConfirm}
        />
      </QueryClientProvider>,
    );
    await waitFor(() => screen.getByTestId('host-no-active-jobs'));
    // Confirm now enabled again (no active jobs); the toggle isn't even rendered.
    expect(screen.queryByTestId('host-hot-update-abort-toggle')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('host-hot-update-confirm'));
    expect(onConfirm).toHaveBeenLastCalledWith('host-B', { abortRunningJobs: false });
  });

  // ── v3: retry_after_seconds live countdown ──────────────────────────

  describe('retryAfterSeconds countdown', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('decrements countdown every second in the abort-draining banner', async () => {
      mocks.getDetail.mockResolvedValueOnce({
        id: 'host-999',
        active_job_count: 1,
        active_jobs: [
          {
            id: 9001,
            plan_run_id: 99,
            plan_id: 9,
            device_id: 5,
            status: 'RUNNING',
            started_at: '2026-05-08T12:00:00Z',
            abort_pending: true,
          },
        ],
      });
      renderDialog({ hostId: 'host-999', retryAfterSeconds: 75 });

      // Let async queries resolve under fake timers
      await act(() => vi.advanceTimersByTimeAsync(500));

      // Banner visible with initial countdown
      const retryEl = screen.getByTestId('host-retry-after');
      expect(retryEl).toHaveTextContent('75');

      // Advance 2 seconds — countdown must decrement
      act(() => {
        vi.advanceTimersByTime(2000);
      });
      expect(retryEl).toHaveTextContent('73');
    });
  });
});
