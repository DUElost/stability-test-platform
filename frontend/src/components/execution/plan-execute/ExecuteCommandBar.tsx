import { Button } from '@/components/ui/button';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { Check, ChevronRight, Loader2 } from 'lucide-react';
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
}

export function ExecuteCommandBar({
  phase, onPhaseChange, summary, primaryLabel, primaryDisabled, primaryLoading, onPrimary, secondary,
}: ExecuteCommandBarProps) {
  const currentIdx = phaseIndex(phase);
  return (
    <div className="mb-4 rounded-xl border bg-card p-3" aria-label="执行指挥条">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <nav className="flex flex-wrap items-center gap-1" aria-label="执行配置进度">
          {EXECUTE_PHASES.map((step, index) => {
            const active = step.id === phase;
            const done = index < currentIdx;
            return (
              <div key={step.id} className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => onPhaseChange(step.id)}
                  aria-current={active ? 'step' : undefined}
                  className={cn(
                    'rounded-lg border px-3 py-2 text-left transition-colors',
                    active ? 'border-primary bg-primary/10' : done ? 'border-success/40 bg-success/5' : 'border-transparent bg-muted/30 hover:bg-accent',
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span className={cn(
                      'flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-semibold',
                      active ? 'bg-primary text-primary-foreground' : done ? 'bg-success text-success-foreground' : 'bg-muted-foreground/20',
                    )}>
                      {done ? <Check className="h-3 w-3" /> : index + 1}
                    </span>
                    <span className="text-sm font-medium">{step.title}</span>
                  </div>
                  <div className={cn('mt-0.5 pl-7 text-[11px]', TEXT.subtitle)}>{step.description}</div>
                </button>
                {index < EXECUTE_PHASES.length - 1 && (
                  <ChevronRight className="hidden h-4 w-4 text-muted-foreground sm:block" />
                )}
              </div>
            );
          })}
        </nav>
        <div className="flex flex-wrap items-center gap-2">
          {secondary}
          <Button type="button" onClick={onPrimary} disabled={primaryDisabled || primaryLoading}>
            {primaryLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            {primaryLabel}
          </Button>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {summary.planName ? (
          <span className="rounded-full border bg-muted/40 px-2.5 py-0.5 text-xs">Plan · {summary.planName}</span>
        ) : (
          <span className={cn('rounded-full border px-2.5 py-0.5 text-xs', TEXT.subtitle)}>未选 Plan</span>
        )}
        {summary.showDeviceMeta && (
          <>
            <span className="rounded-full border bg-primary/10 px-2.5 py-0.5 text-xs text-primary">
              {summary.selectedCount} 台 / {summary.hostCount} 节点
            </span>
            <span className={cn('rounded-full border px-2.5 py-0.5 text-xs', summary.versionConsistent ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning')}>
              {summary.versionCount} 版本 · {summary.versionConsistent ? '一致 ✓' : '冲突'}
            </span>
            <span className={cn(
              'rounded-full border px-2.5 py-0.5 text-xs',
              summary.blockedCount === 0 && summary.selectedCount > 0 ? 'bg-success/10 text-success' : summary.selectedCount === 0 ? TEXT.subtitle : 'bg-destructive/10 text-destructive',
            )}>
              预检 {summary.readyCount}/{summary.selectedCount || 0}
              {summary.blockedCount > 0 ? ` · ${summary.blockedCount} 阻塞` : ''}
            </span>
          </>
        )}
      </div>
    </div>
  );
}
