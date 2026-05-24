export const SOCKET_EVENT_NAMES = {
  deviceUpdate: 'device_update',
  stepLog: 'step_log',
  stepUpdate: 'step_update',
  jobStatus: 'job_status',
  planRunStatus: 'plan_run_status',
  precheckUpdate: 'precheck_update',
  runUpdate: 'run_update',
  taskUpdate: 'task_update',
  reportReady: 'report_ready',
  jobUpdate: 'job_update',
  watcherSignal: 'watcher_signal',
} as const;

export const SOCKET_MESSAGE_TYPES = {
  DEVICE_UPDATE: 'DEVICE_UPDATE',
  STEP_LOG: 'STEP_LOG',
  STEP_UPDATE: 'STEP_UPDATE',
  JOB_STATUS: 'JOB_STATUS',
  PLAN_RUN_STATUS: 'PLAN_RUN_STATUS',
  PRECHECK_UPDATE: 'PRECHECK_UPDATE',
  RUN_UPDATE: 'RUN_UPDATE',
  TASK_UPDATE: 'TASK_UPDATE',
  REPORT_READY: 'REPORT_READY',
  DEPLOY_UPDATE: 'DEPLOY_UPDATE',
  WATCHER_SIGNAL: 'WATCHER_SIGNAL',
} as const;

export type SocketEventName = typeof SOCKET_EVENT_NAMES[keyof typeof SOCKET_EVENT_NAMES];
export type SocketMessageType = typeof SOCKET_MESSAGE_TYPES[keyof typeof SOCKET_MESSAGE_TYPES];
