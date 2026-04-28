// Domain-split API modules — see frontend/src/utils/api/ for individual files.
// This barrel preserves backward-compat: `import { api } from '@/utils/api'` still works.
export { default, api, unwrapApiResponse } from './api/index';
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
} from './api/index';
