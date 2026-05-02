export { default } from './client';
export { unwrapApiResponse } from './client';
export { auth } from './auth';
export { hosts, heartbeat, deploy } from './hosts';
export { devices } from './devices';
export { logs } from './logs';
export { pipeline } from './pipeline';
export { tools, toolCatalog, builtinCatalog, actionTemplates, scripts } from './tools';
export { scriptSequences, scriptExecutions } from './scripts';
export { resourcePools } from './resourcePools';
export { results, stats } from './analytics';
export { users, notifications, schedules, templates, audit } from './management';
export { orchestration, execution } from './orchestration';

export type {
  Host, Device, Task, RunStep, TaskRun,
  RuntimeLogEntry, RuntimeLogQueryResponse, LogArtifact,
  RunRiskSummary, RunRiskAlert, RunReport, JiraDraft,
  TaskTemplate, PipelineTemplate, AgentLogOut, User,
  RunsByStatus, TestTypeStat, RiskDistribution, RecentRun, ResultsSummary,
  ActivityPoint, ActivityResponse, DeviceMetricPoint, DeviceMetricsResponse,
  CompletionTrendPoint, CompletionTrendResponse,
  NotificationChannel, AlertRule,
  TaskSchedule, TaskScheduleCreatePayload, TaskScheduleUpdatePayload, ScheduleRunNowResult,
  PaginatedResponse,
  ToolEntry, BuiltinActionEntry, BuiltinActionUpdatePayload,
  ScriptEntry,
  ScriptSequenceItem, ScriptSequence, ScriptSequencePayload, ScriptSequenceList,
  ScriptExecutionCreatePayload, ScriptExecutionCreated, ScriptExecutionListItem,
  ScriptExecutionList, ScriptExecutionStep, ScriptExecutionJob, ScriptExecutionDetail,
  ActionTemplateEntry, ActionTemplateCreatePayload, ActionTemplateUpdatePayload,
  PipelineStep, PipelineDef, PipelineStepOverride, TaskTemplateEntry,
  WorkflowDefinition, WorkflowDefinitionCreate,
  WorkflowStatus, JobStatus, StepTrace, JobInstance, PaginatedJobList,
  WorkflowRun, WorkflowRunCreate, WorkflowRunPreview, WorkflowRunPreviewTemplate,
  WorkflowSummary, JobArtifactEntry,
  Tool, ToolCategory,
  ResourcePool, ResourcePoolLoad, ResourcePoolCreatePayload,
} from './types';

import { auth } from './auth';
import { hosts, heartbeat, deploy } from './hosts';
import { devices } from './devices';
import { logs } from './logs';
import { pipeline } from './pipeline';
import { tools, toolCatalog, builtinCatalog, actionTemplates, scripts } from './tools';
import { scriptSequences, scriptExecutions } from './scripts';
import { resourcePools } from './resourcePools';
import { results, stats } from './analytics';
import { users, notifications, schedules, templates, audit } from './management';
import { orchestration, execution } from './orchestration';

export const api = {
  auth,
  hosts,
  heartbeat,
  deploy,
  devices,
  logs,
  pipeline,
  tools,
  results,
  stats,
  users,
  notifications,
  schedules,
  templates,
  audit,
  orchestration,
  execution,
  toolCatalog,
  builtinCatalog,
  actionTemplates,
  scripts,
  scriptSequences,
  scriptExecutions,
  resourcePools,
};
