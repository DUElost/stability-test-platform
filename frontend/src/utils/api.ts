// Domain-split API modules — see frontend/src/utils/api/ for individual files.
// This barrel preserves backward-compat: `import { api } from '@/utils/api'` still works.
export { ApiError, default, api, unwrapApiResponse } from './api/index';
export type {
  Host, Device, Task, RunStep, TaskRun,
  RuntimeLogEntry, RuntimeLogQueryResponse, LogArtifact,
  RunRiskSummary, RunRiskAlert, RunReport, JiraDraft,
  PipelineTemplate, AgentLogOut, User,
  RunsByStatus, TestTypeStat, RiskDistribution, RecentRun, ResultsSummary,
  ActivityPoint, ActivityResponse, DeviceMetricPoint, DeviceMetricsResponse,
  CompletionTrendPoint, CompletionTrendResponse,
  NotificationChannel, AlertRule,
  TaskSchedule, TaskScheduleCreatePayload, TaskScheduleUpdatePayload, ScheduleRunNowResult,
  PaginatedResponse,
  ScriptEntry,
  ActionTemplateEntry, ActionTemplateCreatePayload, ActionTemplateUpdatePayload,
  PipelineStep, PipelinePhase, PipelinePatrol, PipelineDef,
  JobStatus, StepTrace, JobArtifactEntry,
  Plan, PlanStep, PlanStepCreate, PlanCreate, PlanUpdate,
  PlanRunStatus, PlanRunType, PlanRun, PlanRunCreate, PlanRunPreview,
  PlanJobInstance, PlanRunSummary,
} from './api/index';
