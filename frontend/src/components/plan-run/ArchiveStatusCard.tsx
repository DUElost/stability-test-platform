import { Server } from 'lucide-react';
import { PANEL, TEXT } from '@/design-system';
import { InlineError } from '@/components/ui/error-state';
import { cn } from '@/lib/utils';
import type { WatcherAgentOpsMetrics } from '@/utils/api/types';

interface Props {
  opsMetrics: WatcherAgentOpsMetrics | null | undefined;
  scanStatus?: string | null;
  /** 来源 query 的加载/失败态：失败或加载中不再静默消失，显示占位。 */
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void;
}

function pctBar(value: number | null | undefined, label: string) {
  const pct = value != null ? Math.min(100, Math.round(value)) : null;
  const color = pct == null
    ? 'bg-muted'
    : pct >= 90
      ? 'bg-destructive'
      : pct >= 70
        ? 'bg-warning'
        : 'bg-success';
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className={cn('w-28 shrink-0', TEXT.subtitle)}>{label}</span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
        {pct != null ? (
          <div className={cn('h-full rounded-full transition-all', color)} style={{ width: `${pct}%` }} />
        ) : (
          <div className="h-full rounded-full bg-muted w-0" />
        )}
      </div>
      <span className={cn('font-mono w-12 text-right', TEXT.subtitle)}>
        {pct != null ? `${pct}%` : '—'}
      </span>
    </div>
  );
}

export default function ArchiveStatusCard({ opsMetrics, scanStatus, isLoading, isError, onRetry }: Props) {
  if (isError) {
    return (
      <section className={PANEL.root} data-testid="archive-status-card">
        <div className="flex items-center justify-between border-b px-4 py-2">
          <span className={cn('flex items-center gap-1.5 text-sm font-semibold', TEXT.heading)}>
            <Server className={cn('h-4 w-4', TEXT.subtitle)} />
            存储运维概览
          </span>
        </div>
        <div className="p-4">
          <InlineError
            message="存储运维数据加载失败"
            onRetry={onRetry}
          />
        </div>
      </section>
    );
  }

  if (isLoading || !opsMetrics) {
    return (
      <section className={PANEL.root} data-testid="archive-status-card">
        <div className="flex items-center justify-between border-b px-4 py-2">
          <span className={cn('flex items-center gap-1.5 text-sm font-semibold', TEXT.heading)}>
            <Server className={cn('h-4 w-4', TEXT.subtitle)} />
            存储运维概览
          </span>
        </div>
        <p className="px-4 py-4 text-xs text-muted-foreground">暂无数据</p>
      </section>
    );
  }

  return (
    <section className={PANEL.root} data-testid="archive-status-card">
      <div className="flex items-center justify-between border-b px-4 py-2">
        <span className={cn('flex items-center gap-1.5 text-sm font-semibold', TEXT.heading)}>
          <Server className={cn('h-4 w-4', TEXT.subtitle)} />
          存储运维概览
        </span>
        {scanStatus && (
          <span className={cn('text-xs', TEXT.subtitle)} data-testid="scan-status">
            Scan: {scanStatus}
          </span>
        )}
      </div>

      <div className="space-y-2 p-4">
        {pctBar(opsMetrics.local_disk_usage_pct, 'HDD 使用率')}

        <div className="grid grid-cols-3 gap-2 text-[11px] text-center">
          <div className="rounded border px-2 py-1">
            <div className={TEXT.subtitle}>SSD 已清理</div>
            <div className={cn('font-mono font-medium', TEXT.body)} data-testid="pruned-total">
              {opsMetrics.pruned_total}
            </div>
          </div>
          <div className="rounded border px-2 py-1">
            <div className={TEXT.subtitle}>HDD 溢出次数</div>
            <div className={cn('font-mono font-medium', TEXT.body)} data-testid="spill-cycles">
              {opsMetrics.spill_cycles}
            </div>
          </div>
          <div className="rounded border px-2 py-1">
            <div className={TEXT.subtitle}>溢出上送数</div>
            <div className={cn('font-mono font-medium', TEXT.body)} data-testid="spilled-total">
              {opsMetrics.spilled_total}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
