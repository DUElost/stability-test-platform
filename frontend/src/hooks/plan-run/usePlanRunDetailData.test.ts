import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, type ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { usePlanRunDetailData } from './usePlanRunDetailData';
import { planRunKeys } from '@/utils/api/queryKeys';

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  getTimeline: vi.fn(),
  getDevices: vi.fn(),
  getWatcherSummary: vi.fn(),
  getChain: vi.fn(),
  jobParameterProjection: vi.fn(),
}));

vi.mock('@/hooks/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), promise: vi.fn() }),
}));

vi.mock('@/hooks/useSocketIO', () => ({
  useSocketIO: () => ({
    isConnected: true,
    connectionStatus: 'connected',
    lastMessage: null,
    sendMessage: vi.fn(),
    reconnectAttempt: 0,
    connect: vi.fn(),
    disconnect: vi.fn(),
  }),
}));

vi.mock('@/utils/api', () => ({
  api: {
    planRuns: {
      get: mocks.get,
      getTimeline: mocks.getTimeline,
      getDevices: mocks.getDevices,
      getWatcherSummary: mocks.getWatcherSummary,
      getChain: mocks.getChain,
      jobParameterProjection: mocks.jobParameterProjection,
      abort: vi.fn(),
      triggerExtract: vi.fn(),
      manualRetryJob: vi.fn(),
      manualExitJob: vi.fn(),
      retryDispatch: vi.fn(),
    },
  },
}));

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return createElement(QueryClientProvider, { client }, children);
}

const filters = {
  deviceStatusFilter: 'all' as const,
  deviceLinkFilter: 'all' as const,
  deviceHostFilter: 'all',
  watcherTimeScope: 'all' as const,
};

describe('usePlanRunDetailData (#3653 U4)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockResolvedValue({
      id: 12,
      plan_id: 7,
      status: 'RUNNING',
      parameter_projection: { layer: 'L2', context: { read_at: 't', authority: 'snap' } },
      plan_snapshot: { schema_version: 1, plan: {}, steps: [] },
    });
    mocks.getTimeline.mockResolvedValue({ plan_run_id: 12, plan_name: 'demo', stages: [] });
    mocks.getDevices.mockResolvedValue({ plan_run_id: 12, total: 0, by_status: {}, by_host: {}, devices: [] });
    mocks.getWatcherSummary.mockResolvedValue({ plan_run_id: 12 });
    mocks.getChain.mockResolvedValue({ plan_run_id: 12, items: [] });
    mocks.jobParameterProjection.mockResolvedValue({
      layer: 'L3',
      context: {
        job_id: 101,
        device_id: 11,
        host_id: 'host-a',
        read_at: '2026-10-11T00:00:00Z',
        authority: 'job',
      },
    });
  });

  it('does not fetch L3 until a projectionJobId is selected', async () => {
    const { result } = renderHook(
      () => usePlanRunDetailData(12, { ...filters, projectionJobId: null }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.runQ.isSuccess).toBe(true));
    expect(mocks.jobParameterProjection).not.toHaveBeenCalled();
    expect(result.current.runQ.data?.parameter_projection?.layer).toBe('L2');
  });

  it('fetches L3 with planRunKeys.jobParameterProjection and does not reuse another job cache key', async () => {
    const { result, rerender } = renderHook(
      ({ jobId }: { jobId: number | null }) =>
        usePlanRunDetailData(12, { ...filters, projectionJobId: jobId }),
      { wrapper, initialProps: { jobId: 101 as number | null } },
    );

    await waitFor(() => expect(result.current.jobProjectionQ.isSuccess).toBe(true));
    expect(mocks.jobParameterProjection).toHaveBeenCalledWith(12, 101);
    expect(planRunKeys.jobParameterProjection(12, 101)).toEqual([
      'plan-run', 12, 'job-parameter-projection', 101,
    ]);
    expect(planRunKeys.jobParameterProjection(12, 202)).not.toEqual(
      planRunKeys.jobParameterProjection(12, 101),
    );
    expect(result.current.jobProjectionQ.data?.context.job_id).toBe(101);

    mocks.jobParameterProjection.mockResolvedValueOnce({
      layer: 'L3',
      context: {
        job_id: 202,
        device_id: 22,
        host_id: 'host-b',
        read_at: '2026-10-11T00:05:00Z',
        authority: 'job-b',
      },
    });
    rerender({ jobId: 202 });
    await waitFor(() => expect(result.current.jobProjectionQ.data?.context.job_id).toBe(202));
    expect(mocks.jobParameterProjection).toHaveBeenCalledWith(12, 202);
    expect(result.current.jobProjectionQ.data?.context.device_id).toBe(22);
    expect(result.current.jobProjectionQ.data?.context.host_id).toBe('host-b');
  });

  it('surfaces L3 read failures without inventing a projection', async () => {
    mocks.jobParameterProjection.mockRejectedValueOnce(new Error('boom'));
    const { result } = renderHook(
      () => usePlanRunDetailData(12, { ...filters, projectionJobId: 101 }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.jobProjectionQ.isError).toBe(true));
    expect(result.current.jobProjectionQ.data).toBeUndefined();
  });
});
