import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronDown, ChevronRight, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DRAWER, PANEL, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import type { PlanSnapshot, PlanSnapshotStep } from '@/utils/api/types';
import { formatScriptIdentity } from './scriptIdentity';

/**
 * #3350（ADR-0023 D4）：PlanRun 详情页「查看快照」抽屉。
 *
 * 数据源就是 `GET /plan-runs/{id}` 已返回的 `plan_snapshot`（不新增端点、不 lazy
 * fetch——快照是派发前固化的设计，正是排查「当时跑的是哪个脚本版本」的事实源）。
 * 快照缺失（早于快照机制的遗留 PlanRun）走空态，不抛错。
 *
 * 原文第 4 条的 WiFi 分配一节未实现：D5 撤销后没有任何写入方，`ResourceAllocation`
 * 也没有 run 级读取面（只有 pool 管理端点）——按「不新增端点/最小方案」留白，
 * 触发条件见 Agent Note（`docs/notes/feature/2026-09-26-planrun-script-identity-3350.md`）。
 */

interface Props {
  open: boolean;
  onClose: () => void;
  /** `GET /plan-runs/{id}` 已返回的 plan_snapshot（旧 PlanRun 可能为 null） */
  snapshot?: PlanSnapshot | null;
}

/** 旧快照可能缺字段（迁移前只有部分键）——统一按 Partial 读，缺即 '—'。 */
type SnapshotStep = Partial<PlanSnapshotStep>;

const STAGE_ORDER: Record<string, number> = { init: 0, patrol: 1, teardown: 2 };
const STAGE_LABEL: Record<string, string> = {
  init: '初始化',
  patrol: '巡检',
  teardown: '清理',
};

function sortSteps(steps: SnapshotStep[]): SnapshotStep[] {
  return [...steps].sort((a, b) => {
    const sa = STAGE_ORDER[String(a.stage ?? '')] ?? 99;
    const sb = STAGE_ORDER[String(b.stage ?? '')] ?? 99;
    if (sa !== sb) return sa - sb;
    return (a.sort_order ?? 0) - (b.sort_order ?? 0);
  });
}

