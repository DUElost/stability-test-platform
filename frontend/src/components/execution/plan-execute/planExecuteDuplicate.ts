import type { PlanRun } from '@/utils/api';

/** 近窗：同 Plan 发起后多少毫秒内视为「近期」。 */
export const DUPLICATE_WINDOW_MS = 30 * 60 * 1000;
/** 重叠率阈值：交集 / 本次选中。 */
export const DUPLICATE_OVERLAP_RATIO = 0.5;
/** 交集绝对下限，避免 1～2 台误报。 */
export const DUPLICATE_MIN_INTERSECTION = 3;
/** 弱提示：设备数接近（±20%）。 */
export const DUPLICATE_WEAK_DEVICE_COUNT_TOLERANCE = 0.2;
/** 列表缺 device ids 时，最多按需 get 的候选数。 */
export const DUPLICATE_CANDIDATE_FETCH_LIMIT = 3;

export type DuplicateKind = 'overlap' | 'weak';

export interface DuplicateMatch {
  kind: DuplicateKind;
  runId: number;
  startedAt: string;
  status: string;
  deviceCount: number;
  overlapCount?: number;
  overlapRatio?: number;
}

export function parseStartedAtMs(startedAt: string | null | undefined): number | null {
  if (!startedAt) return null;
  const ms = Date.parse(startedAt);
  return Number.isFinite(ms) ? ms : null;
}

export function isWithinWindow(
  startedAt: string | null | undefined,
  nowMs: number,
  windowMs = DUPLICATE_WINDOW_MS,
): boolean {
  const startedMs = parseStartedAtMs(startedAt);
  if (startedMs == null) return false;
  const delta = nowMs - startedMs;
  return delta >= 0 && delta <= windowMs;
}

/** 优先 run_context.dispatch_device_ids；无有效数组则返回 null（触发 get 或弱降级）。 */
export function extractDispatchDeviceIds(
  run: Pick<PlanRun, 'run_context'> | null | undefined,
): number[] | null {
  const raw = run?.run_context?.dispatch_device_ids;
  if (!Array.isArray(raw) || raw.length === 0) return null;
  const ids = raw
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id) && id > 0);
  return ids.length > 0 ? ids : null;
}

/** 展示用设备数：dispatch ids → result_summary.total。 */
export function estimateDeviceCount(run: PlanRun | null | undefined): number | null {
  if (!run) return null;
  const fromDispatch = extractDispatchDeviceIds(run);
  if (fromDispatch) return fromDispatch.length;
  const total = run.result_summary?.total;
  if (typeof total === 'number' && Number.isFinite(total) && total >= 0) return total;
  return null;
}

export function computeOverlap(
  selectedIds: Iterable<number>,
  runDeviceIds: Iterable<number>,
): { intersection: number; ratio: number } {
  const selected = new Set(
    Array.from(selectedIds).filter((id) => Number.isFinite(id) && id > 0),
  );
  if (selected.size === 0) return { intersection: 0, ratio: 0 };
  let intersection = 0;
  for (const id of runDeviceIds) {
    if (selected.has(id)) intersection += 1;
  }
  return { intersection, ratio: intersection / selected.size };
}

export function isStrongDuplicate(
  selectedCount: number,
  intersection: number,
  ratio: number,
): boolean {
  if (selectedCount <= 0) return false;
  return ratio >= DUPLICATE_OVERLAP_RATIO && intersection >= DUPLICATE_MIN_INTERSECTION;
}

export function isWeakDeviceCountMatch(
  selectedCount: number,
  runCount: number,
  tolerance = DUPLICATE_WEAK_DEVICE_COUNT_TOLERANCE,
): boolean {
  if (selectedCount <= 0 || runCount <= 0) return false;
  const lo = selectedCount * (1 - tolerance);
  const hi = selectedCount * (1 + tolerance);
  return runCount >= lo && runCount <= hi;
}

export interface DuplicateCandidate {
  run: PlanRun;
  /** null = 仍无设备集，只能走弱降级。 */
  deviceIds: number[] | null;
}

/**
 * 在近窗候选中找最值得提示的一条：优先强重叠，否则弱「设备数接近」。
 * 调用方应先过滤 started_at 在窗内；本函数仍会再校验一次。
 */
export function findDuplicateMatch(
  selectedIds: Iterable<number>,
  candidates: DuplicateCandidate[],
  nowMs: number = Date.now(),
): DuplicateMatch | null {
  const selected = Array.from(new Set(
    Array.from(selectedIds).filter((id) => Number.isFinite(id) && id > 0),
  ));
  if (selected.length === 0) return null;

  const inWindow = candidates.filter((c) => isWithinWindow(c.run.started_at, nowMs));
  let bestStrong: DuplicateMatch | null = null;
  let bestWeak: DuplicateMatch | null = null;

  for (const { run, deviceIds } of inWindow) {
    const deviceCount = deviceIds?.length ?? estimateDeviceCount(run) ?? 0;
    if (deviceIds && deviceIds.length > 0) {
      const { intersection, ratio } = computeOverlap(selected, deviceIds);
      if (isStrongDuplicate(selected.length, intersection, ratio)) {
        const match: DuplicateMatch = {
          kind: 'overlap',
          runId: run.id,
          startedAt: run.started_at,
          status: run.status,
          deviceCount,
          overlapCount: intersection,
          overlapRatio: ratio,
        };
        if (
          !bestStrong
          || (match.overlapCount ?? 0) > (bestStrong.overlapCount ?? 0)
          || (
            (match.overlapCount ?? 0) === (bestStrong.overlapCount ?? 0)
            && parseStartedAtMs(match.startedAt)! > parseStartedAtMs(bestStrong.startedAt)!
          )
        ) {
          bestStrong = match;
        }
      }
      continue;
    }

    if (isWeakDeviceCountMatch(selected.length, deviceCount)) {
      const match: DuplicateMatch = {
        kind: 'weak',
        runId: run.id,
        startedAt: run.started_at,
        status: run.status,
        deviceCount,
      };
      if (
        !bestWeak
        || parseStartedAtMs(match.startedAt)! > parseStartedAtMs(bestWeak.startedAt)!
      ) {
        bestWeak = match;
      }
    }
  }

  return bestStrong ?? bestWeak;
}

/** 近窗内缺 device ids 的 run，按时间新→旧取最多 N 条去 get。 */
export function pickRunsNeedingDeviceFetch(
  runs: PlanRun[],
  nowMs: number = Date.now(),
  limit = DUPLICATE_CANDIDATE_FETCH_LIMIT,
): number[] {
  return runs
    .filter((run) => isWithinWindow(run.started_at, nowMs))
    .filter((run) => extractDispatchDeviceIds(run) == null)
    .slice(0, limit)
    .map((run) => run.id);
}
