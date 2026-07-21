import type { PlanRun } from '@/utils/api';

const TERMINAL_STATUSES = new Set(['SUCCESS', 'PARTIAL_SUCCESS', 'FAILED', 'DEGRADED']);
const MIN_SAMPLES = 2;

export interface WallClockEstimate {
  averageSeconds: number | null;
  sampleCount: number;
}

export function estimatePlanWallClock(runs: PlanRun[], limit = 5): WallClockEstimate {
  const durations = runs
    .filter((run) => TERMINAL_STATUSES.has(run.status) && run.ended_at)
    .map((run) => {
      const startedAt = Date.parse(run.started_at);
      const endedAt = Date.parse(run.ended_at as string);
      return (endedAt - startedAt) / 1000;
    })
    .filter((seconds) => Number.isFinite(seconds) && seconds > 0)
    .slice(0, limit);

  if (durations.length < MIN_SAMPLES) {
    return { averageSeconds: null, sampleCount: durations.length };
  }

  return {
    averageSeconds: durations.reduce((total, seconds) => total + seconds, 0) / durations.length,
    sampleCount: durations.length,
  };
}
