import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

const GLOBAL_QUERY_DEFAULTS = {
  refetchOnWindowFocus: false,
  retry: 1,
  staleTime: 5_000,
} as const;

const ADMIN_QUERY_KEYS = [['users'], ['notifications']] as const;
const REFERENCE_QUERY_KEYS = [['plans'], ['plan'], ['scripts-active']] as const;
const OPERATIONAL_QUERY_KEYS = [
  ['dashboard-summary'],
  ['stats-activity'],
  ['stats-completion-trend'],
  ['hosts'],
  ['devices'],
  ['devices-all'],
  ['runs'],
  ['results'],
  ['results-summary'],
  ['task-runs'],
  ['plan-runs-list'],
  ['job-report'],
  ['job-jira-draft'],
  ['runs-with-jira-drafts'],
  ['resource-pools'],
] as const;
const LIVE_QUERY_KEYS = [
  ['plan-run'],
  ['plan-run-timeline'],
  ['plan-run-devices'],
  ['plan-run-watcher'],
  ['plan-run-chain'],
  ['plan-run-logs'],
  ['plan-run-jobs'],
  ['dedup-status'],
  ['host-detail'],
  ['device-metrics'],
] as const;

function applyStaleTimeDefaults(
  queryClient: QueryClient,
  keys: ReadonlyArray<readonly unknown[]>,
  staleTime: number,
) {
  for (const key of keys) {
    queryClient.setQueryDefaults(key, { staleTime });
  }
}

export function createQueryClient() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: GLOBAL_QUERY_DEFAULTS,
    },
  });

  applyStaleTimeDefaults(queryClient, ADMIN_QUERY_KEYS, 5 * 60 * 1_000);
  applyStaleTimeDefaults(queryClient, REFERENCE_QUERY_KEYS, 60 * 1_000);
  applyStaleTimeDefaults(queryClient, OPERATIONAL_QUERY_KEYS, 15 * 1_000);
  applyStaleTimeDefaults(queryClient, LIVE_QUERY_KEYS, 0);

  return queryClient;
}

const queryClient = createQueryClient();

export function clearAppQueryCache() {
  queryClient.clear();
}

export function QueryProvider({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  );
}
