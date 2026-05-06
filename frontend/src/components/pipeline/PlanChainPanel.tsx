import { useMemo } from 'react';
import type { Plan } from '@/utils/api';
import { ArrowDown, Plus } from 'lucide-react';

export interface PlanChainNode {
  id: number | null;
  name: string;
  initCount: number;
  patrolCount: number;
  teardownCount: number;
  isCurrent: boolean;
  isDraftCurrent?: boolean;
}

interface PlanChainPanelProps {
  plans: Plan[];
  currentPlanId: number | null;
  draftStepCounts: { init: number; patrol: number; teardown: number } | null;
  draftPlanName: string;
  onSelectPlan: (planId: number) => void;
  onAppendPlan: () => void;
}

function countByStage(plan: Plan) {
  const counts = { init: 0, patrol: 0, teardown: 0 };
  for (const step of plan.steps || []) {
    if (step.stage === 'init') counts.init += 1;
    else if (step.stage === 'patrol') counts.patrol += 1;
    else if (step.stage === 'teardown') counts.teardown += 1;
  }
  return counts;
}

function buildChainNodes(
  plans: Plan[],
  currentPlanId: number | null,
  draftStepCounts: PlanChainPanelProps['draftStepCounts'],
  draftPlanName: string,
): PlanChainNode[] {
  if (currentPlanId == null) {
    return [
      {
        id: null,
        name: draftPlanName || '新建 Plan',
        initCount: draftStepCounts?.init ?? 0,
        patrolCount: draftStepCounts?.patrol ?? 0,
        teardownCount: draftStepCounts?.teardown ?? 0,
        isCurrent: true,
        isDraftCurrent: true,
      },
    ];
  }

  const byId = new Map<number, Plan>(plans.map(p => [p.id, p]));
  const inboundOf = new Map<number, number>();
  for (const p of plans) {
    if (p.next_plan_id != null) inboundOf.set(p.next_plan_id, p.id);
  }

  let head = byId.get(currentPlanId);
  if (!head) {
    return [
      {
        id: currentPlanId,
        name: '加载中…',
        initCount: 0,
        patrolCount: 0,
        teardownCount: 0,
        isCurrent: true,
      },
    ];
  }

  const visited = new Set<number>();
  while (true) {
    const inboundId = inboundOf.get(head.id);
    if (inboundId == null) break;
    if (visited.has(inboundId)) break;
    const parent = byId.get(inboundId);
    if (!parent) break;
    visited.add(inboundId);
    head = parent;
  }

  const chain: Plan[] = [];
  const seen = new Set<number>();
  let cursor: Plan | undefined = head;
  while (cursor && !seen.has(cursor.id)) {
    seen.add(cursor.id);
    chain.push(cursor);
    cursor = cursor.next_plan_id != null ? byId.get(cursor.next_plan_id) : undefined;
  }

  return chain.map(p => {
    const isCurrent = p.id === currentPlanId;
    const counts = isCurrent && draftStepCounts ? draftStepCounts : countByStage(p);
    const displayName = isCurrent && draftPlanName.trim() ? draftPlanName.trim() : p.name;
    return {
      id: p.id,
      name: displayName,
      initCount: counts.init,
      patrolCount: counts.patrol,
      teardownCount: counts.teardown,
      isCurrent,
    };
  });
}

export default function PlanChainPanel({
  plans,
  currentPlanId,
  draftStepCounts,
  draftPlanName,
  onSelectPlan,
  onAppendPlan,
}: PlanChainPanelProps) {
  const chain = useMemo(
    () => buildChainNodes(plans, currentPlanId, draftStepCounts, draftPlanName),
    [plans, currentPlanId, draftStepCounts, draftPlanName],
  );

  const isDraft = currentPlanId == null;

  return (
    <aside className="flex flex-col h-full bg-white border-r border-slate-200">
      <header className="flex items-center justify-between gap-3 px-4 py-3 border-b border-slate-200 bg-[#fbfdff]">
        <span className="text-sm font-bold text-slate-800">执行链</span>
        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-bold bg-violet-50 border border-violet-200 text-violet-700">
          {chain.length} {chain.length === 1 ? 'Plan' : 'Plans'}
        </span>
      </header>

      <div className="flex-1 overflow-y-auto p-1.5">
        {chain.map((node, idx) => (
          <div key={node.id ?? `draft-${idx}`}>
            <button
              type="button"
              onClick={() => {
                if (node.id != null && !node.isCurrent) onSelectPlan(node.id);
              }}
              disabled={node.id == null || node.isCurrent}
              className={[
                'w-full text-left px-2.5 py-2 rounded-md grid gap-1 border transition',
                node.isCurrent
                  ? 'bg-cyan-50 border-cyan-200 shadow-[inset_3px_0_0_#0e7490]'
                  : 'border-transparent hover:bg-slate-50',
                node.id == null && !node.isCurrent ? 'cursor-not-allowed opacity-60' : '',
              ].join(' ')}
            >
              <div className="text-[13px] font-bold text-slate-800 truncate">
                {node.name}
                {node.isDraftCurrent && (
                  <span className="ml-1.5 text-[10px] font-semibold text-amber-700">草稿</span>
                )}
              </div>
              <div className="flex flex-wrap gap-1">
                <span className="inline-flex items-center px-1.5 py-px rounded-full text-[11px] font-bold bg-slate-100 text-slate-600 border border-slate-200">
                  Init {node.initCount}
                </span>
                {node.patrolCount > 0 && (
                  <span className="inline-flex items-center px-1.5 py-px rounded-full text-[11px] font-bold bg-emerald-50 text-emerald-700 border border-emerald-200">
                    Patrol {node.patrolCount}
                  </span>
                )}
                {node.teardownCount > 0 && (
                  <span className="inline-flex items-center px-1.5 py-px rounded-full text-[11px] font-bold bg-amber-50 text-amber-700 border border-amber-200">
                    TDown {node.teardownCount}
                  </span>
                )}
              </div>
            </button>

            {idx < chain.length - 1 && (
              <div className="px-3 py-1 flex items-center gap-1.5 text-[10px] font-bold text-violet-600">
                <span className="block w-px h-4 bg-[repeating-linear-gradient(to_bottom,#d8b4fe_0,#d8b4fe_4px,transparent_4px,transparent_8px)]" />
                <span className="inline-flex items-center gap-1">
                  执行后自动触发 <ArrowDown className="w-3 h-3" />
                </span>
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="border-t border-slate-200 bg-[#fbfdff]">
        <button
          type="button"
          onClick={onAppendPlan}
          disabled={isDraft}
          title={isDraft ? '保存当前草稿后再追加链尾' : '在当前链末尾追加一个新 Plan'}
          className="m-1.5 w-[calc(100%-12px)] flex items-center justify-center gap-1.5 px-2.5 py-2 rounded-md text-xs font-bold text-slate-500 border border-dashed border-slate-300 hover:border-cyan-500 hover:text-cyan-700 hover:bg-cyan-50/40 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:border-slate-300 disabled:hover:text-slate-500 transition"
        >
          <Plus className="w-3.5 h-3.5" /> 追加 Plan 到链末尾
        </button>
        <p className="px-3.5 pb-2.5 text-[10px] leading-relaxed text-slate-400">
          每个 Plan 是一个完整的测试计划。
          <br />
          链式执行：前一个 Plan 完成后自动触发下一个。
        </p>
      </div>
    </aside>
  );
}
