import { useMemo, useRef, useState, useEffect, useCallback } from 'react';
import { Grid3X3, List, Activity, AlertCircle } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import { StatusBadge } from '@/components/ui/status-badge';
import { PANEL, SEGMENTED, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import DeviceFilterBar from './DeviceFilterBar';
import SectionHeader from './SectionHeader';
import type {
  DeviceMatrixItem,
  DeviceUiStatus,
  PlanRunDevicesPayload,
} from '@/utils/api/types';
import { DEVICE_UI_STATUS } from './deviceUiStatus';

interface Props {
  data: PlanRunDevicesPayload | undefined;
  isLoading?: boolean;
  isError?: boolean;
  statusFilter?: DeviceUiStatus | 'all';
  hostFilter?: string | 'all';
  onStatusFilterChange?: (s: DeviceUiStatus | 'all') => void;
  onHostFilterChange?: (h: string | 'all') => void;
  onSelectDevice?: (device: DeviceMatrixItem) => void;
  /** Controlled view mode; falls back to internal state when omitted. */
  viewMode?: 'grid' | 'table';
  onViewModeChange?: (mode: 'grid' | 'table') => void;
}

// ── Grid cell color map ──────────────────────────────────────────────────

// 设备 UI 状态色板(label / cellCls / tone)集中在 ./deviceUiStatus.ts

// ── Table helpers ────────────────────────────────────────────────────────

const DISPATCHED_CLAIM_TIMEOUT_SECONDS = 120;

const BUSY_REASON_LABELS: Record<string, string> = {
  active_lease: '设备租约占用',
  device_offline: '设备离线',
  host_offline: '主机离线',
  adb_excluded: 'ADB 状态排除',
};

function fmtCountdown(seconds: number | null | undefined): string | null {
  if (seconds == null) return null;
  if (seconds <= 0) return '已到期';
  return `${seconds}s`;
}

function fmtRelative(ts: string | null | undefined, now = Date.now()): string {
  if (!ts) return '—';
  const t = new Date(ts).getTime();
  if (Number.isNaN(t)) return '—';
  const diff = (t - now) / 1000;
  if (diff > 0) {
    if (diff < 60) return `${Math.round(diff)}s 后`;
    if (diff < 3600) return `${Math.round(diff / 60)}m 后`;
    return `${Math.round(diff / 3600)}h 后`;
  }
  const past = -diff;
  if (past < 60) return `${Math.round(past)}s 前`;
  if (past < 3600) return `${Math.round(past / 60)}m 前`;
  return `${Math.round(past / 3600)}h 前`;
}

function statusTooltip(d: DeviceMatrixItem, now: number): string | undefined {
  if (d.ui_status === 'unknown') {
    const grace = d.grace_remaining_seconds;
    if (grace != null && grace > 0) {
      const prefix = d.status_reason || 'Job 已断开';
      return `${prefix} — grace 剩余 ${grace}s，超时后自动失败`;
    }
    const reason = (d.status_reason || '').toLowerCase();
    if (reason.includes('lease_expired') || reason.includes('heartbeat')) {
      return `${d.status_reason || 'Job 已断开'} — grace 窗口内可 recovery 恢复，超时后自动失败`;
    }
    return 'Job 已断开（UNKNOWN），grace 窗口内等待 recovery 或 reconciler 自动失败';
  }
  if (d.status_reason) return d.status_reason;
  if (d.ui_status === 'pending') {
    const baseTs = d.created_at ?? d.started_at;
    if (baseTs) {
      const deadline = new Date(baseTs).getTime() + DISPATCHED_CLAIM_TIMEOUT_SECONDS * 1000;
      const remaining = Math.max(0, Math.ceil((deadline - now) / 1000));
      if (remaining > 0) return `等待 Agent 认领；${remaining}s 内未认领将自动失败（120s SLA）`;
      return '等待 Agent 认领；认领 SLA 已到期，recycler 将标记失败';
    }
    return '等待 Agent 认领；超时未认领将自动失败（120s SLA）';
  }
  if (d.ui_status === 'backoff' && d.next_retry_at) {
    return `退避中，${fmtRelative(d.next_retry_at, now)}重试`;
  }
  if (d.ui_status === 'running' && d.last_heartbeat_at) {
    return `最近 patrol 心跳：${fmtRelative(d.last_heartbeat_at, now)}`;
  }
  return undefined;
}

// ── DeviceGrid (minimap view) ────────────────────────────────────────────

function DeviceGrid({
  devices,
  onSelect,
}: {
  devices: DeviceMatrixItem[];
  onSelect?: (d: DeviceMatrixItem) => void;
}) {
  return (
    <div className="p-3">
      <style>{`
        @keyframes dev-stripe {
          from { background-position: 0 0; }
          to   { background-position: 8px 0; }
        }
      `}</style>
      <div
        className="grid gap-1"
        style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(24px, 1fr))' }}
      >
        {devices.map((d) => {
          const label = `${d.device_serial || `Device #${d.device_id}`} — ${DEVICE_UI_STATUS[d.ui_status].label}`;
          return (
            <button
              key={d.job_id}
              type="button"
              data-testid={`minimap-cell-${d.job_id}`}
              onClick={() => onSelect?.(d)}
              aria-label={label}
              title={label}
              className={`aspect-square rounded-sm border border-transparent transition-transform hover:scale-[1.12] hover:ring-2 hover:ring-primary/45 hover:z-10 ${DEVICE_UI_STATUS[d.ui_status].cellCls}`}
            />
          );
        })}
      </div>
    </div>
  );
}

