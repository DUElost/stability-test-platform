import { useEffect, useMemo, useRef, useState, Fragment } from 'react';
import { cn } from '@/lib/utils';
import { BulkBarSpacer } from '@/components/ui/bulk-action-bar';
import { Progress } from '@/components/ui/progress';
import {
  Table,
  TableBody,
  TableCell,
  TableEmptyRow,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { TooltipProvider } from '@/components/ui/tooltip';
import { StatusBadge } from '@/components/ui/status-badge';
import { ChevronDown, Server, Cpu, HardDrive, MemoryStick, Clock, Activity, AlertTriangle, CheckCircle2, MoreHorizontal, Pencil, Trash2, CircleSlash, RotateCcw, ClipboardCheck, RefreshCw } from 'lucide-react';
import { resourceUsageBgClass, resourceUsageTextClass, STAT } from '@/design-system/tokens';
import { formatBytesFromGb, formatDateTimeFull, formatDurationSeconds, formatLocalTime, parseIsoToDate } from '@/utils/format';
import type {
  HostScriptPresence,
  ScriptPresenceItem,
  ScriptPresenceState,
  ScriptPresenceSummary,
} from '@/utils/api/types';

export interface HostResources {
  cpu_load: number;
  cpu_cores?: number;
  ram_usage: number;
  ram_total_gb?: number;
  disk_usage: number | null;
  disk_total_gb?: number;
  temperature?: number;
  uptime_seconds?: number;
}

export interface MountStatus {
  path: string;
  mounted: boolean;
  available_gb?: number;
  total_gb?: number;
}

export type AgentCodeSyncStatus = 'unknown' | 'matched' | 'drift' | 'pending';

export interface HostTableData {
  id: string | number;
  name: string;
  ip: string;
  status: 'ONLINE' | 'OFFLINE' | 'DEGRADED';
  watcher_admin_active?: boolean;
  last_heartbeat?: string;
  /** ADR-0038 D1/D4：退役生命周期（retired_at 非空即退役；与 status 正交） */
  retired_at?: string | null;
  retired_by?: string | null;
  retire_reason?: string | null;
  /** 与 status 正交：曾安装成功 / 有过心跳 */
  agent_installed?: boolean;
  agent_protocol_version?: string | null;
  agent_code_revision?: string | null;
  /** ADR-0040 v1.1：drift/matched 的唯一判据源（远端上报的 code digest） */
  agent_artifact_digest?: string | null;
  expected_code_revision?: string | null;
  agent_code_deployed?: string | null;
  agent_code_deployed_at?: string | null;
  agent_code_sync_status?: AgentCodeSyncStatus;
  resources?: HostResources;
  mount_status?: MountStatus[];
  device_count?: number;
  /**
   * lsusb 枚举到的疑似 Android 设备数（物理 USB 侧，与 device_count 的 adb 口径
   * 并排对照）。null / undefined = 未采集到，渲染为「—」而非 0。
   */
  usb_device_count?: number | null;
  /** Tooltip: adb/lease exclusions from device list (frontend-derived). */
  claim_hint?: string | null;
  active_tasks?: number;
  health_status?: 'HEALTHY' | 'DEGRADED' | 'UNSCHEDULABLE';
  health_reasons?: string[];
}

interface ExpandableHostTableProps {
  hosts: HostTableData[];
  onHotUpdate?: (hostId: string | number) => void;
  isHotUpdating?: (hostId: string | number) => boolean;
  onInstall?: (hostId: string | number) => void;
  isInstalling?: (hostId: string | number) => boolean;
  onFlashPrereqs?: (hostId: string | number) => void;
  isFlashPrereqs?: (hostId: string | number) => boolean;
  onEdit?: (host: HostTableData) => void;
  onDelete?: (host: HostTableData) => void;
  /** ADR-0038 D2：退役 / 解除退役（admin；原因由调用方收集）。 */
  onRetire?: (host: HostTableData) => void;
  onUnretire?: (host: HostTableData) => void;
  isDeleting?: (hostId: string | number) => boolean;
  isAdmin?: boolean;
  onWatcherAdminStateChange?: (hostId: string | number, nextActive: boolean) => void;
  isWatcherAdminStateUpdating?: (hostId: string | number) => boolean;
  canManageWatcherAdminState?: boolean;
  selectedIds?: Set<string | number>;
  onSelectionChange?: (ids: Set<string | number>) => void;
  /**
   * #2958 第五道闸：fleet 级脚本在位汇总（父组件拉取一次；缺省则不渲染汇总行）。
   * 汇总只有计数、**没有逐台名单**，故逐台明细只能走下面两个逐台回调。
   */
  scriptPresenceSummary?: ScriptPresenceSummary | null;
  /** 展开某台主机时按需拉该机矩阵（缺省则展开行内不出现「脚本在位」区块）。 */
  onLoadHostScriptPresence?: (hostId: string | number) => Promise<HostScriptPresence>;
  /** 单机按需重核；成功后组件会重新拉一次该机矩阵。 */
  onRefreshHostScriptPresence?: (hostId: string | number) => Promise<unknown>;
}

function getResourceColor(percentage: number): string {
  return resourceUsageTextClass(percentage);
}

function getProgressColor(percentage: number): string {
  return resourceUsageBgClass(percentage);
}

function formatUsagePercent(value: number | null | undefined, digits = 0): string {
  if (value == null || !Number.isFinite(value)) return '未知';
  return `${value.toFixed(digits)}%`;
}

const REASON_LABELS: Record<string, string> = {
  cpu_high: 'CPU 过高',
  ram_high: '内存过高',
  disk_high: '磁盘过高',
  disk_unknown: '磁盘未知',
  mount_failed: '挂载失败',
  adb_low_healthy_devices: '无健康设备',
  // #2902：USB 树上只剩控制器（零权限判据）——`adb_low_healthy_devices` 要求
  // total_devices>0，整树死亡时它恒不成立，这条才是那种形态的唯一可见信号。
  usb_tree_empty: 'USB 总线空树（主控/供电异常）',
  // #3046：USB 枚举到设备，但 sysfs ADB 接口（ff:42）数为 0——adb_low /
  // usb_tree_empty 之间的缝；warning 级，不打闸。
  adb_interfaces_missing: 'USB 有设备但无 ADB 接口',
  adb_multiple_servers: 'ADB 多 server 冲突',
  // #2900：内核 USB 子系统故障（agent 读内核日志判定）——主机可能一台设备都看不到
  // 而心跳全正常，这两条是唯一可见信号。
  usb_host_controller_dead: 'USB 主控失联（xHCI 死亡）',
  usb_link_degraded: 'USB 链路劣化',
};

const AGENT_SYNC_LABELS: Record<AgentCodeSyncStatus, string> = {
  matched: '已对齐',
  pending: '待上报',
  drift: '内容漂移',
  unknown: '未知',
};

function formatAgentVersionLabel(host: HostTableData): string {
  const protocol = host.agent_protocol_version;
  const revision = host.agent_code_revision ?? host.agent_code_deployed;
  if (protocol && revision) return `${protocol} @${revision}`;
  if (protocol) return protocol;
  if (revision) return `@${revision}`;
  return '—';
}

function agentSyncBadgeClass(status: AgentCodeSyncStatus | undefined): string {
  switch (status) {
    case 'matched':
      return 'bg-success/10 text-success';
    case 'pending':
      return 'bg-info/10 text-info';
    case 'drift':
      return 'bg-destructive/10 text-destructive';
    default:
      return 'bg-muted/50 text-muted-foreground';
  }
}

/** #2958 第五道闸：六态中文文案（与后端闭词表一一对应）。 */
const SCRIPT_PRESENCE_LABELS: Record<ScriptPresenceState, string> = {
  present: '在位',
  missing: '缺失',
  mismatch: '内容不符',
  unknown: '未知',
  n_a: '不适用',
  maintenance: '维护窗',
};

/**
 * 缺口（红）> 未知（灰）> 维护窗（黄）> 在位（绿）：运维先看要动手的。
 * `unknown` 不得渲染成绿，也不计缺口——它的动作是「等 agent 可达」。
 */
const SCRIPT_PRESENCE_ORDER: Record<ScriptPresenceState, number> = {
  missing: 0,
  mismatch: 1,
  unknown: 2,
  maintenance: 3,
  present: 4,
  n_a: 5,
};

function scriptPresenceStateClass(state: ScriptPresenceState): string {
  switch (state) {
    case 'present':
      return 'bg-success/10 text-success';
    case 'missing':
    case 'mismatch':
      return 'bg-destructive/10 text-destructive';
    case 'maintenance':
      return 'bg-warning/10 text-warning';
    default:
      return 'bg-muted/50 text-muted-foreground';
  }
}

function sortScriptPresenceItems(items: ScriptPresenceItem[]): ScriptPresenceItem[] {
  return [...items].sort((a, b) => {
    const byState = SCRIPT_PRESENCE_ORDER[a.state] - SCRIPT_PRESENCE_ORDER[b.state];
    if (byState !== 0) return byState;
    return `${a.name}@${a.version}`.localeCompare(`${b.name}@${b.version}`);
  });
}

interface HostPresenceLoad {
  status: 'loading' | 'ready' | 'error';
  data?: HostScriptPresence;
  error?: string;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function formatHeartbeatLabel(value?: string): string {
  if (!value) return '—';
  const date = parseIsoToDate(value);
  if (!date) return formatLocalTime(value);
  const elapsedSeconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (elapsedSeconds < 60) return '刚刚';
  if (elapsedSeconds < 3600) return `${Math.floor(elapsedSeconds / 60)} 分钟前`;
  if (elapsedSeconds < 86400) return `${Math.floor(elapsedSeconds / 3600)} 小时前`;
  return `${Math.floor(elapsedSeconds / 86400)} 天前`;
}

export function ExpandableHostTable({
  hosts,
  onHotUpdate,
  isHotUpdating,
  onInstall,
  isInstalling,
  onFlashPrereqs,
  isFlashPrereqs,
  onEdit,
  onDelete,
  onRetire,
  onUnretire,
  isDeleting,
  isAdmin,
  onWatcherAdminStateChange,
  isWatcherAdminStateUpdating,
  canManageWatcherAdminState = false,
  selectedIds,
  onSelectionChange,
  scriptPresenceSummary,
  onLoadHostScriptPresence,
  onRefreshHostScriptPresence,
}: ExpandableHostTableProps) {
  const [expandedRows, setExpandedRows] = useState<Set<string | number>>(new Set());
  const [statusFilter, setStatusFilter] = useState<'all' | HostTableData['status']>('all');
  const selectable = !!onSelectionChange;
  const selectAllRef = useRef<HTMLInputElement>(null);
  // #2958：逐台矩阵按需拉取（展开时才请求）+ fleet 汇总明细面板开合。
  const [presenceByHost, setPresenceByHost] = useState<Record<string, HostPresenceLoad>>({});
  const [presenceRefreshing, setPresenceRefreshing] = useState<Set<string | number>>(new Set());
  const presenceInFlight = useRef<Set<string>>(new Set());
  const [presenceBreakdownOpen, setPresenceBreakdownOpen] = useState(false);

  const loadHostPresence = async (hostId: string | number, force = false) => {
    if (!onLoadHostScriptPresence) return;
    const key = String(hostId);
    if (!force && presenceInFlight.current.has(key)) return;
    presenceInFlight.current.add(key);
    setPresenceByHost((prev) => ({
      ...prev,
      [key]: { ...prev[key], status: 'loading', error: undefined },
    }));
    try {
      const data = await onLoadHostScriptPresence(hostId);
      setPresenceByHost((prev) => ({ ...prev, [key]: { status: 'ready', data } }));
    } catch (error) {
      setPresenceByHost((prev) => ({
        ...prev,
        [key]: { ...prev[key], status: 'error', error: errorMessage(error) },
      }));
    } finally {
      presenceInFlight.current.delete(key);
    }
  };

  const refreshHostPresence = async (hostId: string | number) => {
    if (!onRefreshHostScriptPresence) return;
    setPresenceRefreshing((prev) => new Set(prev).add(hostId));
    try {
      await onRefreshHostScriptPresence(hostId);
      await loadHostPresence(hostId, true);
    } catch (error) {
      setPresenceByHost((prev) => ({
        ...prev,
        [String(hostId)]: { ...prev[String(hostId)], status: 'error', error: errorMessage(error) },
      }));
    } finally {
      setPresenceRefreshing((prev) => {
        const next = new Set(prev);
        next.delete(hostId);
        return next;
      });
    }
  };

  const filteredHosts = useMemo(() => {
    if (statusFilter === 'all') return hosts;
    return hosts.filter((host) => host.status === statusFilter);
  }, [hosts, statusFilter]);

  const filteredIds = useMemo(() => filteredHosts.map((host) => host.id), [filteredHosts]);
  const selectedFilteredCount = filteredIds.filter((id) => selectedIds?.has(id)).length;
  const allFilteredSelected = filteredIds.length > 0 && selectedFilteredCount === filteredIds.length;
  const someFilteredSelected = selectedFilteredCount > 0 && !allFilteredSelected;

  useEffect(() => {
    if (!selectAllRef.current) return;
    selectAllRef.current.indeterminate = someFilteredSelected;
  }, [someFilteredSelected]);

  const toggleSelect = (id: string | number, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!onSelectionChange || !selectedIds) return;
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id); else next.add(id);
    onSelectionChange(next);
  };

  const toggleAll = () => {
    if (!onSelectionChange || !selectedIds) return;
    if (allFilteredSelected) {
      const next = new Set(selectedIds);
      filteredIds.forEach((id) => next.delete(id));
      onSelectionChange(next);
    } else {
      const next = new Set(selectedIds);
      filteredIds.forEach((id) => next.add(id));
      onSelectionChange(next);
    }
  };

  const toggleRow = (id: string | number) => {
    const newExpanded = new Set(expandedRows);
    if (newExpanded.has(id)) {
      newExpanded.delete(id);
    } else {
      newExpanded.add(id);
      if (onLoadHostScriptPresence && presenceByHost[String(id)] === undefined) {
        void loadHostPresence(id);
      }
    }
    setExpandedRows(newExpanded);
  };

  const stats = useMemo(() => {
    const onlineHosts = hosts.filter((h) => h.status === 'ONLINE' && h.agent_installed);
    const aligned = onlineHosts.filter((h) => h.agent_code_sync_status === 'matched').length;
    return {
      total: hosts.length,
      online: hosts.filter(h => h.status === 'ONLINE').length,
      offline: hosts.filter(h => h.status === 'OFFLINE').length,
      degraded: hosts.filter(h => h.status === 'DEGRADED').length,
      agentAligned: aligned,
      // #2366：把「落后」单独报出来——只报「已对齐 N/M」时，全队落后一版会读成
      // 「口径坏了」（现场实测：观测时点的 0/48 是真实状态，不是判据 bug）。
      agentDrift: onlineHosts.filter((h) => h.agent_code_sync_status === 'drift').length,
      agentTrackable: onlineHosts.length,
    };
  }, [hosts]);

  // #2958：缺口语义 = missing + mismatch（与告警口径一致）；unknown 不计缺口。
  const presenceCounts = scriptPresenceSummary?.counts;
  const presenceGapCount = presenceCounts
    ? presenceCounts.missing + presenceCounts.mismatch
    : 0;
  const presenceStale = scriptPresenceSummary?.stale === true;

  return (
    <TooltipProvider>
      <div className="space-y-4">
        {/* Summary Stats — 稀疏数字筛选卡 */}
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          <button
            type="button"
            onClick={() => setStatusFilter('all')}
            aria-pressed={statusFilter === 'all'}
            aria-label="筛选全部主机"
            className={cn(
              'bg-card rounded-lg border p-3 flex flex-col gap-2 text-left transition-colors',
              statusFilter === 'all' ? 'border-foreground/40 bg-muted/30' : 'border-border hover:bg-muted/20',
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <p className={STAT.label}>主机总数</p>
              <div className={cn(STAT.iconWell, STAT.iconWellMuted)}>
                <Server />
              </div>
            </div>
            <div className={cn(STAT.value, 'text-2xl')}>{stats.total}</div>
            {stats.agentTrackable > 0 && (
              <p className={STAT.suffix}>
                Agent 已对齐 {stats.agentAligned}/{stats.agentTrackable}
                {stats.agentDrift > 0 && ` · ${stats.agentDrift} 台待热更新`}
              </p>
            )}
          </button>
          <button
            type="button"
            onClick={() => setStatusFilter('ONLINE')}
            aria-pressed={statusFilter === 'ONLINE'}
            aria-label="筛选在线主机"
            className={cn(
              'bg-card rounded-lg border p-3 flex flex-col gap-2 text-left transition-colors',
              statusFilter === 'ONLINE'
                ? 'border-success bg-success/5'
                : 'border-success/30 hover:border-success/40 hover:bg-success/5',
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <p className={STAT.label}>在线</p>
              <div className={cn(STAT.iconWell, STAT.iconWellSuccess)}>
                <CheckCircle2 className="text-success" />
              </div>
            </div>
            <div className={cn(STAT.value, 'text-2xl text-success')}>{stats.online}</div>
          </button>
          <button
            type="button"
            onClick={() => setStatusFilter('DEGRADED')}
            aria-pressed={statusFilter === 'DEGRADED'}
            aria-label="筛选告警主机"
            className={cn(
              'bg-card rounded-lg border p-3 flex flex-col gap-2 text-left transition-colors',
              statusFilter === 'DEGRADED'
                ? 'border-warning bg-warning/5'
                : 'border-warning/30 hover:border-warning/40 hover:bg-warning/5',
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <p className={STAT.label}>告警</p>
              <div className={cn(STAT.iconWell, 'bg-warning/10 text-warning')}>
                <AlertTriangle className="text-warning" />
              </div>
            </div>
            <div className={cn(STAT.value, 'text-2xl text-warning')}>{stats.degraded}</div>
          </button>
          <button
            type="button"
            onClick={() => setStatusFilter('OFFLINE')}
            aria-pressed={statusFilter === 'OFFLINE'}
            aria-label="筛选离线主机"
            className={cn(
              'bg-card rounded-lg border p-3 flex flex-col gap-2 text-left transition-colors',
              statusFilter === 'OFFLINE'
                ? 'border-foreground/40 bg-muted/30'
                : 'border-border hover:bg-muted/20',
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <p className={STAT.label}>离线</p>
              <div className={cn(STAT.iconWell, STAT.iconWellMuted)}>
                <Activity />
              </div>
            </div>
            <div className={cn(STAT.value, 'text-2xl text-muted-foreground')}>{stats.offline}</div>
          </button>
        </div>

        {/* #2958 第五道闸：脚本在位 fleet 汇总（逐台明细在展开行内，汇总接口不含名单） */}
        {scriptPresenceSummary && presenceCounts && (
          <div
            data-testid="script-presence-summary"
            className={cn(
              'rounded-lg border px-3 py-2',
              presenceStale
                ? 'border-warning/40 bg-warning/5'
                : presenceGapCount > 0
                  ? 'border-destructive/30 bg-destructive/5'
                  : 'border-border bg-card',
            )}
          >
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs">
              <span className="inline-flex items-center gap-1.5 text-sm font-medium text-foreground">
                <ClipboardCheck className="h-4 w-4 text-muted-foreground" />
                脚本在位
              </span>
              <span
                className={cn(
                  'font-medium',
                  presenceStale
                    ? 'text-muted-foreground'
                    : presenceGapCount > 0
                      ? 'text-destructive'
                      : 'text-success',
                )}
              >
                缺口 {scriptPresenceSummary.hosts_with_gap} 台
                {presenceGapCount > 0 && `（${presenceGapCount} 项）`}
              </span>
              <span className="text-muted-foreground">未知 {presenceCounts.unknown} 台</span>
              <span className="text-muted-foreground">维护窗 {presenceCounts.maintenance} 台</span>
              {presenceStale && (
                <span
                  className="inline-flex items-center gap-1 font-medium text-warning"
                  data-testid="script-presence-stale"
                >
                  <AlertTriangle className="h-3.5 w-3.5" />
                  账本陈旧（最近完整 sweep：
                  {scriptPresenceSummary.checked_at_min
                    ? formatDateTimeFull(scriptPresenceSummary.checked_at_min)
                    : '无记录'}
                  ）
                </span>
              )}
              <button
                type="button"
                aria-expanded={presenceBreakdownOpen}
                onClick={() => setPresenceBreakdownOpen((open) => !open)}
                className="ml-auto rounded-md px-2 py-0.5 font-medium text-primary transition-colors hover:bg-primary/10"
              >
                {presenceBreakdownOpen ? '收起明细' : '展开明细'}
              </button>
            </div>
            {presenceBreakdownOpen && (
              <div className="mt-2 space-y-1 border-t border-border pt-2 text-[11px] text-muted-foreground">
                <div className="flex flex-wrap gap-x-3 gap-y-1">
                  <span>在位 {presenceCounts.present}</span>
                  <span className={presenceCounts.missing > 0 ? 'text-destructive' : undefined}>
                    缺失 {presenceCounts.missing}
                  </span>
                  <span className={presenceCounts.mismatch > 0 ? 'text-destructive' : undefined}>
                    内容不符 {presenceCounts.mismatch}
                  </span>
                  <span>未知 {presenceCounts.unknown}</span>
                  <span className={presenceCounts.maintenance > 0 ? 'text-warning' : undefined}>
                    维护窗 {presenceCounts.maintenance}
                  </span>
                  <span>不适用 {presenceCounts.n_a}</span>
                  <span>目标版本 {scriptPresenceSummary.full_versions}</span>
                  <span>覆盖主机 {scriptPresenceSummary.hosts_total}</span>
                </div>
                <p>
                  fleet 汇总不含逐台名单：逐台缺口请展开下方对应主机行查看，并用该区块的「重新核验」单机重核。
                </p>
                <p>
                  最近完整 sweep：
                  {scriptPresenceSummary.checked_at_min
                    ? formatDateTimeFull(scriptPresenceSummary.checked_at_min)
                    : '无记录'}
                  {' → '}
                  {scriptPresenceSummary.checked_at_max
                    ? formatDateTimeFull(scriptPresenceSummary.checked_at_max)
                    : '无记录'}
                </p>
              </div>
            )}
          </div>
        )}

        {/* Table */}
        <div className="rounded-xl border border-border bg-card">
          <Table className="min-w-[720px]">
            <TableHeader>
              <TableRow className="sticky top-0 z-10 bg-muted/95 hover:bg-muted/95">
                {selectable && (
                  <TableHead className="w-10 px-3 py-2">
                    <input
                      ref={selectAllRef}
                      type="checkbox"
                      checked={allFilteredSelected}
                      onChange={toggleAll}
                      aria-label="选择全部主机"
                      className="h-4 w-4 rounded border-border accent-primary"
                    />
                  </TableHead>
                )}
                <TableHead className="w-10"></TableHead>
                <TableHead className="min-w-[150px] font-medium">主机</TableHead>
                <TableHead className="min-w-[104px] font-medium">状态</TableHead>
                <TableHead className="min-w-[188px] font-medium text-center whitespace-nowrap">设备 / 任务</TableHead>
                <TableHead className="min-w-[156px] font-medium 2xl:hidden">资源</TableHead>
                <TableHead className="hidden min-w-[112px] font-medium 2xl:table-cell">CPU</TableHead>
                <TableHead className="hidden min-w-[112px] font-medium 2xl:table-cell">内存</TableHead>
                <TableHead className="hidden min-w-[112px] font-medium 2xl:table-cell">磁盘</TableHead>
                <TableHead className="w-28 font-medium whitespace-nowrap">Agent</TableHead>
                <TableHead className="hidden min-w-[96px] font-medium text-right 2xl:table-cell">心跳</TableHead>
                <TableHead className="min-w-[112px] font-medium text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredHosts.map((host) => {
                const isExpanded = expandedRows.has(host.id);
                // #2958：逐台矩阵（展开时按需拉取；未拉过 = undefined）。
                const presence = presenceByHost[String(host.id)];
                const refreshingPresence = presenceRefreshing.has(host.id);
                const presenceItems = presence?.data
                  ? sortScriptPresenceItems(presence.data.items)
                  : [];

                return (
                  <Fragment key={host.id}>
                    <TableRow
                      key={host.id}
                      className={cn(
                        'cursor-pointer hover:bg-muted/50 transition-colors',
                        isExpanded && 'bg-muted/50',
                        selectedIds?.has(host.id) && 'bg-primary/5 hover:bg-primary/10',
                      )}
                      data-state={selectedIds?.has(host.id) ? 'selected' : undefined}
                      onClick={() => toggleRow(host.id)}
                    >
                      {selectable && (
                        <TableCell className="px-3 py-1.5">
                          <input
                            type="checkbox"
                            checked={selectedIds?.has(host.id) ?? false}
                            onClick={(e) => toggleSelect(host.id, e)}
                            onChange={() => {}}
                            aria-label={`选择主机 ${host.name ?? host.id}`}
                            className="h-4 w-4 rounded border-border accent-primary"
                          />
                        </TableCell>
                      )}
                      <TableCell className="px-3 py-1.5">
                        <ChevronDown
                          className={cn(
                            'w-4 h-4 text-muted-foreground transition-transform',
                            !isExpanded && '-rotate-90'
                          )}
                        />
                      </TableCell>
                      <TableCell className="max-w-[200px] px-3 py-1.5">
                        <div className="truncate font-medium text-foreground" title={host.name ?? ''}>
                          {host.name}
                        </div>
                        {host.name !== host.ip && (
                          <div className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground" title={host.ip}>
                            {host.ip}
                          </div>
                        )}
                      </TableCell>
                      <TableCell className="px-3 py-1.5">
                        <div className="space-y-1">
                          <div className="flex items-center gap-1.5">
                            <StatusBadge kind="host" status={host.status} size="sm" />
                            {host.retired_at && (
                              <span
                                className={cn(
                                  'inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-xs font-medium cursor-help',
                                  host.status === 'ONLINE'
                                    ? 'bg-warning/10 text-warning'
                                    : 'bg-muted text-muted-foreground',
                                )}
                                title={`已退役${host.retired_by ? `（${host.retired_by}）` : ''}${
                                  host.retire_reason ? `：${host.retire_reason}` : ''
                                }`}
                                data-testid={`host-retired-badge-${host.id}`}
                              >
                                {host.status === 'ONLINE' ? '已退役但仍在心跳' : '已退役'}
                              </span>
                            )}
                            {host.health_status && host.health_status !== 'HEALTHY' && (
                              <span
                                className={cn(
                                  'inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-xs font-medium cursor-help',
                                  host.health_status === 'UNSCHEDULABLE'
                                    ? 'bg-destructive/10 text-destructive'
                                    : 'bg-warning/10 text-warning'
                                )}
                                title={host.health_reasons?.map(r => REASON_LABELS[r] || r).join(', ') || ''}
                              >
                                {host.health_status === 'UNSCHEDULABLE' ? '禁调' : '降级'}
                              </span>
                            )}
                          </div>
                          <div
                            className={cn(
                              'flex items-center gap-1 text-[11px]',
                              host.watcher_admin_active !== false ? 'text-success' : 'text-destructive',
                            )}
                          >
                            <span className="h-1.5 w-1.5 rounded-full bg-current" />
                            Watch {host.watcher_admin_active !== false ? '已激活' : '未激活'}
                          </div>
                        </div>
                      </TableCell>
                      <TableCell className="px-3 py-1.5 text-center">
                        <div className="inline-flex items-center gap-1">
                          <span
                            className={cn(
                              'inline-flex items-center justify-center rounded-full px-1.5 py-0.5 text-[11px] font-medium',
                              (host.device_count || 0) > 0 ? 'bg-primary/10 text-primary' : 'bg-muted/50 text-muted-foreground'
                            )}
                            title={host.claim_hint ?? '在线设备数（来源：adb devices）'}
                          >
                            在线 {host.device_count || 0}
                          </span>
                          {(() => {
                            // lsusb 对照值：与「在线」(adb devices) 并排，差值即 ADB
                            // 未枚举到的物理设备。未采集显示「—」，不伪装成 0。
                            const usb = host.usb_device_count;
                            const known = typeof usb === 'number' && Number.isFinite(usb);
                            const adbCount = host.device_count || 0;
                            const mismatch = known && usb > adbCount;
                            return (
                              <span
                                className={cn(
                                  'inline-flex items-center justify-center rounded-full px-1.5 py-0.5 text-[11px] font-medium',
                                  !known
                                    ? 'bg-muted/50 text-muted-foreground'
                                    : mismatch
                                      ? 'bg-warning/10 text-warning'
                                      : 'bg-muted/50 text-muted-foreground'
                                )}
                                title={
                                  !known
                                    ? 'USB 设备数未采集到（lsusb 不可用或采集失败）'
                                    : mismatch
                                      ? `USB 枚举 ${usb} 台 > ADB 在线 ${adbCount} 台：设备在 USB 上但 ADB 未枚举，可能存在授权/驱动/多 ADB server 问题（来源：lsusb）`
                                      : `USB 枚举到的疑似 Android 设备数（来源：lsusb），与 ADB 在线数对照`
                                }
                              >
                                USB {known ? usb : '—'}
                              </span>
                            );
                          })()}
                          <span className={cn(
                            'inline-flex items-center justify-center rounded-full px-1.5 py-0.5 text-[11px] font-medium',
                            (host.active_tasks || 0) > 0 ? 'bg-info/10 text-info' : 'bg-muted/50 text-muted-foreground'
                          )}>
                            任务 {host.active_tasks || 0}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell className="px-3 py-1.5 2xl:hidden">
                        {host.resources && host.status === 'ONLINE' ? (
                          <div className="grid min-w-[150px] grid-cols-3 gap-1.5">
                            {[
                              ['CPU', host.resources.cpu_load],
                              ['内存', host.resources.ram_usage],
                              ['磁盘', host.resources.disk_usage],
                            ].map(([label, value]) => (
                              <div key={String(label)} className="text-center">
                                <div className="text-[11px] text-muted-foreground">{label}</div>
                                <div
                                  data-testid={label === '磁盘' ? 'host-disk-usage' : undefined}
                                  className={cn(
                                    'font-mono text-[11px]',
                                    typeof value === 'number'
                                      ? getResourceColor(value)
                                      : 'text-muted-foreground',
                                  )}
                                >
                                  {formatUsagePercent(typeof value === 'number' ? value : null)}
                                </div>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <span className="text-muted-foreground/40">-</span>
                        )}
                      </TableCell>
                      <TableCell className="hidden px-3 py-1.5 2xl:table-cell">
                        {host.resources && host.status === 'ONLINE' ? (
                          <div className="flex items-center gap-2">
                            <Progress
                              value={host.resources.cpu_load}
                              className="h-2 w-16"
                              indicatorClassName={getProgressColor(host.resources.cpu_load)}
                            />
                            <span className={cn('text-xs font-mono', getResourceColor(host.resources.cpu_load))}>
                              {host.resources.cpu_load.toFixed(0)}%
                            </span>
                          </div>
                        ) : (
                          <span className="text-muted-foreground/40">-</span>
                        )}
                      </TableCell>
                      <TableCell className="hidden px-3 py-1.5 2xl:table-cell">
                        {host.resources && host.status === 'ONLINE' ? (
                          <div className="flex items-center gap-2">
                            <Progress
                              value={host.resources.ram_usage}
                              className="h-2 w-16"
                              indicatorClassName={getProgressColor(host.resources.ram_usage)}
                            />
                            <span className={cn('text-xs font-mono', getResourceColor(host.resources.ram_usage))}>
                              {host.resources.ram_usage.toFixed(0)}%
                            </span>
                          </div>
                        ) : (
                          <span className="text-muted-foreground/40">-</span>
                        )}
                      </TableCell>
                      <TableCell className="hidden px-3 py-1.5 2xl:table-cell">
                        {host.resources && host.status === 'ONLINE' ? (
                          host.resources.disk_usage == null ? (
                            <span data-testid="host-disk-usage" className="text-xs font-mono text-muted-foreground">未知</span>
                          ) : (
                            <div className="flex items-center gap-2">
                              <Progress
                                value={host.resources.disk_usage}
                                className="h-2 w-16"
                                indicatorClassName={getProgressColor(host.resources.disk_usage)}
                              />
                              <span
                                data-testid="host-disk-usage"
                                className={cn('text-xs font-mono', getResourceColor(host.resources.disk_usage))}
                              >
                                {host.resources.disk_usage.toFixed(0)}%
                              </span>
                            </div>
                          )
                        ) : (
                          <span className="text-muted-foreground/40">-</span>
                        )}
                      </TableCell>
                      <TableCell className="px-3 py-1.5">
                        {host.agent_installed ? (
                          <div className="flex w-28 flex-col gap-1">
                            <span
                              className="font-mono text-xs text-foreground truncate"
                              title={formatAgentVersionLabel(host)}
                            >
                              {formatAgentVersionLabel(host)}
                            </span>
                            <span
                              className={cn(
                                'inline-flex w-fit items-center rounded-full px-1.5 py-0.5 text-[11px] font-medium',
                                agentSyncBadgeClass(host.agent_code_sync_status),
                              )}
                              title={
                                host.agent_artifact_digest
                                  ? `当前摘要 ${host.agent_artifact_digest.replace(/^sha256:/, '').slice(0, 12)}${host.expected_code_revision ? ` · 期望 HEAD @${host.expected_code_revision}` : ''}`
                                  : host.expected_code_revision
                                    ? `未上报摘要 · 期望 HEAD @${host.expected_code_revision}`
                                    : undefined
                              }
                            >
                              {AGENT_SYNC_LABELS[host.agent_code_sync_status ?? 'unknown']}
                            </span>
                          </div>
                        ) : (
                          <span className="text-muted-foreground/40 text-xs">未安装</span>
                        )}
                      </TableCell>
                      <TableCell
                        className="hidden p-3 text-right text-xs text-muted-foreground 2xl:table-cell"
                        title={host.last_heartbeat ? formatLocalTime(host.last_heartbeat) : undefined}
                      >
                        {host.last_heartbeat
                          ? formatHeartbeatLabel(host.last_heartbeat)
                          : '—'}
                      </TableCell>
                      <TableCell className="px-3 py-1.5 text-right">
                        <div className="inline-flex items-center gap-1.5">
                          {host.retired_at ? (
                            <span className="text-muted-foreground/40 text-xs">已退役</span>
                          ) : host.status === 'ONLINE' && onHotUpdate ? (
                            <>
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  onHotUpdate(host.id);
                                }}
                                disabled={isHotUpdating?.(host.id) || isFlashPrereqs?.(host.id)}
                                aria-label={`${host.name ?? host.id} 热更新 Agent`}
                                className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium text-primary bg-primary/10 hover:bg-primary/15 rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                              >
                                {isHotUpdating?.(host.id) ? '更新中...' : '热更新'}
                              </button>
                              {onFlashPrereqs && host.agent_installed !== false && (
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    onFlashPrereqs(host.id);
                                  }}
                                  disabled={
                                    isFlashPrereqs?.(host.id) || isHotUpdating?.(host.id)
                                  }
                                  aria-label={`${host.name ?? host.id} 补齐刷机前置`}
                                  title="补齐 dialout / udev / Qt 刷机依赖"
                                  className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium text-muted-foreground bg-muted/60 hover:bg-muted rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                                >
                                  {isFlashPrereqs?.(host.id) ? '补齐中...' : '刷机前置'}
                                </button>
                              )}
                            </>
                          ) : host.status !== 'ONLINE' && onInstall ? (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                onInstall(host.id);
                              }}
                              disabled={isInstalling?.(host.id)}
                              aria-label={
                                host.agent_installed
                                  ? `${host.name ?? host.id} 重新安装 Agent`
                                  : `${host.name ?? host.id} 首次安装 Agent`
                              }
                              title={
                                host.agent_installed
                                  ? 'Agent 曾安装成功，当前离线 — 可重新安装'
                                  : '尚未检测到 Agent 安装记录'
                              }
                              className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium text-warning bg-warning/10 hover:bg-warning/20 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                            >
                              {isInstalling?.(host.id)
                                ? '安装中...'
                                : host.agent_installed
                                  ? '重新安装'
                                  : '首次安装'}
                            </button>
                          ) : (
                            <span className="text-muted-foreground/40 text-xs">-</span>
                          )}
                          {isAdmin && (onEdit || onDelete || onRetire || onUnretire) && (
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <button
                                  type="button"
                                  onClick={(e) => e.stopPropagation()}
                                  aria-label={`${host.name ?? host.id} 更多操作`}
                                  className="inline-flex items-center justify-center rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                                >
                                  <MoreHorizontal className="h-4 w-4" />
                                </button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end" className="w-32">
                                {onEdit && (
                                  <DropdownMenuItem onClick={() => onEdit(host)}>
                                    <Pencil className="mr-2 h-3.5 w-3.5" />
                                    编辑
                                  </DropdownMenuItem>
                                )}
                                {onRetire && !host.retired_at && (
                                  <DropdownMenuItem onClick={() => onRetire(host)}>
                                    <CircleSlash className="mr-2 h-3.5 w-3.5" />
                                    退役
                                  </DropdownMenuItem>
                                )}
                                {onUnretire && host.retired_at && (
                                  <DropdownMenuItem onClick={() => onUnretire(host)}>
                                    <RotateCcw className="mr-2 h-3.5 w-3.5" />
                                    解除退役
                                  </DropdownMenuItem>
                                )}
                                {onDelete && (
                                  <DropdownMenuItem
                                    disabled={isDeleting?.(host.id)}
                                    onClick={() => onDelete(host)}
                                    className="text-destructive focus:bg-destructive/10 focus:text-destructive"
                                  >
                                    <Trash2 className="mr-2 h-3.5 w-3.5" />
                                    删除
                                  </DropdownMenuItem>
                                )}
                              </DropdownMenuContent>
                            </DropdownMenu>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>

                    {/* Expanded Details */}
                    {isExpanded && (
                      <TableRow className="bg-muted/40 hover:bg-muted/40">
                        <TableCell colSpan={selectable ? 12 : 11} className="p-4">
                          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
                            {/* CPU Details */}
                            <div className="bg-card rounded-lg border border-border p-3">
                              <div className="flex items-center gap-2 mb-2">
                                <Cpu className="w-4 h-4 text-muted-foreground" />
                                <span className="text-sm font-medium text-foreground">CPU</span>
                              </div>
                              {host.resources ? (
                                <div className="space-y-1">
                                  <div className="flex justify-between text-xs">
                                    <span className="text-muted-foreground">负载</span>
                                    <span className={cn('font-mono', getResourceColor(host.resources.cpu_load))}>
                                      {host.resources.cpu_load.toFixed(1)}%
                                    </span>
                                  </div>
                                  {host.resources.cpu_cores && (
                                    <div className="flex justify-between text-xs">
                                      <span className="text-muted-foreground">核心数</span>
                                      <span className="font-mono text-foreground">{host.resources.cpu_cores}</span>
                                    </div>
                                  )}
                                </div>
                              ) : (
                                <span className="text-xs text-muted-foreground">无数据</span>
                              )}
                            </div>

                            {/* Memory Details */}
                            <div className="bg-card rounded-lg border border-border p-3">
                              <div className="flex items-center gap-2 mb-2">
                                <MemoryStick className="w-4 h-4 text-muted-foreground" />
                                <span className="text-sm font-medium text-foreground">内存</span>
                              </div>
                              {host.resources ? (
                                <div className="space-y-1">
                                  <div className="flex justify-between text-xs">
                                    <span className="text-muted-foreground">使用率</span>
                                    <span className={cn('font-mono', getResourceColor(host.resources.ram_usage))}>
                                      {host.resources.ram_usage.toFixed(1)}%
                                    </span>
                                  </div>
                                  {host.resources.ram_total_gb && (
                                    <div className="flex justify-between text-xs">
                                      <span className="text-muted-foreground">总量</span>
                                      <span className="font-mono text-foreground">{formatBytesFromGb(host.resources.ram_total_gb)}</span>
                                    </div>
                                  )}
                                </div>
                              ) : (
                                <span className="text-xs text-muted-foreground">无数据</span>
                              )}
                            </div>

                            {/* Disk Details */}
                            <div className="bg-card rounded-lg border border-border p-3">
                              <div className="flex items-center gap-2 mb-2">
                                <HardDrive className="w-4 h-4 text-muted-foreground" />
                                <span className="text-sm font-medium text-foreground">磁盘</span>
                              </div>
                              {host.resources ? (
                                <div className="space-y-1">
                                  <div className="flex justify-between text-xs">
                                    <span className="text-muted-foreground">使用率</span>
                                    <span className={cn(
                                      'font-mono',
                                      host.resources.disk_usage == null
                                        ? 'text-muted-foreground'
                                        : getResourceColor(host.resources.disk_usage),
                                    )}>
                                      {formatUsagePercent(host.resources.disk_usage, 1)}
                                    </span>
                                  </div>
                                  {host.resources.disk_total_gb && (
                                    <div className="flex justify-between text-xs">
                                      <span className="text-muted-foreground">总量</span>
                                      <span className="font-mono text-foreground">{formatBytesFromGb(host.resources.disk_total_gb)}</span>
                                    </div>
                                  )}
                                </div>
                              ) : (
                                <span className="text-xs text-muted-foreground">无数据</span>
                              )}
                            </div>

                            {/* Agent Version */}
                            <div className="bg-card rounded-lg border border-border p-3">
                              <div className="flex items-center gap-2 mb-2">
                                <Server className="w-4 h-4 text-muted-foreground" />
                                <span className="text-sm font-medium text-foreground">Agent 版本</span>
                              </div>
                              {host.agent_installed ? (
                                <div className="space-y-1">
                                  <div className="flex justify-between text-xs gap-2">
                                    <span className="text-muted-foreground shrink-0">协议</span>
                                    <span className="font-mono text-foreground truncate">
                                      {host.agent_protocol_version ?? '—'}
                                    </span>
                                  </div>
                                  <div className="flex justify-between text-xs gap-2">
                                    <span className="text-muted-foreground shrink-0">部署摘要</span>
                                    <span
                                      className="font-mono text-foreground truncate"
                                      title={host.agent_artifact_digest ?? undefined}
                                    >
                                      {host.agent_artifact_digest
                                        ? host.agent_artifact_digest.replace(/^sha256:/, '').slice(0, 12)
                                        : '未上报'}
                                    </span>
                                  </div>
                                  <div className="flex justify-between text-xs gap-2">
                                    <span className="text-muted-foreground shrink-0">上报修订</span>
                                    <span className="font-mono text-foreground truncate">
                                      {host.agent_code_revision ? `@${host.agent_code_revision}` : '—'}
                                    </span>
                                  </div>
                                  <div className="flex justify-between text-xs gap-2">
                                    <span className="text-muted-foreground shrink-0">期望修订</span>
                                    <span className="font-mono text-foreground truncate">
                                      {host.expected_code_revision ? `@${host.expected_code_revision}` : '—'}
                                    </span>
                                  </div>
                                  <div className="flex justify-between text-xs gap-2">
                                    <span className="text-muted-foreground shrink-0">热更新部署</span>
                                    <span className="font-mono text-foreground truncate">
                                      {host.agent_code_deployed ? `@${host.agent_code_deployed}` : '—'}
                                    </span>
                                  </div>
                                  {host.agent_code_deployed_at && (
                                    <div className="flex justify-between text-xs gap-2">
                                      <span className="text-muted-foreground shrink-0">部署时间</span>
                                      <span className="font-mono text-foreground truncate">
                                        {formatDateTimeFull(host.agent_code_deployed_at)}
                                      </span>
                                    </div>
                                  )}
                                  <div className="flex justify-between text-xs gap-2">
                                    <span className="text-muted-foreground shrink-0">对齐状态</span>
                                    <span
                                      className={cn(
                                        'rounded-full px-1.5 py-0.5 text-[11px] font-medium',
                                        agentSyncBadgeClass(host.agent_code_sync_status),
                                      )}
                                    >
                                      {AGENT_SYNC_LABELS[host.agent_code_sync_status ?? 'unknown']}
                                    </span>
                                  </div>
                                </div>
                              ) : (
                                <span className="text-xs text-muted-foreground">未安装 Agent</span>
                              )}
                            </div>

                            {/* Other Info */}
                            <div className="bg-card rounded-lg border border-border p-3">
                              <div className="flex items-center gap-2 mb-2">
                                <Clock className="w-4 h-4 text-muted-foreground" />
                                <span className="text-sm font-medium text-foreground">其他</span>
                              </div>
                              <div className="space-y-1.5">
                                <div className="flex items-center justify-between gap-2 text-xs">
                                  <span className="text-muted-foreground">Watch</span>
                                  <div className="flex items-center gap-2">
                                    <span className={host.watcher_admin_active !== false ? 'text-success' : 'text-destructive'}>
                                      {host.watcher_admin_active !== false ? '已激活' : '未激活'}
                                    </span>
                                    {onWatcherAdminStateChange && (
                                      <button
                                        role="switch"
                                        aria-checked={host.watcher_admin_active !== false}
                                        aria-label={`${host.name ?? host.id} Watcher 管理开关`}
                                        disabled={
                                          !canManageWatcherAdminState ||
                                          !!isWatcherAdminStateUpdating?.(host.id)
                                        }
                                        data-testid={`watcher-admin-toggle-${host.id}`}
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          onWatcherAdminStateChange(
                                            host.id,
                                            !(host.watcher_admin_active !== false),
                                          );
                                        }}
                                        className={cn(
                                          'relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full',
                                          'border-2 border-transparent transition-colors',
                                          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
                                          'disabled:cursor-not-allowed disabled:opacity-50',
                                          host.watcher_admin_active !== false ? 'bg-success' : 'bg-muted',
                                        )}
                                      >
                                        <span
                                          className={cn(
                                            'pointer-events-none inline-block h-4 w-4 rounded-full bg-card shadow-sm',
                                            'ring-0 transition-transform',
                                            host.watcher_admin_active !== false ? 'translate-x-4' : 'translate-x-0',
                                          )}
                                        />
                                      </button>
                                    )}
                                  </div>
                                </div>
                                <div className="flex justify-between gap-2 text-xs">
                                  <span className="text-muted-foreground">最近心跳</span>
                                  <span
                                    className="font-mono text-foreground"
                                    title={host.last_heartbeat ? formatLocalTime(host.last_heartbeat) : undefined}
                                  >
                                    {formatHeartbeatLabel(host.last_heartbeat)}
                                  </span>
                                </div>
                                {host.resources && (
                                  <>
                                    {host.resources.temperature !== undefined && (
                                      <div className="flex justify-between text-xs">
                                        <span className="text-muted-foreground">温度</span>
                                        <span className={cn(
                                          'font-mono',
                                          host.resources.temperature > 80 ? 'text-destructive' :
                                          host.resources.temperature > 60 ? 'text-warning' : 'text-foreground'
                                        )}>
                                          {host.resources.temperature.toFixed(1)}°C
                                        </span>
                                      </div>
                                    )}
                                    {host.resources.uptime_seconds !== undefined && (
                                      <div className="flex justify-between text-xs">
                                        <span className="text-muted-foreground">运行时间</span>
                                        <span className="font-mono text-foreground">{formatDurationSeconds(host.resources.uptime_seconds, 'compact')}</span>
                                      </div>
                                    )}
                                  </>
                                )}
                              </div>
                            </div>
                          </div>

                          {/* #2958 第五道闸：脚本在位矩阵（展开时按需拉；缺口优先排序） */}
                          {onLoadHostScriptPresence && (
                            <div
                              data-testid={`host-script-presence-${host.id}`}
                              className="mt-4 rounded-lg border border-border bg-card p-3"
                            >
                              <div className="mb-2 flex flex-wrap items-center gap-2">
                                <ClipboardCheck className="h-4 w-4 text-muted-foreground" />
                                <span className="text-sm font-medium text-foreground">脚本在位</span>
                                {presence?.data?.checked_at && (
                                  <span
                                    className="text-[11px] text-muted-foreground"
                                    title={formatLocalTime(presence.data.checked_at)}
                                  >
                                    核验于 {formatHeartbeatLabel(presence.data.checked_at)}
                                  </span>
                                )}
                                {onRefreshHostScriptPresence && (
                                  <button
                                    type="button"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      void refreshHostPresence(host.id);
                                    }}
                                    disabled={refreshingPresence}
                                    aria-label={`${host.name ?? host.id} 重新核验脚本在位`}
                                    className="ml-auto inline-flex items-center gap-1 rounded-md bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary transition-colors hover:bg-primary/15 disabled:cursor-not-allowed disabled:opacity-50"
                                  >
                                    <RefreshCw className={cn('h-3.5 w-3.5', refreshingPresence && 'animate-spin')} />
                                    {refreshingPresence ? '核验中...' : '重新核验'}
                                  </button>
                                )}
                              </div>
                              {presenceStale && (
                                <p
                                  data-testid={`host-script-presence-stale-${host.id}`}
                                  className="mb-2 rounded-md bg-warning/10 px-2 py-1 text-[11px] text-warning"
                                >
                                  账本陈旧（最近完整 sweep：
                                  {scriptPresenceSummary?.checked_at_min
                                    ? formatDateTimeFull(scriptPresenceSummary.checked_at_min)
                                    : '无记录'}
                                  ），当前状态可能已过期
                                </p>
                              )}
                              {presence?.status === 'error' && (
                                <p className="mb-2 text-[11px] text-destructive">
                                  核验数据加载失败：{presence.error || '未知错误'}
                                  <button
                                    type="button"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      void loadHostPresence(host.id, true);
                                    }}
                                    className="ml-2 rounded-md px-1.5 py-0.5 font-medium text-primary hover:bg-primary/10"
                                  >
                                    重试
                                  </button>
                                </p>
                              )}
                              {!presence || (presence.status === 'loading' && !presence.data) ? (
                                <span className="text-xs text-muted-foreground">加载中...</span>
                              ) : presence.data ? (
                                <div className="space-y-1.5">
                                  <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                                    <span>在位 {presence.data.counts.present}</span>
                                    <span
                                      className={
                                        presence.data.counts.missing + presence.data.counts.mismatch > 0
                                          ? 'text-destructive'
                                          : undefined
                                      }
                                    >
                                      缺口 {presence.data.counts.missing + presence.data.counts.mismatch}
                                      （缺失 {presence.data.counts.missing} · 内容不符 {presence.data.counts.mismatch}）
                                    </span>
                                    <span>未知 {presence.data.counts.unknown}</span>
                                    <span>维护窗 {presence.data.counts.maintenance}</span>
                                  </div>
                                  {presenceItems.length === 0 ? (
                                    <p className="text-xs text-muted-foreground">
                                      暂无条目——该主机尚未跑过 sweep，可点「重新核验」。
                                    </p>
                                  ) : (
                                    <ul className="divide-y divide-border/60">
                                      {presenceItems.map((item) => (
                                        <li
                                          key={`${item.name}@${item.version}`}
                                          className="flex items-start gap-2 py-1.5"
                                        >
                                          <span
                                            className={cn(
                                              'mt-px inline-flex shrink-0 items-center rounded-full px-1.5 py-0.5 text-[11px] font-medium',
                                              scriptPresenceStateClass(item.state),
                                            )}
                                          >
                                            {SCRIPT_PRESENCE_LABELS[item.state] ?? item.state}
                                          </span>
                                          <span className="shrink-0 font-mono text-xs text-foreground">
                                            {item.name}@{item.version}
                                          </span>
                                          {item.detail && (
                                            <span
                                              className="min-w-0 truncate text-[11px] text-muted-foreground"
                                              title={item.detail}
                                            >
                                              {item.detail}
                                            </span>
                                          )}
                                        </li>
                                      ))}
                                    </ul>
                                  )}
                                </div>
                              ) : null}
                            </div>
                          )}
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                );
              })}
            {filteredHosts.length === 0 && (
              <TableEmptyRow colSpan={16}>
                {hosts.length === 0 ? '暂无主机' : '没有符合当前筛选条件的主机'}
              </TableEmptyRow>
            )}
            </TableBody>
          </Table>
        </div>

        {/* 全选后底部悬浮操作条会挡住最后一行；用真实占位撑开滚动，避免与 PageContainer lg:p-8 抢 padding */}
        {selectable && selectedIds && selectedIds.size > 0 && (
          <BulkBarSpacer testId="host-table-selection-spacer" />
        )}
      </div>
    </TooltipProvider>
  );
}
