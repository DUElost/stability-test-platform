import type { Plan, PlanRun } from '@/utils/api';

export interface PlanSelectGroups {
  recent: Plan[];
  all: Plan[];
}

function comparePlanUpdatedAtDesc(a: Plan, b: Plan): number {
  const aTs = a.updated_at ? Date.parse(a.updated_at) : 0;
  const bTs = b.updated_at ? Date.parse(b.updated_at) : 0;
  if (bTs !== aTs) return bTs - aTs;
  return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
}

/** 过滤 + updated_at 倒序；「最近执行」取近期 PlanRun 去重后的前 N 个 Plan。 */
export function groupPlansForSelect(
  plans: Plan[],
  recentRuns: Array<Pick<PlanRun, 'plan_id' | 'started_at' | 'id'>>,
  keyword = '',
  recentLimit = 5,
): PlanSelectGroups {
  const q = keyword.trim().toLowerCase();
  const filtered = (q
    ? plans.filter((p) => p.name.toLowerCase().includes(q))
    : [...plans]
  ).sort(comparePlanUpdatedAtDesc);

  const byId = new Map(filtered.map((p) => [p.id, p]));
  const recentIds: number[] = [];
  const seen = new Set<number>();
  const runsNewestFirst = [...recentRuns].sort((a, b) => {
    const aTs = a.started_at ? Date.parse(a.started_at) : 0;
    const bTs = b.started_at ? Date.parse(b.started_at) : 0;
    if (bTs !== aTs) return bTs - aTs;
    return b.id - a.id;
  });
  for (const run of runsNewestFirst) {
    if (seen.has(run.plan_id)) continue;
    if (!byId.has(run.plan_id)) continue;
    seen.add(run.plan_id);
    recentIds.push(run.plan_id);
    if (recentIds.length >= recentLimit) break;
  }

  const recent = recentIds.map((id) => byId.get(id)!);
  const recentSet = new Set(recentIds);
  const all = filtered.filter((p) => !recentSet.has(p.id));
  return { recent, all };
}
