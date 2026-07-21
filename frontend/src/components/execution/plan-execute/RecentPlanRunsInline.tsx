import type { PlanRun } from '@/utils/api';
import { StatusBadge } from '@/components/ui/status-badge';
import { Skeleton } from '@/components/ui/skeleton';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { formatDateTimeShort } from '@/utils/format';
import { estimateDeviceCount } from './planExecuteDuplicate';

interface RecentPlanRunsInlineProps {
  runs: PlanRun[];
  loading?: boolean;
  onOpenRun: (runId: number) => void;
  className?: string;
  /** 展示条数，默认 3 */
  limit?: number;
}

export function RecentPlanRunsInline({
  runs,
  loading = false,
  onOpenRun,
  className,
  limit = 3,
}: RecentPlanRunsInlineProps) {
  const visible = runs.slice(0, limit);

  return (
    <div className={cn('space-y-2', className)} data-testid="recent-plan-runs-inline">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-semibold">
          近 {limit} 次执行
          <span className={cn('ml-1 font-medium', TEXT.subtitle)}>（协作参考）</span>
        </div>
      </div>
      {loading ? (
        <div className="space-y-2">
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-11 w-full" />
        </div>
      ) : visible.length === 0 ? (
        <div className={cn('rounded-lg border border-dashed px-3 py-4 text-center text-xs', TEXT.subtitle)}>
          暂无近期执行记录
        </div>
      ) : (
        <div className="space-y-2">
          {visible.map((run) => {
            const deviceCount = estimateDeviceCount(run);
            return (
              <button
                key={run.id}
                type="button"
                data-testid={`recent-plan-run-${run.id}`}
                onClick={() => onOpenRun(run.id)}
                className="flex w-full items-center justify-between gap-2 rounded-lg border px-3 py-2.5 text-left text-xs transition-colors hover:border-primary hover:bg-primary/5"
              >
                <div className="min-w-0">
                  <div className="font-medium">#{run.id}</div>
                  <div className={cn('mt-0.5 truncate', TEXT.subtitle)}>
                    {formatDateTimeShort(run.started_at)}
                    {deviceCount != null ? ` · ${deviceCount} 台` : ''}
                  </div>
                </div>
                <StatusBadge kind="plan-run" status={run.status} size="sm" />
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
