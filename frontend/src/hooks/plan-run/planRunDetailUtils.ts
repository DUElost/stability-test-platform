import type { DeviceMatrixItem, PlanDispatchState, PlanRun, WatcherTimeScope } from '@/utils/api/types';
import { isPlanRunTerminal } from '@/components/plan-run/planRunStatus';

export const GATE_ACTIVE_REFETCH_MS = 3_000;
export const FAST_REFETCH_MS = 10_000;
export const SLOW_REFETCH_MS = 30_000;

/** Patrol/init stale thresholds removed (#520) — recycler owns stuck policy via API fields. */

const WATCHER_TIME_SCOPE_MAP: Record<string, WatcherTimeScope> = {
  all: 'all',
  '15m': '15m',
  '1h': '1h',
  '6h': '6h',
  '24h': '24h',
  '15': '15m',
  '60': '1h',
  '360': '6h',
  '1440': '24h',
};

export function normalizeWatcherTimeScope(value: string | null): WatcherTimeScope {
  if (!value) return 'all';
  return WATCHER_TIME_SCOPE_MAP[value] ?? 'all';
}

/** Backfill V2 runs that reached RUNNING before dispatch_state was completed. */
export function normalizeDispatchStateForRun(
  run: PlanRun | undefined,
  dispatchState: PlanDispatchState | null | undefined,
): PlanDispatchState | null {
  if (!run || !dispatchState) return dispatchState ?? null;
  if (
    run.status === 'RUNNING' &&
    !run.run_context?.precheck &&
    dispatchState.status === 'queued'
  ) {
    return { ...dispatchState, status: 'completed' };
  }
  return dispatchState;
}

export function shouldShowDispatchGate(run: PlanRun | undefined): boolean {
  if (!run) return false;

  if (run.status === 'QUEUED' || run.status === 'PRECHECK') {
    return true;
  }

  const summary = run.result_summary;
  const admissionFailed =
    run.status === 'FAILED' &&
    (summary?.dispatch_failed === true || summary?.precheck_failed === true);
  const dispatchState = normalizeDispatchStateForRun(run, run.run_context?.dispatch_state);
  const dispatchFailed =
    dispatchState?.status === 'failed' || admissionFailed;

  if (dispatchFailed) return true;
  if (run.run_context?.precheck) return true;

  // V2 admission path: no precheck blob; dispatch_state is the gate contract.
  if (run.status === 'RUNNING' && dispatchState) return true;

  return false;
}

export function isDispatchGateActive(run: PlanRun | undefined): boolean {
  if (!run) return false;

  if (run.status === 'QUEUED' || run.status === 'PRECHECK') {
    return true;
  }

  if (run.status !== 'RUNNING') {
    const summary = run.result_summary;
    return (
      run.status === 'FAILED' &&
      (summary?.dispatch_failed === true || summary?.precheck_failed === true)
    );
  }

  const precheck = run.run_context?.precheck;
  const dispatch = run.run_context?.dispatch_state;

  if (!precheck) {
    return dispatch?.status === 'queued' || dispatch?.status === 'running';
  }

  if (precheck.phase !== 'ready' && precheck.phase !== 'failed') {
    return true;
  }

  if (precheck.phase === 'ready') {
    const dispatchStatus = dispatch?.status;
    return dispatchStatus !== 'completed' && dispatchStatus !== 'failed';
  }

  return false;
}

export function isJobStuck(d: DeviceMatrixItem, now = Date.now()): boolean {
  if (d.job_status !== 'RUNNING') return false;
  if (typeof d.is_stuck === 'boolean') return d.is_stuck;
  if (d.heartbeat_deadline_at) {
    const deadline = new Date(d.heartbeat_deadline_at).getTime();
    if (!Number.isNaN(deadline)) return now >= deadline;
  }
  return false;
}

export function planRunRefetchInterval(
  run: PlanRun | undefined,
  isTerminal: boolean,
): number | false {
  if (isTerminal) return false;
  return isDispatchGateActive(run) ? GATE_ACTIVE_REFETCH_MS : FAST_REFETCH_MS;
}

export { isPlanRunTerminal };