function jsonBlock(value: unknown): string {
  if (value == null) return '—';
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function StepCard({ step }: { step: SnapshotStep }) {
  const [expanded, setExpanded] = useState(false);
  const identity = formatScriptIdentity(step.script_name, step.script_version);
  const stage = String(step.stage ?? '');
  return (
    <div
      className={cn(PANEL.root, 'p-2.5')}
      data-testid={`snapshot-step-${stage}-${step.step_key ?? ''}`}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        data-testid={`snapshot-step-toggle-${stage}-${step.step_key ?? ''}`}
        className="flex w-full items-center gap-1.5 text-left"
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className={cn('truncate font-mono text-xs font-semibold', TEXT.heading)}>
          {step.step_key || '—'}
        </span>
        <span className={cn('shrink-0 rounded border px-1.5 py-0.5 text-[10px]', TEXT.subtitle)}>
          {STAGE_LABEL[stage] ?? stage ?? '—'}
        </span>
        <span
          className={cn('ml-auto shrink-0 font-mono text-[11px]', TEXT.subtitle)}
          data-testid={`snapshot-step-script-${stage}-${step.step_key ?? ''}`}
        >
          {step.script_name ? (
            /* #3350（ADR-0023 D3）：深链到脚本库定位该版本（ScriptManagementPage 消费 ?name=&version=） */
            <Link
              to={`/script-management?name=${encodeURIComponent(step.script_name)}${
                step.script_version ? `&version=${encodeURIComponent(step.script_version)}` : ''
              }`}
              className="hover:underline"
            >
              {identity}
            </Link>
          ) : (
            '—'
          )}
        </span>
      </button>
      <div className={cn('mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px]', TEXT.subtitle)}>
        <span>timeout {step.timeout_seconds ?? '—'}s</span>
        <span>retry {step.retry ?? 0}</span>
        <span>{step.enabled === false ? '已禁用' : '启用'}</span>
        {step.nfs_path ? <span className="truncate font-mono">{step.nfs_path}</span> : null}
      </div>
      {expanded && (
        <div className="mt-2 space-y-2" data-testid={`snapshot-step-body-${stage}-${step.step_key ?? ''}`}>
          <div>
            <div className={cn('text-[10px] font-semibold', TEXT.subtitle)}>default_params</div>
            <pre className="mt-0.5 overflow-x-auto rounded bg-muted/40 p-1.5 text-[10px] leading-snug">
              {jsonBlock(step.default_params)}
            </pre>
          </div>
          <div>
            <div className={cn('text-[10px] font-semibold', TEXT.subtitle)}>param_schema</div>
            <pre className="mt-0.5 overflow-x-auto rounded bg-muted/40 p-1.5 text-[10px] leading-snug">
              {jsonBlock(step.param_schema)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

export default function PlanSnapshotDrawer({ open, onClose, snapshot }: Props) {
  const steps = useMemo<SnapshotStep[]>(() => {
    const raw = snapshot && typeof snapshot === 'object' ? snapshot.steps : null;
    if (!Array.isArray(raw)) return [];
    return sortSteps(
      raw.filter((s) => Boolean(s) && typeof s === 'object') as SnapshotStep[],
    );
  }, [snapshot]);
  const plan = useMemo<Record<string, unknown>>(() => {
    const raw = snapshot && typeof snapshot === 'object' ? snapshot.plan : null;
    return raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
  }, [snapshot]);

  if (!open) return null;

  return (
    <>
      <div data-testid="plan-snapshot-overlay" onClick={onClose} className={DRAWER.overlay} />
      <aside
        data-testid="plan-snapshot-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="计划快照"
        className={DRAWER.panel}
      >
        <header className="flex items-center justify-between border-b px-4 py-3">
          <div className="min-w-0">
            <p className={cn('truncate text-xs', TEXT.subtitle)}>plan_snapshot</p>
            <h2 className={cn('truncate text-base font-semibold', TEXT.heading)}>计划快照</h2>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
            data-testid="plan-snapshot-close"
            aria-label="关闭计划快照"
          >
            <X className="h-4 w-4" />
          </Button>
        </header>

        <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3 text-sm">
          {steps.length === 0 ? (
            <div
              className={cn('rounded-lg border border-dashed p-4 text-center text-xs', TEXT.subtitle)}
              data-testid="plan-snapshot-empty"
            >
              该 PlanRun 没有可浏览的快照（早于快照机制或快照为空）
            </div>
          ) : (
            <>
              <section className="space-y-1" data-testid="plan-snapshot-plan-meta">
                <div className={cn('text-xs font-semibold', TEXT.heading)}>
                  {String(plan.name ?? '—')}
                </div>
                <div className={cn('flex flex-wrap gap-x-3 text-[11px]', TEXT.subtitle)}>
                  <span>patrol_interval_seconds {String(plan.patrol_interval_seconds ?? '—')}</span>
                  <span>timeout_seconds {String(plan.timeout_seconds ?? '—')}</span>
                  <span className="font-mono">
                    watcher_policy{' '}
                    {plan.watcher_policy && Object.keys(plan.watcher_policy as object).length > 0
                      ? JSON.stringify(plan.watcher_policy)
                      : '—'}
                  </span>
                </div>
              </section>
              <section className="space-y-1.5">
                {steps.map((step) => (
                  <StepCard
                    key={`${step.stage ?? ''}-${step.step_key ?? ''}-${step.sort_order ?? 0}`}
                    step={step}
                  />
                ))}
              </section>
            </>
          )}
        </div>

        <footer className={cn('border-t px-4 py-2 text-[10px]', TEXT.subtitle)}>
          快照由 dispatcher 写入，与当时 Script 表元数据一致；后续 Script 升级不影响此快照。
        </footer>
      </aside>
    </>
  );
}
