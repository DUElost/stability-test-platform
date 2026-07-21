import { AlertTriangle } from 'lucide-react';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { formatDateTimeShort } from '@/utils/format';
import type { DuplicateMatch } from './planExecuteDuplicate';

interface DuplicateLaunchBannerProps {
  match: DuplicateMatch;
  onOpenRun: (runId: number) => void;
  className?: string;
}

export function DuplicateLaunchBanner({ match, onOpenRun, className }: DuplicateLaunchBannerProps) {
  const overlapHint = match.kind === 'overlap' && match.overlapCount != null
    ? `，重叠约 ${match.overlapCount} 台`
    : '';
  const weakHint = match.kind === 'weak'
    ? '（列表未带回设备集，按时间窗 + 设备数接近降级提示）'
    : '';

  return (
    <div
      role="status"
      data-testid="duplicate-launch-banner"
      className={cn(
        'flex gap-2.5 rounded-lg border border-warning/40 bg-warning/10 px-3.5 py-3 text-sm text-warning',
        className,
      )}
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <div className="min-w-0 space-y-1">
        <div className="font-semibold text-warning">疑似重复发起</div>
        <p className={cn('text-xs leading-5', TEXT.subtitle)}>
          近 30 分钟内已有同 Plan
          {match.kind === 'overlap' ? '、设备集高度重叠' : '、设备数接近'}
          的执行：
          {' '}
          <button
            type="button"
            className="font-medium text-primary underline-offset-2 hover:underline"
            onClick={() => onOpenRun(match.runId)}
            data-testid="duplicate-launch-run-link"
          >
            PlanRun #{match.runId}
          </button>
          {' '}
          （{formatDateTimeShort(match.startedAt)} · {match.status} · {match.deviceCount} 台
          {overlapHint}）
          {weakHint}
          。黄警不阻断，请确认是否仍要发起。
        </p>
      </div>
    </div>
  );
}
