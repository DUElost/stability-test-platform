import { describe, expect, it } from 'vitest';
import { createQueryClient } from './QueryProvider';

describe('createQueryClient', () => {
  it('applies layered staleTime defaults by query key prefix', () => {
    const queryClient = createQueryClient();

    const unknown = queryClient.defaultQueryOptions({
      queryKey: ['unknown'],
      queryFn: async () => null,
    });
    const users = queryClient.defaultQueryOptions({
      queryKey: ['users'],
      queryFn: async () => [],
    });
    const plans = queryClient.defaultQueryOptions({
      queryKey: ['plans', { limit: 100 }],
      queryFn: async () => [],
    });
    const retiredTasksKey = queryClient.defaultQueryOptions({
      queryKey: ['tasks', 1],
      queryFn: async () => null,
    });
    const planRun = queryClient.defaultQueryOptions({
      queryKey: ['plan-run', 42],
      queryFn: async () => null,
    });

    expect(unknown.staleTime).toBe(5_000);
    expect(users.staleTime).toBe(5 * 60 * 1_000);
    expect(plans.staleTime).toBe(60 * 1_000);
    expect(retiredTasksKey.staleTime).toBe(5_000);
    expect(planRun.staleTime).toBe(0);
  });
});
