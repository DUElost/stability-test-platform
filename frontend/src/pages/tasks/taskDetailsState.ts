import type { PlanJobInstance, LogArtifact, RunReport } from '../../utils/api';

const TERMINAL_JOB_STATUSES = new Set<PlanJobInstance['status']>(['COMPLETED', 'FAILED', 'ABORTED']);

export function isTerminalJobStatus(status?: PlanJobInstance['status'] | null): boolean {
  return status ? TERMINAL_JOB_STATUSES.has(status) : false;
}

export function shouldPollJobData(activeRun?: Pick<PlanJobInstance, 'status'> | null): boolean {
  return !!activeRun && !isTerminalJobStatus(activeRun.status);
}

export function getWorkflowDisplayStatus(activeRun?: Pick<PlanJobInstance, 'status'> | null): string {
  return activeRun?.status ?? 'PENDING';
}

export function getLatestArtifact(report?: Pick<RunReport, 'run'> | null): LogArtifact | null {
  const artifacts = report?.run?.artifacts ?? [];
  return artifacts.length > 0 ? artifacts[artifacts.length - 1] : null;
}
