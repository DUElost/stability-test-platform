import type { DeviceViewMode } from './types';

export interface PlanExecuteFilterState {
  q: string;
  version: string;
  model: string;
  host: string;
  tags: string[];
  readyOnly: boolean;
  view: DeviceViewMode;
}

export type ActiveFilterChipId =
  | 'q'
  | 'version'
  | 'model'
  | 'host'
  | 'ready'
  | `tag:${string}`;

export interface ActiveFilterChip {
  id: ActiveFilterChipId;
  label: string;
}

const FILTER_KEYS = ['q', 'version', 'model', 'host', 'tags', 'ready'] as const;

export function parseViewParam(value: string | null): DeviceViewMode {
  return value === 'table' ? 'table' : 'matrix';
}

export function parsePlanExecuteFilterParams(params: URLSearchParams): PlanExecuteFilterState {
  const tagsRaw = params.get('tags');
  const tags = tagsRaw
    ? [...new Set(tagsRaw.split(',').map((t) => t.trim()).filter(Boolean))]
    : [];
  return {
    q: params.get('q') ?? '',
    version: params.get('version') || 'all',
    model: params.get('model') || 'all',
    host: params.get('host') || 'all',
    tags,
    readyOnly: params.get('ready') === '1',
    view: parseViewParam(params.get('view')),
  };
}

/** True when any selection-filter query is present (excludes plan/devices/view). */
export function hasFilterQueryParams(params: URLSearchParams): boolean {
  return FILTER_KEYS.some((key) => params.has(key));
}

export function writeFilterParamsToSearch(
  prev: URLSearchParams,
  state: PlanExecuteFilterState,
): URLSearchParams {
  const next = new URLSearchParams(prev);
  const q = state.q.trim();
  if (q) next.set('q', q);
  else next.delete('q');

  if (state.version && state.version !== 'all') next.set('version', state.version);
  else next.delete('version');

  if (state.model && state.model !== 'all') next.set('model', state.model);
  else next.delete('model');

  if (state.host && state.host !== 'all') next.set('host', state.host);
  else next.delete('host');

  if (state.tags.length > 0) next.set('tags', state.tags.join(','));
  else next.delete('tags');

  if (state.readyOnly) next.set('ready', '1');
  else next.delete('ready');

  next.set('view', state.view);
  return next;
}

export function buildActiveFilterChips(
  state: Pick<PlanExecuteFilterState, 'q' | 'version' | 'model' | 'host' | 'tags' | 'readyOnly'>,
  opts?: { hostLabel?: string },
): ActiveFilterChip[] {
  const chips: ActiveFilterChip[] = [];
  const q = state.q.trim();
  if (q) chips.push({ id: 'q', label: `搜索:${q}` });
  if (state.version && state.version !== 'all') {
    chips.push({ id: 'version', label: `版本:${state.version}` });
  }
  if (state.model && state.model !== 'all') {
    chips.push({ id: 'model', label: `型号:${state.model}` });
  }
  if (state.host && state.host !== 'all') {
    chips.push({ id: 'host', label: `节点:${opts?.hostLabel || state.host}` });
  }
  for (const tag of state.tags) {
    chips.push({ id: `tag:${tag}`, label: `标签:${tag}` });
  }
  if (state.readyOnly) chips.push({ id: 'ready', label: '仅就绪' });
  return chips;
}

export function clearActiveFilterChip(
  state: PlanExecuteFilterState,
  chipId: ActiveFilterChipId,
): PlanExecuteFilterState {
  if (chipId === 'q') return { ...state, q: '' };
  if (chipId === 'version') return { ...state, version: 'all' };
  if (chipId === 'model') return { ...state, model: 'all' };
  if (chipId === 'host') return { ...state, host: 'all' };
  if (chipId === 'ready') return { ...state, readyOnly: false };
  if (chipId.startsWith('tag:')) {
    const tag = chipId.slice(4);
    return { ...state, tags: state.tags.filter((t) => t !== tag) };
  }
  return state;
}
