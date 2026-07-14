export { default } from './client';
export {
  ApiError,
  toApiError,
  unwrapApiResponse,
  registerAuthFailureHandler,
} from './client';
export { auth } from './auth';
export { hosts, heartbeat, hotUpdate, agentInstall } from './hosts';
export { devices } from './devices';
export { logs } from './logs';
export { pipeline } from './pipeline';
export { actionTemplates, scripts } from './tools';
export { resourcePools } from './resourcePools';
export { results, stats } from './analytics';
export { users, notifications, schedules, audit } from './management';
export { plans } from './plans';
export { planRuns } from './planRuns';
export { runs } from './runs';

export type {
  Host, Device, Task, RunStep, TaskRun,
  RuntimeLogEntry, RuntimeLogQueryResponse, LogArtifact,
  RunRiskSummary, RunRiskAlert, RunReport, JiraDraft,
  JiraRunRecord,
  PipelineTemplate, AgentLogOut, User,
  RunsByStatus, TestTypeStat, RiskDistribution, RecentRun, ResultsSummary,
  ActivityPoint, ActivityResponse, DeviceMetricPoint, DeviceMetricsResponse,
  CompletionTrendPoint, CompletionTrendResponse,
  NotificationChannel, AlertRule, NotificationLog, NotificationLogsResponse, UnreadCountResponse,
  TaskSchedule, TaskScheduleCreatePayload, TaskScheduleUpdatePayload, ScheduleRunNowResult,
  PaginatedResponse,
  ScriptEntry,
  ActionTemplateEntry, ActionTemplateCreatePayload, ActionTemplateUpdatePayload,
  PipelineStep, PipelinePhase, PipelinePatrol, PipelineDef,
  JobStatus, StepTrace, JobArtifactEntry,
  ResourcePool, ResourcePoolLoad, ResourcePoolCreatePayload,
  Plan, PlanStep, PlanStepCreate, PlanCreate, PlanUpdate,
  PlanRunStatus, PlanRunType, PlanRun, PlanRunCreate, PlanRunPreview,
  PlanJobInstance, PlanRunSummary, PlanSnapshot, PlanSnapshotStep,
  PlanRunAbortRequest, PlanRunAbortResult, PlanRunCapabilities,
  JobActionCapabilities, StructuredApiError, ApiErrorCapabilities,
  HostActiveJob,
} from './types';

import { auth } from './auth';
import { hosts, heartbeat, hotUpdate, agentInstall } from './hosts';
import { devices } from './devices';
import { logs } from './logs';
import { pipeline } from './pipeline';
import { actionTemplates, scripts } from './tools';
import { resourcePools } from './resourcePools';
import { results, stats } from './analytics';
import { users, notifications, schedules, audit } from './management';
import { plans } from './plans';
import { planRuns } from './planRuns';
import { runs } from './runs';

export const api = {
  auth,
  hosts,
  heartbeat,
  hotUpdate,
  agentInstall,
  devices,
  logs,
  pipeline,
  results,
  stats,
  users,
  notifications,
  schedules,
  audit,
  actionTemplates,
  scripts,
  resourcePools,
  plans,
  planRuns,
  runs,
};
