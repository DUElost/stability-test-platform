import { Filter, Server } from 'lucide-react';
import { FilterSearchInput } from '@/components/ui/filter-bar';
import { SearchableSelect } from '@/components/ui/searchable-select';
import { FILTER_CHIP, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import type { DeviceLinkStatus, DeviceUiStatus } from '@/utils/api/types';
import type {
  PlanRunDeviceLinkFilter,
  PlanRunDeviceStatusFilter,
} from './PlanRunKpiGrid';

interface Props {
  /** Facet over `job_exec_status` (执行维度)。 */
  byStatus: Record<string, number>;
  /** Facet over `device_link_status` (连接维度)。缺省则不渲染连接 chip 组。 */
  byLinkStatus?: Record<string, number>;
  byHost?: Record<string, number>;
  statusFilter: PlanRunDeviceStatusFilter;
  linkFilter?: PlanRunDeviceLinkFilter;
  hostFilter: string | 'all';
  searchQuery?: string;
  onStatusFilterChange: (s: PlanRunDeviceStatusFilter) => void;
  onLinkFilterChange?: (s: PlanRunDeviceLinkFilter) => void;
  onHostFilterChange: (h: string | 'all') => void;
  onSearchQueryChange?: (q: string) => void;
  statusTestIdPrefix?: string;
  linkTestIdPrefix?: string;
  hostTestIdPrefix?: string;
  /**
   * #2601：host_id → 显示名。缺省为恒等（沿用旧行为）；调用方传统一入口
   * `hostLabel()`，避免这里直接渲染内部 slug。
   */
  hostLabelFor?: (hostId: string) => string;
  /** Whether to show the "执行" label before the status chips. Default true. */
  showStatusLabel?: boolean;
}

const STATUS_DEF: Array<{ key: DeviceUiStatus | 'all'; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'running', label: '运行中' },
  { key: 'finishing', label: '收尾中' },
  { key: 'completed', label: '完成' },
  { key: 'unknown', label: '已断开' },
  { key: 'failed', label: '失败' },
  { key: 'aborted', label: '已中止' },
  { key: 'backoff', label: '退避' },
  { key: 'pending', label: '等待' },
];

const LINK_DEF: Array<{ key: DeviceLinkStatus | 'all'; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'online', label: '在线' },
  { key: 'offline', label: '离线' },
  { key: 'adb_error', label: 'ADB 异常' },
  { key: 'host_offline', label: 'Host 离线' },
  { key: 'unknown', label: '未知' },
];

export default function DeviceFilterBar({
  byStatus,
  byLinkStatus,
  byHost = {},
  statusFilter,
  linkFilter = 'all',
  hostFilter,
  searchQuery = '',
  onStatusFilterChange,
  onLinkFilterChange,
  onHostFilterChange,
  onSearchQueryChange,
  statusTestIdPrefix = 'device-status-filter',
  linkTestIdPrefix = 'device-link-filter',
  hostTestIdPrefix = 'device-host-filter',
  hostLabelFor,
  showStatusLabel = true,
}: Props) {
  const hosts = Object.keys(byHost).sort((a, b) => a.localeCompare(b));
  // Why: byStatus 自带 'all' 键,直接 sum 会把总数算成两倍。
  const total = byStatus.all ?? 0;
  const showLinkChips = !!byLinkStatus && !!onLinkFilterChange;
  const lostOrBackoffCount = (byStatus.unknown ?? 0) + (byStatus.backoff ?? 0);
  const linkAbnormalCount = byLinkStatus
    ? Math.max(0, (byLinkStatus.all ?? total) - (byLinkStatus.online ?? 0))
    : 0;

  return (
    <div className="flex flex-wrap items-center gap-1 border-b bg-card px-3 py-2">
      <Filter className={cn('mr-1 h-3 w-3', TEXT.subtitle)} />
      {showStatusLabel && (
        <span className={cn('mr-1 text-[11px] font-semibold uppercase tracking-wider', TEXT.subtitle)}>
          执行
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
      {statusFilter === 'lost_or_backoff' && (
        <button
          type="button"
          data-testid={`${statusTestIdPrefix}-lost_or_backoff`}
          onClick={() => onStatusFilterChange('all')}
          className={cn('rounded-md px-2 py-0.5 text-xs transition', FILTER_CHIP.active)}
          title="点击清除失联/退避并集筛选"
        >
          失联/退避
          <span className={cn('ml-1', FILTER_CHIP.count)}>{lostOrBackoffCount}</span>
        </button>
      )}
      {showLinkChips && (
        <>
          <span className={FILTER_CHIP.divider} />
          <span className={cn('mr-1 text-[11px] font-semibold uppercase tracking-wider', TEXT.subtitle)}>
            连接
          </span>
          {LINK_DEF.map((d) => (
            <button
              key={d.key}
              type="button"
              data-testid={`${linkTestIdPrefix}-${d.key}`}
              onClick={() => onLinkFilterChange?.(d.key)}
              className={cn(
                'rounded-md px-2 py-0.5 text-xs transition',
                linkFilter === d.key ? FILTER_CHIP.active : FILTER_CHIP.idle,
              )}
            >
              {d.label}
              <span className={cn('ml-1', FILTER_CHIP.count)}>
                {byLinkStatus?.[d.key] ?? 0}
              </span>
            </button>
          ))}
          {linkFilter === 'abnormal' && (
            <button
              type="button"
              data-testid={`${linkTestIdPrefix}-abnormal`}
              onClick={() => onLinkFilterChange?.('all')}
              className={cn('rounded-md px-2 py-0.5 text-xs transition', FILTER_CHIP.active)}
              title="点击清除连接异常并集筛选"
            >
              连接异常
              <span className={cn('ml-1', FILTER_CHIP.count)}>{linkAbnormalCount}</span>
            </button>
          )}
        </>
      )}
      {hosts.length > 0 && (
        <>
          <span className={FILTER_CHIP.divider} />
          <SearchableSelect
            value={hostFilter}
            onChange={(val) => onHostFilterChange(val as string | 'all')}
            options={hosts.map((h) => ({
              value: h,
              label: `${hostLabelFor ? hostLabelFor(h) : h} (${byHost[h] ?? 0})`,
            }))}
            allLabel={`全部主机 (${total})`}
            searchPlaceholder="搜索主机..."
            icon={<Server />}
            ariaLabel="按主机筛选"
            testId={hostTestIdPrefix}
            className="w-44"
          />
        </>
      )}
      {onSearchQueryChange && (
        <>
          <span className={FILTER_CHIP.divider} />
          <FilterSearchInput
            id="plan-run-device-search"
            value={searchQuery}
            onChange={onSearchQueryChange}
            placeholder="搜索设备串号..."
            ariaLabel="按设备串号筛选"
            testId="plan-run-device-search"
            className="w-48"
          />
        </>
      )}
    </div>
  );
}
