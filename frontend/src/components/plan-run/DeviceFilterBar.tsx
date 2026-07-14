import { Filter } from 'lucide-react';
import { FILTER_CHIP, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import type { DeviceUiStatus } from '@/utils/api/types';

interface Props {
  byStatus: Record<string, number>;
  byHost?: Record<string, number>;
  statusFilter: DeviceUiStatus | 'all';
  hostFilter: string | 'all';
  onStatusFilterChange: (s: DeviceUiStatus | 'all') => void;
  onHostFilterChange: (h: string | 'all') => void;
  statusTestIdPrefix?: string;
  hostTestIdPrefix?: string;
  /** Whether to show the "状态" label before the status chips. Default true. */
  showStatusLabel?: boolean;
}

const STATUS_DEF: Array<{ key: DeviceUiStatus | 'all'; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'running', label: '运行中' },
  { key: 'completed', label: '完成' },
  { key: 'unknown', label: '已断开' },
  { key: 'failed', label: '失败' },
  { key: 'aborted', label: '已中止' },
  { key: 'backoff', label: '退避' },
  { key: 'pending', label: '等待' },
];

export default function DeviceFilterBar({
  byStatus,
  byHost = {},
  statusFilter,
  hostFilter,
  onStatusFilterChange,
  onHostFilterChange,
  statusTestIdPrefix = 'device-status-filter',
  hostTestIdPrefix = 'device-host-filter',
  showStatusLabel = true,
}: Props) {
  const hosts = Object.keys(byHost).sort((a, b) => a.localeCompare(b));
  const total = Object.values(byStatus).reduce((a, b) => a + b, 0);

  return (
    <div className="flex flex-wrap items-center gap-1 border-b bg-card px-3 py-2">
      <Filter className={cn('mr-1 h-3 w-3', TEXT.subtitle)} />
      {showStatusLabel && (
        <span className={cn('mr-1 text-[11px] font-semibold uppercase tracking-wider', TEXT.subtitle)}>
          状态
        </span>
      )}
      {STATUS_DEF.map((d) => (
        <button
          key={d.key}
          type="button"
          data-testid={`${statusTestIdPrefix}-${d.key}`}
          onClick={() => onStatusFilterChange(d.key)}
          className={cn(
            'rounded-md px-2 py-0.5 text-xs transition',
            statusFilter === d.key ? FILTER_CHIP.active : FILTER_CHIP.idle,
          )}
        >
          {d.label}
          <span className={cn('ml-1', FILTER_CHIP.count)}>
            {byStatus[d.key] ?? 0}
          </span>
        </button>
      ))}
      {hosts.length > 0 && (
        <>
          <span className={FILTER_CHIP.divider} />
          <span className={cn('mr-1 text-[11px] font-semibold uppercase tracking-wider', TEXT.subtitle)}>
            Host
          </span>
          <select
            data-testid={hostTestIdPrefix}
            value={hostFilter}
            onChange={(e) => onHostFilterChange(e.target.value)}
            className="rounded-md border bg-card px-2 py-0.5 text-xs focus:outline-none focus:ring-2 focus:ring-primary/20"
          >
            <option value="all">全部 ({total})</option>
            {hosts.map((h) => (
              <option key={h} value={h}>
                {h} ({byHost[h] ?? 0})
              </option>
            ))}
          </select>
        </>
      )}
    </div>
  );
}
