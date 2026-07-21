import { Button } from '@/components/ui/button';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { Loader2 } from 'lucide-react';
import { EXECUTE_PHASES, phaseIndex, type ExecutePhase } from './types';

export interface ExecuteCommandBarSummary {
  planName?: string | null;
  selectedCount: number;
  hostCount: number;
  versionCount: number;
  versionConsistent: boolean;
  readyCount: number;
  blockedCount: number;
  showDeviceMeta: boolean;
  /** 超节点槽位将排队的选中量（增强 B4） */
  capacityOverflowCount?: number;
}

interface ExecuteCommandBarProps {
  phase: ExecutePhase;
  onPhaseChange: (phase: ExecutePhase) => void;
  summary: ExecuteCommandBarSummary;
  primaryLabel: string;
  primaryDisabled?: boolean;
  primaryLoading?: boolean;
  onPrimary: () => void;
  secondary?: React.ReactNode;
  /** 选机工作台：更紧凑，贴近 mockup command-bar */
  compact?: boolean;
}

function stepChipLabel(phase: ExecutePhase, summary: ExecuteCommandBarSummary, index: number): string {
  if (phase === 'plan') {
    return summary.planName ? `${index + 1} Plan · ${summary.planName}` : `${index + 1} Plan · 未选`;
  }
  if (phase === 'select') {
    return summary.selectedCount > 0
      ? `${index + 1} 选机 · ${summary.selectedCount} 台`
      : `${index + 1} 选机`;
  }
  return `${index + 1} 发起`;
}

/**
 * 顶栏指挥条 — 对齐 mockup `.command-bar`：
 * 左：路径 step chips + 产出摘要；右：次要操作 + 主 CTA。
 */
export function ExecuteCommandBar({
  phase,
  onPhaseChange,
  summary,
  primaryLabel,
  primaryDisabled,
  primaryLoading,
  onPrimary,
  secondary,
  compact = false,
}: ExecuteCommandBarProps) {
  const currentIdx = phaseIndex(phase);

  return (
    <div
      className={cn(
        'shrink-0 rounded-xl border bg-card shadow-sm',
        compact ? 'px-3 py-2.5' : 'px-4 py-3',
      )}
      aria-label="执行指挥条"
      data-testid="execute-command-bar"
      data-compact={compact ? 'true' : 'false'}
    >
      <div className="grid items-center gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
        <div className="min-w-0">
          <nav
            className="flex flex-wrap items-center gap-1.5"
            aria-label="执行配置进度"
          >
            {EXECUTE_PHASES.map((step, index) => {
              const active = step.id === phase;
              const done = index < currentIdx;
              return (
                <div key={step.id} className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => onPhaseChange(step.id)}
                    aria-current={active ? 'step' : undefined}
                    className={cn(
                      'rounded-lg px-2.5 py-1 text-left text-xs transition-colors',
                      active && 'bg-primary/15 font-semibold text-primary',
                      done && !active && 'bg-success/15 font-medium text-success',
                      !active && !done && 'bg-muted text-muted-foreground hover:bg-accent',
                    )}
                  >
                    {stepChipLabel(step.id, summary, index)}
                  </button>
                  {index < EXECUTE_PHASES.length - 1 && (
                    <span className={cn('text-xs', TEXT.subtitle)} aria-hidden>
                      →
                    </span>
                  )}
                </div>
              );
            })}
          </nav>

          <div className={cn('flex flex-wrap gap-1.5', compact ? 'mt-1.5' : 'mt-2')}>
            {!summary.showDeviceMeta && !summary.planName ? (
              <span className={cn('rounded-md bg-muted px-2 py-0.5 text-xs', TEXT.subtitle)}>
                选择 Plan 后显示摘要
              </span>
            ) : null}
            {summary.planName && !summary.showDeviceMeta ? (
              <span className="rounded-md border bg-muted/40 px-2 py-0.5 text-xs">
                Plan · {summary.planName}
              </span>
            ) : null}
            {summary.showDeviceMeta ? (
              <>
                <span className="rounded-md bg-success/15 px-2 py-0.5 text-xs font-medium text-success">
                  已选 {summary.selectedCount} 台 / {summary.hostCount} 节点
                </span>
                <span
                  className={cn(
                    'rounded-md px-2 py-0.5 text-xs font-medium',
                    summary.versionConsistent
                      ? 'bg-success/15 text-success'
                      : 'bg-warning/15 text-warning',
                  )}
                >
                  {summary.versionCount} 版本 · {summary.versionConsistent ? '一致 ✓' : '冲突'}
                </span>
                <span
                  className={cn(
                    'rounded-md px-2 py-0.5 text-xs font-medium',
                    summary.blockedCount === 0 && summary.selectedCount > 0
                      ? 'bg-success/15 text-success'
                      : summary.selectedCount === 0
                        ? 'bg-muted text-muted-foreground'
                        : 'bg-destructive/15 text-destructive',
                  )}
                >
                  预检{' '}
                  {summary.selectedCount === 0
                    ? '—'
                    : summary.blockedCount === 0
                      ? '通过'
                      : `${summary.blockedCount} 阻塞`}
                </span>
                {(summary.capacityOverflowCount ?? 0) > 0 ? (
                  <span className="rounded-md bg-warning/15 px-2 py-0.5 text-xs font-medium text-warning">
                    {summary.capacityOverflowCount} 个节点超选
                  </span>
                ) : null}
              </>
            ) : null}
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2">
          {secondary}
          <Button type="button" onClick={onPrimary} disabled={primaryDisabled || primaryLoading}>
            {primaryLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            {primaryLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
