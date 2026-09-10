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
});

describe('invalidateCrossClientSyncQueries', () => {
  it('covers all cross-client sync query roots', () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, 'invalidateQueries');

    invalidateCrossClientSyncQueries(qc);

    expect(spy).toHaveBeenCalledTimes(8);
  });
});