// ── DeviceTable (detailed view) ──────────────────────────────────────────

function DeviceTable({
  devices,
  onSelect,
  highlightJobId,
}: {
  devices: DeviceMatrixItem[];
  onSelect?: (d: DeviceMatrixItem) => void;
  highlightJobId?: number | null;
}) {
  const now = Date.now();
  const rowRefs = useRef<Map<number, HTMLTableRowElement | null>>(new Map());

  useEffect(() => {
    if (highlightJobId != null) {
      const el = rowRefs.current.get(highlightJobId);
      if (el && typeof el.scrollIntoView === 'function') {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }
  }, [highlightJobId]);

  const setRowRef = useCallback(
    (jobId: number) => (el: HTMLTableRowElement | null) => {
      rowRefs.current.set(jobId, el);
    },
    [],
  );

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px]">
        <thead className={cn('bg-muted/50 text-xs font-semibold uppercase tracking-wider', TEXT.subtitle)}>
          <tr>
            <th className="px-3 py-2 text-left">Serial</th>
            <th className="px-2 py-2 text-left">Host</th>
            <th className="px-2 py-2 text-left">状态</th>
            <th className="px-2 py-2 text-left">等待/占用</th>
            <th className="px-2 py-2 text-left">阶段</th>
            <th className="px-2 py-2 text-left">当前步骤</th>
            <th className="px-2 py-2 text-right">巡检周期</th>
            <th className="px-2 py-2 text-right">连击</th>
            <th className="px-2 py-2 text-right">下次重试</th>
            <th className="px-2 py-2 text-right">异常</th>
          </tr>
        </thead>
        <tbody>
          {devices.map((d) => {
            const failureClass =
              d.current_failure_streak >= 3
                ? 'text-destructive font-semibold'
                : d.current_failure_streak >= 1
                ? 'text-warning'
                : 'text-muted-foreground/70';
            const waitLabel =
              d.ui_status === 'unknown' && d.grace_remaining_seconds != null
                ? `grace ${fmtCountdown(d.grace_remaining_seconds)}`
                : d.ui_status === 'pending' && d.pending_claim_remaining_seconds != null
                ? `认领 ${fmtCountdown(d.pending_claim_remaining_seconds)}`
                : d.busy_reason
                ? BUSY_REASON_LABELS[d.busy_reason] ?? d.busy_reason
                : '—';
            const isHighlight = highlightJobId === d.job_id;
            return (
              <tr
                key={d.job_id}
                ref={setRowRef(d.job_id)}
                data-testid={`device-row-${d.job_id}`}
                onClick={() => onSelect?.(d)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onSelect?.(d);
                  }
                }}
                role="button"
                tabIndex={0}
                aria-label={`${d.device_serial || `Device #${d.device_id}`} 详情`}
                className={cn(
                  'cursor-pointer border-t transition-colors hover:bg-primary/5',
                  isHighlight && 'bg-primary/10 ring-1 ring-primary/30',
                )}
              >
                <td className="px-3 py-2 font-mono text-xs">
                  {d.device_serial || `Device #${d.device_id}`}
                </td>
                <td className={cn('px-2 py-2 font-mono text-xs', TEXT.subtitle)}>
                  {d.host_id || '—'}
                </td>
                <td className="px-2 py-2">
                  <span title={statusTooltip(d, now)}>
                    <StatusBadge
                      kind="device-ui"
                      status={d.ui_status}
                      size="sm"
                      spin={d.ui_status === 'running'}
                    />
                  </span>
                </td>
                <td className={cn('px-2 py-2 text-xs', TEXT.subtitle)} data-testid={`device-wait-${d.job_id}`}>
                  {waitLabel}
                </td>
                <td className={cn('px-2 py-2 text-xs uppercase', TEXT.subtitle)}>
                  {d.current_stage}
                </td>
                <td className={cn('px-2 py-2 font-mono text-xs', TEXT.body)}>
                  {d.current_step || '—'}
                </td>
                <td className={cn('px-2 py-2 text-right font-mono text-xs', TEXT.body)}>
                  #{d.patrol_cycle_count}
                  <span className="ml-1 text-[11px] text-muted-foreground/70">
                    ({d.patrol_success_cycle_count}✓ / {d.patrol_failed_cycle_count}✗)
                  </span>
                </td>
                <td className={`px-2 py-2 text-right font-mono text-xs ${failureClass}`}>
                  {d.current_failure_streak > 0
                    ? `× ${d.current_failure_streak}`
                    : '—'}
                </td>
                <td className={cn('px-2 py-2 text-right text-xs', TEXT.subtitle)}>
                  {d.next_retry_at
                    ? fmtRelative(d.next_retry_at, now)
                    : d.manual_action === 'EXIT_REQUESTED'
                    ? '退出待执行'
                    : d.manual_action === 'RETRY_NOW'
                    ? '已请求立即重试'
                    : '—'}
                </td>
                <td className={cn('px-2 py-2 text-right text-xs', TEXT.body)}>
                  {d.log_signal_count > 0 ? (
                    <span className="text-warning">⚠ {d.log_signal_count}</span>
                  ) : (
                    '—'
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── DeviceOverview ───────────────────────────────────────────────────────

export default function DeviceOverview({
  data,
  isLoading = false,
  isError = false,
  statusFilter = 'all',
  hostFilter = 'all',
  onStatusFilterChange,
  onHostFilterChange,
  onSelectDevice,
  viewMode: controlledViewMode,
  onViewModeChange,
}: Props) {
  const [internalViewMode, setInternalViewMode] = useState<'grid' | 'table'>('grid');
  const viewMode = controlledViewMode ?? internalViewMode;
  const setViewMode = useCallback(
    (mode: 'grid' | 'table') => {
      if (onViewModeChange) onViewModeChange(mode);
      else setInternalViewMode(mode);
    },
    [onViewModeChange],
  );

  const total = data?.total ?? 0;
  const devices = data?.devices ?? [];
  const byStatus = data?.by_status ?? { all: 0 };
  const byHost = data?.by_host ?? {};

  const hosts = useMemo(
    () => Object.keys(byHost).sort((a, b) => a.localeCompare(b)),
    [byHost],
  );

  const handleGridSelect = useCallback(
    (d: DeviceMatrixItem) => {
      onSelectDevice?.(d);
    },
    [onSelectDevice],
  );

  const meta = `${total} 设备 · ${hosts.length} Host`;

  const viewToggle = (
    <div className={SEGMENTED.track}>
      <button
        type="button"
        data-testid="device-overview-grid-btn"
        onClick={() => setViewMode('grid')}
        className={cn(
          'inline-flex items-center gap-1 rounded px-2 py-0.5 transition',
          viewMode === 'grid' ? SEGMENTED.itemActive : SEGMENTED.item,
        )}
        title="缩略图视图"
        aria-label="缩略图视图"
      >
        <Grid3X3 className="h-3 w-3" />
      </button>
      <button
        type="button"
        data-testid="device-overview-table-btn"
        onClick={() => setViewMode('table')}
        className={cn(
          'inline-flex items-center gap-1 rounded px-2 py-0.5 transition',
          viewMode === 'table' ? SEGMENTED.itemActive : SEGMENTED.item,
        )}
        title="表格视图"
        aria-label="表格视图"
      >
        <List className="h-3 w-3" />
      </button>
    </div>
  );

  return (
    <section data-testid="device-overview" className="space-y-2">
      <SectionHeader title="设备总览" meta={meta} extra={viewToggle} />

      <div className={PANEL.root}>
        <DeviceFilterBar
          byStatus={byStatus}
          byHost={byHost}
          statusFilter={statusFilter}
          hostFilter={hostFilter}
          onStatusFilterChange={onStatusFilterChange ?? (() => {})}
          onHostFilterChange={onHostFilterChange ?? (() => {})}
        />

        {/* Body */}
        {isError ? (
          <div className="flex flex-col items-center justify-center py-10 text-center">
            <AlertCircle className="mb-2 h-6 w-6 text-destructive/60" />
            <span className="text-xs font-semibold text-destructive">加载失败</span>
            <span className="mt-1 text-[11px] text-destructive/70">请检查网络连接或稍后重试</span>
          </div>
        ) : isLoading && devices.length === 0 ? (
          <div className="space-y-2 p-3">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : devices.length === 0 ? (
          <div className={cn('py-10 text-center text-xs', TEXT.subtitle)}>
            <Activity className="mx-auto mb-2 h-5 w-5 opacity-30" />
            该过滤条件下暂无设备
          </div>
        ) : viewMode === 'grid' ? (
          <DeviceGrid devices={devices} onSelect={handleGridSelect} />
        ) : (
          <DeviceTable
            devices={devices}
            onSelect={onSelectDevice}
          />
        )}
      </div>
    </section>
  );
}
