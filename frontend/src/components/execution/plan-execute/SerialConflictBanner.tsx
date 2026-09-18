import { AlertTriangle } from 'lucide-react';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

interface SerialConflictBannerProps {
  /** 序列号为占位/重复值的已选设备 serial 列表（#2649）。 */
  serials: string[];
  className?: string;
}

export function SerialConflictBanner({ serials, className }: SerialConflictBannerProps) {
  if (serials.length === 0) return null;
  const sample = Array.from(new Set(serials)).slice(0, 5).join('、');

  return (
    <div
      role="status"
      data-testid="serial-conflict-banner"
      className={cn(
        'flex gap-2.5 rounded-lg border border-warning/40 bg-warning/10 px-3.5 py-3 text-sm text-warning',
        className,
      )}
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <div className="min-w-0 space-y-1">
        <div className="font-semibold text-warning">序列号冲突设备</div>
        <p className={cn('text-xs leading-5', TEXT.subtitle)}>
          已选设备中 {serials.length} 台的序列号为占位/重复值（如 {sample}
          {serials.length > 5 ? ' 等' : ''}）——同一序列号会被多台主机的 agent
          争抢，归属不稳定。发起后这些设备将被拒绝执行并在结果中标记为
          失败（原因：序列号冲突），其余设备照常运行；建议先修复设备序列号上报。
        </p>
      </div>
    </div>
  );
}
