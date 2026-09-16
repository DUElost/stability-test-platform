export const SOCKET_EVENT_NAMES = {
  deviceUpdate: 'device_update',
  dashboardSummary: 'dashboard_summary',
  jobStatus: 'job_status',
  planRunStatus: 'plan_run_status',
  precheckUpdate: 'precheck_update',
  watcherSignal: 'watcher_signal',
  consoleLog: 'console_log',
  consoleStatus: 'console_status',
  notificationNew: 'notification:new',
  planChanged: 'plan_changed',
  projectChanged: 'project_changed',
} as const;

export const SOCKET_MESSAGE_TYPES = {
  DEVICE_UPDATE: 'DEVICE_UPDATE',
  DASHBOARD_SUMMARY: 'DASHBOARD_SUMMARY',
  JOB_STATUS: 'JOB_STATUS',
  PLAN_RUN_STATUS: 'PLAN_RUN_STATUS',
  PRECHECK_UPDATE: 'PRECHECK_UPDATE',
  DEPLOY_UPDATE: 'DEPLOY_UPDATE',
  WATCHER_SIGNAL: 'WATCHER_SIGNAL',
  PLAN_CHANGED: 'PLAN_CHANGED',
  PROJECT_CHANGED: 'PROJECT_CHANGED',
} as const;

export type SocketEventName = typeof SOCKET_EVENT_NAMES[keyof typeof SOCKET_EVENT_NAMES];
export type SocketMessageType = typeof SOCKET_MESSAGE_TYPES[keyof typeof SOCKET_MESSAGE_TYPES];
