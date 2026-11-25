import type {
  DeviceLinkStatus,
  DeviceUiStatus,
  PlanRunDevicesPayload,
  PlanRunMinimapFacets,
} from '@/utils/api/types';
import { BORDER, KPI_TONE, STAT, SURFACE, type KpiTone } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import SectionHeader from './SectionHeader';

/** B3-3（契约 §5.2）：支持真实并集筛选 `lost_or_backoff`（unknown OR backoff）与 `abnormal`（!= online）。 */
export type PlanRunDeviceStatusFilter = DeviceUiStatus | 'all' | 'lost_or_backoff';
export type PlanRunDeviceLinkFilter = DeviceLinkStatus | 'all' | 'abnormal';

interface Props {
  devices?: PlanRunDevicesPayload;
  /** B3-3：优先消费 Minimap 全局 Facets，不依赖重列表载荷 */
  facets?: PlanRunMinimapFacets;
  currentStage?: string | null;
  patrolCycle?: number | null;
  statusFilter?: PlanRunDeviceStatusFilter;
  linkFilter?: PlanRunDeviceLinkFilter;
  onSelectStatus?: (status: PlanRunDeviceStatusFilter) => void;
  onSelectLink?: (link: PlanRunDeviceLinkFilter) => void;
  onResetFilters?: () => void;
}

function Cell({
  value,
  label,
  tone = 'default',
  testId,
  active = false,
  onClick,
  title,
}: {
  value: number | string;
  label: string;
  tone?: KpiTone;
  testId: string;
  active?: boolean;
  onClick?: () => void;
  title?: string;
}) {
  const cls = KPI_TONE[tone];
  const baseCls = cn(
    'flex w-full flex-col items-center justify-center rounded-lg border py-1.5 px-1.5 shadow-none transition-colors',
    SURFACE.elevated,
    active ? 'border-primary/60 bg-primary/5 ring-1 ring-primary/40' : BORDER.default,
    onClick && 'cursor-pointer hover:border-primary/40 hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
  );

  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        aria-pressed={active}
        title={title}
        className={baseCls}
        data-testid={testId}
      >
        <span className={cn(STAT.value, 'text-xl leading-tight', cls.value)}>{value}</span>
        <span className={cn('mt-0.5 text-[11px]', STAT.label, cls.label)}>{label}</span>
      </button>
    );
  }

  return (
    <div className={baseCls} data-testid={testId}>
      <span className={cn(STAT.value, 'text-xl leading-tight', cls.value)}>{value}</span>
      <span className={cn('mt-0.5 text-[11px]', STAT.label, cls.label)}>{label}</span>
    </div>
  );
}

export default function PlanRunKpiGrid({
  devices,
  facets,
  currentStage,
  patrolCycle,
  statusFilter = 'all',
  linkFilter = 'all',
  onSelectStatus,
  onSelectLink,
  onResetFilters,
}: Props) {
  const byStatus = facets?.by_status ?? devices?.by_status ?? {};
  const byLinkStatus = facets?.by_link_status ?? devices?.by_link_status;
  const total = facets?.total ?? devices?.total ?? 0;
  const running = byStatus.running ?? 0;
  const completed = byStatus.completed ?? 0;
  const failed = byStatus.failed ?? 0;
  const unknown = byStatus.unknown ?? 0;
  const backoff = byStatus.backoff ?? 0;
  // 执行维度:Job 失联(lease/心跳超时)+ 退避。by_status 现在只投影 Job 状态,
  // 不再掺设备连接 —— 设备断连要看下面的 linkAbnormal。
  const disconnectedAndBackoff = unknown + backoff;
  const disconnectedTone: KpiTone =
    unknown > 0 ? 'info' : disconnectedAndBackoff > 0 ? 'warning' : 'default';

  // 连接维度:非 online 的一律算异常。用「总数 - online」而非枚举各异常态,
  // 这样后端将来新增 link 状态时前端不会漏计。
  // 老后端不返回 by_link_status:那时 by_status 还是基于 ui_status,
  // 其 unknown 恰好表示断连,拿它兜底。
  const linkAbnormal = byLinkStatus
    ? Math.max(0, (byLinkStatus.all ?? total) - (byLinkStatus.online ?? 0))
    : unknown;
  const linkTone: KpiTone = linkAbnormal > 0 ? 'warning' : 'default';

  const stageLabel =
    currentStage === 'init'
      ? '初始化'
      : currentStage === 'patrol'
        ? '巡检'
        : currentStage === 'teardown'
          ? '清理'
          : '—';

  const toggleStatus = onSelectStatus
    ? (target: PlanRunDeviceStatusFilter) =>
        onSelectStatus(statusFilter === target ? 'all' : target)
    : undefined;

  const isDisconnectedActive =
    statusFilter === 'lost_or_backoff' || statusFilter === 'unknown' || statusFilter === 'backoff';
  const isLinkAbnormalActive =
    linkFilter === 'abnormal' || (linkFilter !== 'all' && linkFilter !== 'online');

  return (
    <div className="space-y-2">
      <SectionHeader title="关键指标" />
      <div className="grid grid-cols-2 gap-1.5">
        <Cell
          value={total}
          label="设备总数"
          testId="kpi-total"
          active={Boolean(onResetFilters) && statusFilter === 'all' && linkFilter === 'all'}
          onClick={onResetFilters}
          title={onResetFilters ? '点击查看全部设备' : undefined}
        />
        <Cell
          value={stageLabel}
          label={patrolCycle != null ? `周期 #${patrolCycle}` : '当前阶段'}
          testId="kpi-stage"
        />
        <Cell
          value={running}
          label="运行中"
          tone="warning"
          testId="kpi-running"
          active={statusFilter === 'running'}
          onClick={toggleStatus ? () => toggleStatus('running') : undefined}
          title={toggleStatus ? '点击筛选运行中设备' : undefined}
        />
        <Cell
          value={completed}
          label="已完成"
          tone="success"
          testId="kpi-completed"
          active={statusFilter === 'completed'}
          onClick={toggleStatus ? () => toggleStatus('completed') : undefined}
          title={toggleStatus ? '点击筛选已完成设备' : undefined}
        />
        <Cell
          value={failed}
          label="失败"
          tone={failed > 0 ? 'destructive' : 'default'}
          testId="kpi-failed"
          active={statusFilter === 'failed'}
          onClick={toggleStatus ? () => toggleStatus('failed') : undefined}
          title={toggleStatus ? '点击筛选失败设备' : undefined}
        />
        <Cell
          value={disconnectedAndBackoff}
          label="Job 失联/退避"
          tone={disconnectedTone}
          testId="kpi-disconnected-backoff"
          active={isDisconnectedActive}
          onClick={
            onSelectStatus
              ? () => onSelectStatus(isDisconnectedActive ? 'all' : 'lost_or_backoff')
              : undefined
          }
          title={onSelectStatus ? '点击筛选全部失联或退避设备（unknown + backoff 并集）' : undefined}
        />
        {/* 连接维度与上面的执行维度正交,同一台设备可能同时计入两处,
            所以横跨整行单独呈现,避免读成「又一个执行状态」。 */}
        <div className="col-span-2">
          <Cell
            value={linkAbnormal}
            label="设备连接异常"
            tone={linkTone}
            testId="kpi-link-abnormal"
            active={isLinkAbnormalActive}
            onClick={
              onSelectLink
                ? () => onSelectLink(isLinkAbnormalActive ? 'all' : 'abnormal')
                : undefined
            }
            title={onSelectLink ? '点击筛选全部连接异常设备（非 online 并集）' : undefined}
          />
        </div>
      </div>
    </div>
  );
}
