/** Personal device-selection presets (localStorage). Complements asset tags. */

export const PRESETS_STORAGE_KEY = 'stp.planExecute.presets.v1';
export const PRESETS_MAX = 20;
export const PRESET_NAME_MAX_LEN = 40;

export interface PlanExecutePreset {
  id: string;
  name: string;
  deviceIds: number[];
  createdAt: string;
}

export interface ApplyPresetResult {
  /** Intersection of preset ids with currently schedulable devices. */
  appliedIds: number[];
  /** Preset ids that were dropped (offline / gone / unschedulable). */
  missingCount: number;
}

function normalizeName(name: string): string {
  return name.trim().replace(/\s+/g, ' ').slice(0, PRESET_NAME_MAX_LEN);
}

function normalizeDeviceIds(ids: unknown): number[] {
  if (!Array.isArray(ids)) return [];
  const seen = new Set<number>();
  const out: number[] = [];
  for (const id of ids) {
    if (!Number.isInteger(id) || id <= 0 || seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out;
}

function normalizePreset(raw: unknown): PlanExecutePreset | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const obj = raw as Record<string, unknown>;
  const name = typeof obj.name === 'string' ? normalizeName(obj.name) : '';
  const deviceIds = normalizeDeviceIds(obj.deviceIds);
  if (!name || deviceIds.length === 0) return null;
  const id = typeof obj.id === 'string' && obj.id ? obj.id : `preset-${Date.now()}`;
  const createdAt = typeof obj.createdAt === 'string' && obj.createdAt
    ? obj.createdAt
    : new Date().toISOString();
  return { id, name, deviceIds, createdAt };
}

export function loadPresets(storage: Storage = localStorage): PlanExecutePreset[] {
  try {
    const raw = storage.getItem(PRESETS_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map(normalizePreset)
      .filter((p): p is PlanExecutePreset => p != null)
      .slice(0, PRESETS_MAX);
  } catch {
    return [];
  }
}

export function persistPresets(presets: PlanExecutePreset[], storage: Storage = localStorage): void {
  try {
    storage.setItem(PRESETS_STORAGE_KEY, JSON.stringify(presets.slice(0, PRESETS_MAX)));
  } catch { /* ignore quota / private mode */ }
}

export function createPreset(
  name: string,
  deviceIds: number[],
  opts: { id?: string; createdAt?: string; existing?: PlanExecutePreset[] } = {},
): PlanExecutePreset {
  const normalizedName = normalizeName(name);
  const ids = normalizeDeviceIds(deviceIds);
  if (!normalizedName) throw new Error('方案名称不能为空');
  if (ids.length === 0) throw new Error('请先选择至少一台样机');
  return {
    id: opts.id ?? `preset-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    name: normalizedName,
    deviceIds: ids,
    createdAt: opts.createdAt ?? new Date().toISOString(),
  };
}

/** Prepend a new preset; drop oldest beyond PRESETS_MAX. */
export function addPreset(
  name: string,
  deviceIds: number[],
  storage: Storage = localStorage,
): PlanExecutePreset {
  const preset = createPreset(name, deviceIds);
  const next = [preset, ...loadPresets(storage)].slice(0, PRESETS_MAX);
  persistPresets(next, storage);
  return preset;
}

export function deletePreset(id: string, storage: Storage = localStorage): PlanExecutePreset[] {
  const next = loadPresets(storage).filter((p) => p.id !== id);
  persistPresets(next, storage);
  return next;
}

/**
 * Apply preset against currently schedulable pool (intersection).
 * Missing count = preset ids not present in the schedulable set.
 */
export function applyPresetIntersection(
  presetDeviceIds: number[],
  schedulableIds: Iterable<number>,
): ApplyPresetResult {
  const schedulable = schedulableIds instanceof Set
    ? schedulableIds
    : new Set(schedulableIds);
  const appliedIds: number[] = [];
  let missingCount = 0;
  for (const id of normalizeDeviceIds(presetDeviceIds)) {
    if (schedulable.has(id)) appliedIds.push(id);
    else missingCount += 1;
  }
  return { appliedIds, missingCount };
}
