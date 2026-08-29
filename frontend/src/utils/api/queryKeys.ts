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
   *  ADR-0029：projectKey / specialtyKey 维度（页面级筛选）。
   */
  list: (limit: number, projectKey?: string | null, specialtyKey?: string | null) =>
    ['plans', { limit, projectKey: projectKey ?? null, specialtyKey: specialtyKey ?? null }] as const,

  /** Invalidation key that matches ALL plan list queries regardless of limit.
   *  react-query partial matching: ['plans'] matches ['plans', {limit: X}].
   */
  allLists: () => ['plans'] as const,
  detail: (id: number) => ['plan', id] as const,
} as const;

export const hostKeys = {
  /** Always use fetchHostList() as queryFn — cache must store Host[], not PaginatedResponse. */
  list: () => ['hosts'] as const,
  /** Host 详情（含 active_jobs 占用明细，仅 GET /hosts/{id} 返回）。 */
  detail: (id: string | number) => ['host', String(id)] as const,
} as const;

/** Bulk active-job occupancy (GET /jobs/active-by-device). */
export const jobKeys = {
  activeByDevice: () => ['jobs', 'active-by-device'] as const,
} as const;

export const deviceKeys = {
  /** ADR-0029：projectKey 维度——设备页项目筛选走后端（未知 key 404 语义）。 */
  list: (projectKey?: string | null, unassigned = false) =>
    ['devices', { projectKey: projectKey ?? null, unassigned }] as const,
  /** 全量设备（fetchAllDevices 分页拉全）— PlanExecutePage 等需要完整设备视图的页面用。 */
  all: () => ['devices-all'] as const,
} as const;

export const planRunKeys = {
  detail: (id: number) => ['plan-run', id] as const,
  timeline: (id: number) => ['plan-run-timeline', id] as const,
  devices: (
    id: number,
    status?: string,
    hostId?: number | string | null,
    linkStatus?: string,
  ) => ['plan-run-devices', id, status, hostId, linkStatus] as const,
  /** Partial key — invalidates all device queries for a PlanRun. */
  devicesByRun: (id: number) => ['plan-run-devices', id] as const,
  watcher: (id: number, scope?: string) => ['plan-run-watcher', id, scope] as const,
  watcherByRun: (id: number) => ['plan-run-watcher', id] as const,
  chain: (id: number) => ['plan-run-chain', id] as const,
  logs: (id: number, stage: string, severity: string, page: number) =>
    ['plan-run-logs', id, stage, severity, page] as const,
  /** Partial key — invalidates all log queries for a PlanRun. */
  logsByRun: (id: number) => ['plan-run-logs', id] as const,
  /** ADR-0029：projectKey 维度（页面级筛选）。前缀仍为 ['plan-runs-list']。 */
  list: (projectKey?: string | null) =>
    ['plan-runs-list', { projectKey: projectKey ?? null }] as const,
} as const;

export const dedupKeys = {
  status: (runId: number) => ['dedup-status', runId] as const,
} as const;

export const notificationKeys = {
  channels: () => ['notifications', 'channels'] as const,
  rules: () => ['notifications', 'rules'] as const,
} as const;

export const scheduleKeys = {
  list: () => ['schedules'] as const,
} as const;

/** Job report keys — `jobId` is Job.id, not PlanRun.id. */
export const jobReportKeys = {
  report: (jobId: number) => ['job-report', jobId] as const,
  jiraDraft: (jobId: number) => ['job-jira-draft', jobId] as const,
} as const;

/** ADR-0029 项目登记簿 — 页面级独立筛选（无全局选择器，D8 挂起）。 */
export const projectKeys = {
  list: () => ['projects'] as const,
  detail: (key: string) => ['project', key] as const,
  inventoryModels: () => ['projects', 'inventory-models'] as const,
  inventorySummary: () => ['projects', 'inventory-summary'] as const,
  modelsOf: (key: string) => ['project-models', key] as const,
  /** 某项目下的设备（设备页筛选/详情页设备块共用；key 空 = 全量） */
  devicesOf: (key: string | null | undefined) => ['project-devices', key ?? '*'] as const,
  plansOf: (key: string | null | undefined) => ['project-plans', key ?? '*'] as const,
  summaryOf: (key: string | null | undefined) => ['project-summary', key ?? '*'] as const,
} as const;

/** ADR-0031 平台 AI 助手。 */
export const aiAssistantKeys = {
  config: () => ['ai-assistant-config'] as const,
  sessions: () => ['ai-assistant-sessions'] as const,
  messages: (sessionId: number) => ['ai-assistant-messages', sessionId] as const,
  action: (actionId: number) => ['ai-assistant-action', actionId] as const,
  /** 长命令日志——running 时由调用方开 refetchInterval 轮询。 */
  actionLog: (actionId: number) => ['ai-assistant-action-log', actionId] as const,
} as const;
