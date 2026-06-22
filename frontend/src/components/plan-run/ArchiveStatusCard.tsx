import { Server } from 'lucide-react';
import type { WatcherAgentOpsMetrics } from '@/utils/api/types';

interface Props {
  opsMetrics: WatcherAgentOpsMetrics | null | undefined;
  scanStatus?: string | null;
}

function pctBar(value: number | null | undefined, label: string) {
  const pct = value != null ? Math.min(100, Math.round(value)) : null;
  const color = pct == null ? 'bg-gray-200' : pct >= 90 ? 'bg-red-500' : pct >= 70 ? 'bg-yellow-500' : 'bg-green-500';
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className="w-28 shrink-0 text-gray-500">{label}</span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-100">
        {pct != null ? (
          <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${pct}%` }} />
        ) : (
          <div className="h-full rounded-full bg-gray-200 w-0" />
        )}
      </div>
      <span className="font-mono text-gray-600 w-12 text-right">
        {pct != null ? `${pct}%` : '—'}
      </span>
    </div>
  );
}

export default function ArchiveStatusCard({ opsMetrics, scanStatus }: Props) {
  if (!opsMetrics) {
    return null;
  }

  return (
    <section
      className="rounded-xl border border-gray-200 bg-white"
      data-testid="archive-status-card"
    >
      <div className="flex items-center justify-between border-b px-4 py-2">
        <span className="flex items-center gap-1.5 text-sm font-semibold text-gray-800">
          <Server className="h-4 w-4 text-gray-500" />
          存储运维概览
        </span>
        {scanStatus && (
          <span className="text-xs text-gray-400" data-testid="scan-status">
            Scan: {scanStatus}
          </span>
        )}
      </div>

      <div className="space-y-2 p-4">
        {pctBar(opsMetrics.local_disk_usage_pct, 'HDD 使用率')}

        <div className="grid grid-cols-3 gap-2 text-[11px] text-center">
          <div className="rounded border border-gray-100 px-2 py-1">
            <div className="text-gray-400">SSD 已清理</div>
            <div className="font-mono font-medium text-gray-700" data-testid="pruned-total">
              {opsMetrics.pruned_total}
            </div>
          </div>
          <div className="rounded border border-gray-100 px-2 py-1">
            <div className="text-gray-400">HDD 溢出次数</div>
            <div className="font-mono font-medium text-gray-700" data-testid="spill-cycles">
              {opsMetrics.spill_cycles}
            </div>
          </div>
          <div className="rounded border border-gray-100 px-2 py-1">
            <div className="text-gray-400">溢出上送数</div>
            <div className="font-mono font-medium text-gray-700" data-testid="spilled-total">
              {opsMetrics.spilled_total}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
