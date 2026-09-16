import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import {
  invalidateCrossClientSyncQueries,
  useCrossClientSync,
} from '@/hooks/useCrossClientSync';

const socketOptions: { current?: Record<string, unknown> } = {};

vi.mock('@/hooks/useSocketIO', () => ({
  useSocketIO: vi.fn((_sub: unknown, opts: Record<string, unknown>) => {
    socketOptions.current = opts;
  }),
}));

describe('useCrossClientSync', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    socketOptions.current = undefined;
    vi.clearAllMocks();
  });

  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );

  it('invalidates plan and project sync queries on socket reconnect', () => {
    const spy = vi.spyOn(queryClient, 'invalidateQueries');

    renderHook(() => useCrossClientSync(), { wrapper });

    expect(socketOptions.current?.onConnect).toBeTypeOf('function');
    (socketOptions.current?.onConnect as () => void)();

    const keys = spy.mock.calls.map((call) => call[0]?.queryKey?.[0]);
    expect(keys).toContain('plans');
    expect(keys).toContain('plan');
    expect(keys).toContain('projects');
    expect(keys).toContain('project');
    expect(keys).toContain('devices');
    expect(keys).toContain('project-devices');
    expect(keys).toContain('projects-for-plan-editor');
    expect(keys).toContain('project-models');
  });

  it('invalidates scoped sync queries on visibility restore (#2369)', () => {
    const spy = vi.spyOn(queryClient, 'invalidateQueries');

    renderHook(() => useCrossClientSync(), { wrapper });

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    });
    document.dispatchEvent(new Event('visibilitychange'));

    const keys = spy.mock.calls.map((call) => call[0]?.queryKey?.[0]);
    expect(keys).toContain('plans');
    expect(keys).toContain('projects');
    // Must NOT nuke the whole cache (no-arg invalidateQueries).
    expect(
      spy.mock.calls.some(
        (call) => call.length === 0 || call[0] === undefined || Object.keys(call[0] ?? {}).length === 0,
      ),
    ).toBe(false);
  });
});

describe('invalidateCrossClientSyncQueries', () => {
  it('covers all cross-client sync query roots', () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, 'invalidateQueries');

    invalidateCrossClientSyncQueries(qc);

    expect(spy).toHaveBeenCalledTimes(8);
  });
});
