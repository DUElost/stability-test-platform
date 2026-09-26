import { useMemo, useRef, useState, useEffect, useCallback } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { Grid3X3, List, Activity, AlertCircle } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { StatusBadge } from '@/components/ui/status-badge';
import { PANEL, SEGMENTED, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import DeviceFilterBar from './DeviceFilterBar';
import SectionHeader from './SectionHeader';
import { formatScriptIdentity } from './scriptIdentity';
import type {
  DeviceLinkStatus,
  DeviceMatrixItem,
  DeviceUiStatus,
  PlanRunDevicesPayload,
} from '@/utils/api/types';
import { DEVICE_UI_STATUS } from './deviceUiStatus';
import { DEVICE_LINK_STATUS } from './deviceLinkStatus';
import { useQuery } from '@tanstack/react-query';
import { fetchAllHosts } from '@/utils/api';
import { hostKeys } from '@/utils/api/queryKeys';
import { hostLabel } from '@/utils/hostDisplay';
import {
  DEVICE_TABLE_OVERSCAN,
  DEVICE_TABLE_ROW_PX,
  DEVICE_TABLE_VIEWPORT_MAX_PX,
  deviceTableSpacers,
  shouldVirtualizeDeviceTable,
} from './deviceTableVirtual';

interface Props {
  data: PlanRunDevicesPayload | undefined;
  isLoading?: boolean;
  isError?: boolean;
  statusFilter?: DeviceUiStatus | 'all';
  linkFilter?: DeviceLinkStatus | 'all';
  hostFilter?: string | 'all';
  onStatusFilterChange?: (s: DeviceUiStatus | 'all') => void;
  onLinkFilterChange?: (s: DeviceLinkStatus | 'all') => void;
  onHostFilterChange?: (h: string | 'all') => void;
  onSelectDevice?: (device: DeviceMatrixItem) => void;
  /** Controlled view mode; falls back to internal state when omitted. */
  viewMode?: 'grid' | 'table';
  onViewModeChange?: (mode: 'grid' | 'table') => void;
}

// ── Grid cell color map ──────────────────────────────────────────────────

// 设备 UI 状态色板(label / cellCls / tone)集中在 ./deviceUiStatus.ts

// ── Table helpers ────────────────────────────────────────────────────────

const LEGACY_DISPATCHED_CLAIM_TIMEOUT_SECONDS = 120;

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

/**
 * 连接维度的提示语。作为**前缀**拼到执行维度提示之前，而不是早退取代它 ——
 * 断连设备恰恰是最需要看到 grace 倒计时 / 认领 SLA 的那一批。
 */
function linkTooltipPrefix(d: DeviceMatrixItem): string | undefined {
  const link = d.device_link_status;
  if (!link || link === 'online') return undefined;
  return DEVICE_LINK_STATUS[link]?.hint ?? '设备不可达';
}

function execTooltip(d: DeviceMatrixItem, now: number): string | undefined {
  const exec = d.job_exec_status ?? d.ui_status;
  if (exec === 'unknown') {
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
  if (exec === 'pending') {
    if (d.pending_claim_remaining_seconds != null) {
      return d.pending_claim_remaining_seconds > 0
        ? `等待 Agent 认领；剩余 ${d.pending_claim_remaining_seconds}s`
        : '等待 Agent 认领；认领 SLA 已到期，recycler 将标记失败';
    }
    if (d.pending_claim_deadline_at) {
      const deadline = new Date(d.pending_claim_deadline_at).getTime();
      if (!Number.isNaN(deadline)) {
        const remaining = Math.max(0, Math.ceil((deadline - now) / 1000));
        return remaining > 0
          ? `等待 Agent 认领；剩余 ${remaining}s`
          : '等待 Agent 认领；认领 SLA 已到期，recycler 将标记失败';
      }
    }
    // Compatibility with legacy servers that do not expose an SLA projection.
    const baseTs = d.created_at ?? d.started_at;
    if (baseTs) {
      const deadline =
        new Date(baseTs).getTime() + LEGACY_DISPATCHED_CLAIM_TIMEOUT_SECONDS * 1000;
      const remaining = Math.max(0, Math.ceil((deadline - now) / 1000));
      if (remaining > 0) return `等待 Agent 认领；${remaining}s 内未认领将自动失败（120s SLA）`;
      return '等待 Agent 认领；认领 SLA 已到期，recycler 将标记失败';
    }
    return '等待 Agent 认领；超时未认领将自动失败（120s SLA）';
  }
  if (exec === 'backoff' && d.next_retry_at) {
    return `退避中，${fmtRelative(d.next_retry_at, now)}重试`;
  }
  if (exec === 'running' && d.last_heartbeat_at) {
    return `最近 patrol 心跳：${fmtRelative(d.last_heartbeat_at, now)}`;
  }
  return undefined;
}

function statusTooltip(d: DeviceMatrixItem, now: number): string | undefined {
  const link = linkTooltipPrefix(d);
  const exec = execTooltip(d, now);
  if (link && exec) return `${link} — ${exec}`;
  return link ?? exec;
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
          // 方块着色跟随「执行」维度,与筛选 chip / 表格执行列同源;
          // 连接维度另行拼进 label,避免两处口径打架。
          const exec = d.job_exec_status ?? d.ui_status;
          const link = d.device_link_status;
          const linkSuffix =
            link && link !== 'online' ? ` · ${DEVICE_LINK_STATUS[link].label}` : '';
          const label =
            `${d.device_serial || `Device #${d.device_id}`} — ` +
            `${DEVICE_UI_STATUS[exec].label}${linkSuffix}`;
          return (
            <button
              key={d.job_id}
              type="button"
              data-testid={`minimap-cell-${d.job_id}`}
              onClick={() => onSelect?.(d)}
              aria-label={label}
              title={label}
              className={`aspect-square rounded-sm border border-transparent transition-transform hover:scale-[1.12] hover:ring-2 hover:ring-primary/45 hover:z-10 ${DEVICE_UI_STATUS[exec].cellCls}`}
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
  hostMap,
}: {
  devices: DeviceMatrixItem[];
  onSelect?: (d: DeviceMatrixItem) => void;
  highlightJobId?: number | null;
  /** #2601：host_id → Host，仅用于显示名解析（查不到回落 host_id，与旧行为一致） */
  hostMap: Map<string, { ip?: string | null; name?: string | null }>;
}) {
  // 渲染期时间戳仅用于卡死高亮派生，无副作用（#260 待统一 tick 状态）。
  // 注：原先这里挂着 `eslint-disable react-hooks/purity`；虚拟化接线落地后该规则
  // 不再在此报告（eslint 判定为 unused directive），故摘掉指令本身——留着它反而
  // 会让 `eslint --max-warnings 0` 变红。
  const now = Date.now();
  const rowRefs = useRef<Map<number, HTMLTableRowElement | null>>(new Map());
  const scrollRef = useRef<HTMLDivElement | null>(null);

  /**
   * #83：表格视图行虚拟化。510 台实测 14,056 DOM 节点 / 511 个 `<tr>` 全量物化
   * （minimap 态同一批数据只要 603 节点），外推 1000 台 ≈2.75 万节点。
   * **只有表格走虚拟化**——minimap 每设备 1 节点，本来就不该分页（#83 的约束）。
   */
  const virtualize = shouldVirtualizeDeviceTable(devices.length);
  const rowVirtualizer = useVirtualizer({
    count: devices.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => DEVICE_TABLE_ROW_PX,
    overscan: DEVICE_TABLE_OVERSCAN,
    enabled: virtualize,
  });
  const visible = virtualize ? rowVirtualizer.getVirtualItems() : [];
  const { padTopPx, padBottomPx } = virtualize
    ? deviceTableSpacers(visible, rowVirtualizer.getTotalSize())
    : { padTopPx: 0, padBottomPx: 0 }; // 静态路径不挂滚动容器，也就不需要垫片
  const windowRows = virtualize
    ? visible.map((row) => devices[row.index]).filter(Boolean)
    : devices;

  useEffect(() => {
    if (highlightJobId == null) return;
    if (virtualize) {
      // 虚拟化后目标行可能根本没挂载，scrollIntoView 无从下手——按索引滚动，
      // 让虚拟窗口自己把它换进来（与 DeviceMatrix 的 highlight 处理同一手法）。
      const index = devices.findIndex((d) => d.job_id === highlightJobId);
      if (index >= 0) rowVirtualizer.scrollToIndex(index, { align: 'center' });
      return;
    }
    const el = rowRefs.current.get(highlightJobId);
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [highlightJobId, virtualize, devices, rowVirtualizer]);

  const setRowRef = useCallback(
    (jobId: number) => (el: HTMLTableRowElement | null) => {
      rowRefs.current.set(jobId, el);
    },
    [],
  );

  const table = (
    <Table
      className="text-[12px]"
      // #83：虚拟层里滚动视口是外层 device-table-scroll，Table 原语自带的
      // overflow-auto 包裹层会变成「不滚动的第二 scrollport」——sticky 的最近
      // 滚动祖先落在它身上，表头钉不住。虚拟化时把内层降为 overflow-visible，
      // 让 sticky 绑到真正滚动的外层（tailwind-merge 同组去重，后者胜出）。
      containerClassName={cn(virtualize && 'overflow-visible')}
    >
        <TableHeader
          className={cn(
            'text-xs font-semibold uppercase tracking-wider',
            TEXT.subtitle,
            // 虚拟化后表格在自己的高度里滚（#83）：表头必须跟着钉住，
            // 否则滚两屏就认不出哪列是哪列——静态守卫锁这条 class。
            virtualize && 'sticky top-0 z-10',
          )}
        >
          {/* #2850：sticky 表头必须近乎不透明——行要从它下面滚过去，/50 会透印。
              口径出处：DeviceTablePanel.tsx:80-81「勿当底色漂移改成 /50」。 */}
          <TableRow className="bg-muted/95 hover:bg-muted/95">
            <TableHead className="h-auto px-3 py-2 text-left">Serial</TableHead>
            <TableHead className="h-auto px-2 py-2 text-left">Host</TableHead>
            <TableHead className="h-auto px-2 py-2 text-left">连接</TableHead>
            <TableHead className="h-auto px-2 py-2 text-left">执行</TableHead>
            <TableHead className="h-auto px-2 py-2 text-left">等待/占用</TableHead>
            <TableHead className="h-auto px-2 py-2 text-left">阶段</TableHead>
            <TableHead className="h-auto px-2 py-2 text-left">当前步骤</TableHead>
            <TableHead className="h-auto px-2 py-2 text-right">巡检周期</TableHead>
            <TableHead className="h-auto px-2 py-2 text-right">连击</TableHead>
            <TableHead className="h-auto px-2 py-2 text-right">下次重试</TableHead>
            <TableHead className="h-auto px-2 py-2 text-right">异常</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {/* 未渲染的行用两段空白补回（滚动条长度与真实总高一致；夹到 ≥0，见 deviceTableVirtual） */}
          {padTopPx > 0 ? <tr aria-hidden="true" style={{ height: `${padTopPx}px` }} /> : null}
          {windowRows.map((d) => {
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
              <TableRow
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
                <TableCell className="px-3 py-2 font-mono text-xs">
                  {d.device_serial || `Device #${d.device_id}`}
                </TableCell>
                <TableCell className={cn('px-2 py-2 font-mono text-xs', TEXT.subtitle)}>
                  {hostLabel(hostMap.get(String(d.host_id ?? '')), d.host_id, '—')}
                </TableCell>
                <TableCell className="px-2 py-2">
                  <span title={statusTooltip(d, now)}>
                    <StatusBadge
                      kind="device-link"
                      status={d.device_link_status ?? 'unknown'}
                      size="sm"
                    />
                  </span>
                </TableCell>
                <TableCell className="px-2 py-2">
                  <span title={statusTooltip(d, now)}>
                    <StatusBadge
                      kind="device-ui"
                      status={d.job_exec_status ?? d.ui_status}
                      size="sm"
                      spin={(d.job_exec_status ?? d.ui_status) === 'running'}
                    />
                  </span>
                </TableCell>
                <TableCell className={cn('px-2 py-2 text-xs', TEXT.subtitle)} data-testid={`device-wait-${d.job_id}`}>
                  {waitLabel}
                </TableCell>
                <TableCell className={cn('px-2 py-2 text-xs uppercase', TEXT.subtitle)}>
                  {d.current_stage}
                </TableCell>
                <TableCell className={cn('px-2 py-2 font-mono text-xs', TEXT.body)}>
                  <div>{d.current_step || '—'}</div>
                  {/* #3350（ADR-0023 D3）：current_step 的脚本身份（快照派生）；
                      缩略图视图不加这一行（保持紧凑） */}
                  {formatScriptIdentity(d.current_script_name, d.current_script_version) && (
                    <div
                      className={cn('text-[10px]', TEXT.subtitle)}
                      data-testid={`device-script-${d.job_id}`}
                    >
                      {formatScriptIdentity(d.current_script_name, d.current_script_version)}
                    </div>
                  )}
                </TableCell>
                <TableCell className={cn('px-2 py-2 text-right font-mono text-xs', TEXT.body)}>
                  #{d.patrol_cycle_count}
                  <span className="ml-1 text-[11px] text-muted-foreground/70">
                    ({d.patrol_success_cycle_count}✓ / {d.patrol_failed_cycle_count}✗)
                  </span>
                </TableCell>
                <TableCell className={`px-2 py-2 text-right font-mono text-xs ${failureClass}`}>
                  {d.current_failure_streak > 0
                    ? `× ${d.current_failure_streak}`
                    : '—'}
                </TableCell>
                <TableCell className={cn('px-2 py-2 text-right text-xs', TEXT.subtitle)}>
                  {d.next_retry_at
                    ? fmtRelative(d.next_retry_at, now)
                    : d.manual_action === 'EXIT_REQUESTED'
                    ? '退出待执行'
                    : d.manual_action === 'RETRY_NOW'
                    ? '已请求立即重试'
                    : '—'}
                </TableCell>
                <TableCell className={cn('px-2 py-2 text-right text-xs', TEXT.body)}>
                  {d.log_signal_count > 0 ? (
                    <span className="text-warning">⚠ {d.log_signal_count}</span>
                  ) : (
                    '—'
                  )}
                </TableCell>
              </TableRow>
            );
          })}
          {padBottomPx > 0 ? <tr aria-hidden="true" style={{ height: `${padBottomPx}px` }} /> : null}
        </TableBody>
      </Table>
  );

  if (!virtualize) return table;
  return (
    <div
      ref={scrollRef}
      data-testid="device-table-scroll"
      data-virtual="true"
      data-row-total={devices.length}
      className="w-full overflow-y-auto"
      style={{ maxHeight: `${DEVICE_TABLE_VIEWPORT_MAX_PX}px` }}
    >
      {table}
    </div>
  );
}

// ── DeviceOverview ───────────────────────────────────────────────────────

export default function DeviceOverview({
  data,
  isLoading = false,
  isError = false,
  statusFilter = 'all',
  linkFilter = 'all',
  hostFilter = 'all',
  onStatusFilterChange,
  onLinkFilterChange,
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
  const byLinkStatus = data?.by_link_status;
  const byHost = useMemo(() => data?.by_host ?? {}, [data?.by_host]);
  // #2601：`by_host` 的键是内部 host_id，本组件此前直接把它当展示值（同一条 host
  // 事实在报告页显示 IP、在这里显示 slug）。复用与选机工作台同键的 host 查询，
  // 显示名一律走 hostLabel()；查不到（权限不足/缓存未到）时回落 host_id，与旧行为一致。
  const { data: hostList } = useQuery({
    queryKey: hostKeys.retiredList(),
    queryFn: () => fetchAllHosts(true),  // #3152：翻页拉全（含退役，历史设备要能归属）
  });
  const hostMap = useMemo(
    () => new Map((hostList ?? []).map(host => [String(host.id), host])),
    [hostList],
  );

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
          byLinkStatus={byLinkStatus}
          byHost={byHost}
          statusFilter={statusFilter}
          linkFilter={linkFilter}
          hostFilter={hostFilter}
          onStatusFilterChange={onStatusFilterChange ?? (() => {})}
          onLinkFilterChange={onLinkFilterChange}
          onHostFilterChange={onHostFilterChange ?? (() => {})}
          hostLabelFor={(hostId) => hostLabel(hostMap.get(hostId), hostId)}
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
            hostMap={hostMap}
          />
        )}
      </div>
    </section>
  );
}
