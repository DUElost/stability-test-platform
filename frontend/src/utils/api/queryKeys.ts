/**
 * Query key factories for consistent react-query cache management.
 *
 * Each factory produces query keys with the same structure that react-query
 * uses for deep equality matching.  Components that subscribe to the same
 * data with different query parameters use distinct keys to prevent
 * cross-consumer cache collisions.
 */

export const planKeys = {
  /** Plan list queries — scoped by limit to avoid cache collision between
   *  PlanListPage (limit=100) and PlanExecutePage (limit=100).
   */
  list: (limit: number) => ['plans', { limit }] as const,

  /** Invalidation key that matches ALL plan list queries regardless of limit.
   *  react-query partial matching: ['plans'] matches ['plans', {limit: X}].
   */
  allLists: () => ['plans'] as const,
  detail: (id: number) => ['plan', id] as const,
} as const;

export const hostKeys = {
  list: () => ['hosts'] as const,
} as const;

export const deviceKeys = {
  list: () => ['devices'] as const,
} as const;

export const planRunKeys = {
  detail: (id: number) => ['plan-run', id] as const,
  timeline: (id: number) => ['plan-run-timeline', id] as const,
  devices: (id: number, status?: string, hostId?: number | string | null) =>
    ['plan-run-devices', id, status, hostId] as const,
  /** Partial key — invalidates all device queries for a PlanRun. */
  devicesByRun: (id: number) => ['plan-run-devices', id] as const,
  watcher: (id: number, scope?: string) => ['plan-run-watcher', id, scope] as const,
  watcherByRun: (id: number) => ['plan-run-watcher', id] as const,
  chain: (id: number) => ['plan-run-chain', id] as const,
  logs: (id: number, stage: string, severity: string, page: number) =>
    ['plan-run-logs', id, stage, severity, page] as const,
  /** Partial key — invalidates all log queries for a PlanRun. */
  logsByRun: (id: number) => ['plan-run-logs', id] as const,
  list: () => ['plan-runs-list'] as const,
} as const;

export const dedupKeys = {
  status: (runId: number) => ['dedup-status', runId] as const,
} as const;

export const notificationKeys = {
  channels: () => ['notifications', 'channels'] as const,
  rules: () => ['notifications', 'rules'] as const,
} as const;

/** Job report keys — `jobId` is Job.id, not PlanRun.id. */
export const jobReportKeys = {
  report: (jobId: number) => ['job-report', jobId] as const,
  jiraDraft: (jobId: number) => ['job-jira-draft', jobId] as const,
} as const;
