import { Filter } from 'lucide-react';
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
    <div className="flex flex-wrap items-center gap-1 border-b bg-white px-3 py-2">
      <Filter className="mr-1 h-3 w-3 text-gray-400" />
      {showStatusLabel && (
        <span className="mr-1 text-[11px] font-semibold uppercase tracking-wider text-gray-400">
          状态
        </span>
      )}
      {STATUS_DEF.map((d) => (
        <button
          key={d.key}
          type="button"
          data-testid={`${statusTestIdPrefix}-${d.key}`}
          onClick={() => onStatusFilterChange(d.key)}
          className={`rounded-md px-2 py-0.5 text-xs transition ${
            statusFilter === d.key
              ? 'bg-blue-100 font-semibold text-blue-700'
              : 'text-gray-600 hover:bg-gray-100'
          }`}
        >
          {d.label}
          <span className="ml-1 text-[11px] text-gray-400">
            {byStatus[d.key] ?? 0}
          </span>
        </button>
      ))}
      {hosts.length > 0 && (
        <>
          <span className="mx-2 h-3 w-px bg-gray-200" />
          <span className="mr-1 text-[11px] font-semibold uppercase tracking-wider text-gray-400">
            Host
          </span>
          <select
            data-testid={hostTestIdPrefix}
            value={hostFilter}
            onChange={(e) => onHostFilterChange(e.target.value)}
            className="rounded-md border px-2 py-0.5 text-xs focus:outline-none focus:ring-2 focus:ring-blue-500/20"
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
