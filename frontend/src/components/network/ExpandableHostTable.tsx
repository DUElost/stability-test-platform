import { useEffect, useMemo, useRef, useState, Fragment } from 'react';
import { cn } from '@/lib/utils';
import { Progress } from '@/components/ui/progress';
import {
  Table,
  TableBody,
  TableCell,
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
import { ChevronDown, Server, Cpu, HardDrive, MemoryStick, Clock, Activity, AlertTriangle, CheckCircle2, MoreHorizontal, Pencil, Trash2 } from 'lucide-react';
import { resourceUsageBgClass, resourceUsageTextClass } from '@/design-system/tokens';
import { formatBytesFromGb, formatDurationSeconds, formatLocalTime, parseIsoToDate } from '@/utils/format';

export interface HostResources {
  cpu_load: number;
  cpu_cores?: number;
  ram_usage: number;
  ram_total_gb?: number;
  disk_usage: number;
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
  /** 与 status 正交：曾安装成功 / 有过心跳 */
  agent_installed?: boolean;
  agent_protocol_version?: string | null;
  agent_code_revision?: string | null;
  expected_code_revision?: string | null;
  agent_code_deployed?: string | null;
  agent_code_deployed_at?: string | null;
  agent_code_sync_status?: AgentCodeSyncStatus;
  resources?: HostResources;
  mount_status?: MountStatus[];
  device_count?: number;
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
  onEdit?: (host: HostTableData) => void;
  onDelete?: (host: HostTableData) => void;
  isDeleting?: (hostId: string | number) => boolean;
  isAdmin?: boolean;
  onWatcherAdminStateChange?: (hostId: string | number, nextActive: boolean) => void;
  isWatcherAdminStateUpdating?: (hostId: string | number) => boolean;
  canManageWatcherAdminState?: boolean;
  selectedIds?: Set<string | number>;
  onSelectionChange?: (ids: Set<string | number>) => void;
}

function getResourceColor(percentage: number): string {
  return resourceUsageTextClass(percentage);
}

function getProgressColor(percentage: number): string {
  return resourceUsageBgClass(percentage);
}

const REASON_LABELS: Record<string, string> = {
  cpu_high: 'CPU 过高',
  ram_high: '内存过高',
  disk_high: '磁盘过高',
  mount_failed: '挂载失败',
  adb_low_healthy_devices: '无健康设备',
};

const AGENT_SYNC_LABELS: Record<AgentCodeSyncStatus, string> = {
  matched: '已对齐',
  pending: '待上报',
  drift: '版本漂移',
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
  onEdit,
  onDelete,
  isDeleting,
  isAdmin,
  onWatcherAdminStateChange,
  isWatcherAdminStateUpdating,
  canManageWatcherAdminState = false,
  selectedIds,
  onSelectionChange,
}: ExpandableHostTableProps) {
  const [expandedRows, setExpandedRows] = useState<Set<string | number>>(new Set());
  const [statusFilter, setStatusFilter] = useState<'all' | HostTableData['status']>('all');
  const selectable = !!onSelectionChange;
  const selectAllRef = useRef<HTMLInputElement>(null);

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
      agentTrackable: onlineHosts.length,
    };
  }, [hosts]);

  return (
    <TooltipProvider>
      <div className="space-y-4">
        {/* Summary Stats - 点击筛选，对齐设备页 */}
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          <button
            type="button"
            onClick={() => setStatusFilter('all')}
            aria-pressed={statusFilter === 'all'}
            aria-label="筛选全部主机"
            className={cn(
              'bg-card rounded-lg border p-3 flex items-center gap-3 text-left transition-all',
              statusFilter === 'all' ? 'border-muted-foreground shadow-sm' : 'border-border',
            )}
          >
            <div className="w-10 h-10 rounded-lg bg-muted/50 flex items-center justify-center">
              <Server className="w-5 h-5 text-muted-foreground" />
            </div>
            <div>
              <div className="text-xl font-semibold text-foreground">{stats.total}</div>
              <div className="text-xs text-muted-foreground">主机总数</div>
              {stats.agentTrackable > 0 && (
                <div className="text-[11px] text-muted-foreground mt-0.5">
                  Agent 已对齐 {stats.agentAligned}/{stats.agentTrackable}
                </div>
              )}
            </div>
          </button>
          <button
            type="button"
            onClick={() => setStatusFilter('ONLINE')}
            aria-pressed={statusFilter === 'ONLINE'}
            aria-label="筛选在线主机"
            className={cn(
              'bg-card rounded-lg border p-3 flex items-center gap-3 text-left transition-all',
              statusFilter === 'ONLINE'
                ? 'border-success shadow-md bg-success/5'
                : 'border-success/30 hover:border-success/40 hover:bg-success/5',
            )}
          >
            <div className="w-10 h-10 rounded-lg bg-success/10 flex items-center justify-center">
              <CheckCircle2 className="w-5 h-5 text-success" />
            </div>
            <div>
              <div className="text-xl font-semibold text-success">{stats.online}</div>
              <div className="text-xs text-muted-foreground">在线</div>
            </div>
          </button>
          <button
            type="button"
            onClick={() => setStatusFilter('DEGRADED')}
            aria-pressed={statusFilter === 'DEGRADED'}
            aria-label="筛选告警主机"
            className={cn(
              'bg-card rounded-lg border p-3 flex items-center gap-3 text-left transition-all',
              statusFilter === 'DEGRADED'
                ? 'border-warning shadow-md bg-warning/5'
                : 'border-warning/30 hover:border-warning/40 hover:bg-warning/5',
            )}
          >
            <div className="w-10 h-10 rounded-lg bg-warning/10 flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-warning" />
            </div>
            <div>
              <div className="text-xl font-semibold text-warning">{stats.degraded}</div>
              <div className="text-xs text-muted-foreground">告警</div>
            </div>
          </button>
          <button
            type="button"
            onClick={() => setStatusFilter('OFFLINE')}
            aria-pressed={statusFilter === 'OFFLINE'}
            aria-label="筛选离线主机"
            className={cn(
              'bg-card rounded-lg border p-3 flex items-center gap-3 text-left transition-all',
              statusFilter === 'OFFLINE' ? 'border-muted-foreground shadow-sm' : 'border-border hover:bg-muted/30',
            )}
          >
            <div className="w-10 h-10 rounded-lg bg-muted/50 flex items-center justify-center">
              <Activity className="w-5 h-5 text-muted-foreground" />
            </div>
            <div>
              <div className="text-xl font-semibold text-muted-foreground">{stats.offline}</div>
              <div className="text-xs text-muted-foreground">离线</div>
            </div>
          </button>
        </div>

        {/* Table */}
        <div className="overflow-hidden rounded-xl border border-border bg-card">
          <Table className="min-w-[720px]">
            <TableHeader>
              <TableRow className="bg-muted/50 hover:bg-muted/50">
                {selectable && (
                  <TableHead className="w-10 p-3">
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
                <TableHead className="min-w-[112px] font-medium text-center whitespace-nowrap">设备 / 任务</TableHead>
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
                        <TableCell className="p-3">
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
                      <TableCell className="p-3">
                        <ChevronDown
                          className={cn(
                            'w-4 h-4 text-muted-foreground transition-transform',
                            !isExpanded && '-rotate-90'
                          )}
                        />
                      </TableCell>
                      <TableCell className="max-w-[200px] p-3">
                        <div className="truncate font-medium text-foreground" title={host.name ?? ''}>
                          {host.name}
                        </div>
                        <div className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground" title={host.ip}>
                          {host.ip}
                        </div>
                      </TableCell>
                      <TableCell className="p-3">
                        <div className="space-y-1">
                          <div className="flex items-center gap-1.5">
                            <StatusBadge kind="host" status={host.status} size="sm" />
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
                              'flex items-center gap-1 text-[10px]',
                              host.watcher_admin_active !== false ? 'text-success' : 'text-destructive',
                            )}
                          >
                            <span className="h-1.5 w-1.5 rounded-full bg-current" />
                            Watch {host.watcher_admin_active !== false ? '已激活' : '未激活'}
                          </div>
                        </div>
                      </TableCell>
                      <TableCell className="p-3 text-center">
                        <div className="inline-flex items-center gap-1">
                          <span
                            className={cn(
                              'inline-flex items-center justify-center rounded-full px-1.5 py-0.5 text-[11px] font-medium',
                              (host.device_count || 0) > 0 ? 'bg-primary/10 text-primary' : 'bg-muted/50 text-muted-foreground'
                            )}
                            title={host.claim_hint ?? '设备数'}
                          >
                            设备 {host.device_count || 0}
                          </span>
                          <span className={cn(
                            'inline-flex items-center justify-center rounded-full px-1.5 py-0.5 text-[11px] font-medium',
                            (host.active_tasks || 0) > 0 ? 'bg-info/10 text-info' : 'bg-muted/50 text-muted-foreground'
                          )}>
                            任务 {host.active_tasks || 0}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell className="p-3 2xl:hidden">
                        {host.resources && host.status === 'ONLINE' ? (
                          <div className="grid min-w-[150px] grid-cols-3 gap-1.5">
                            {[
                              ['CPU', host.resources.cpu_load],
                              ['内存', host.resources.ram_usage],
                              ['磁盘', host.resources.disk_usage],
                            ].map(([label, value]) => (
                              <div key={String(label)} className="text-center">
                                <div className="text-[10px] text-muted-foreground">{label}</div>
                                <div className={cn('font-mono text-[11px]', getResourceColor(Number(value)))}>
                                  {Number(value).toFixed(0)}%
                                </div>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <span className="text-muted-foreground/40">-</span>
                        )}
                      </TableCell>
                      <TableCell className="hidden p-3 2xl:table-cell">
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
                      <TableCell className="hidden p-3 2xl:table-cell">
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
                      <TableCell className="hidden p-3 2xl:table-cell">
                        {host.resources && host.status === 'ONLINE' ? (
                          <div className="flex items-center gap-2">
                            <Progress
                              value={host.resources.disk_usage}
                              className="h-2 w-16"
                              indicatorClassName={getProgressColor(host.resources.disk_usage)}
                            />
                            <span className={cn('text-xs font-mono', getResourceColor(host.resources.disk_usage))}>
                              {host.resources.disk_usage.toFixed(0)}%
                            </span>
                          </div>
                        ) : (
                          <span className="text-muted-foreground/40">-</span>
                        )}
                      </TableCell>
                      <TableCell className="p-3">
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
                                'inline-flex w-fit items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                                agentSyncBadgeClass(host.agent_code_sync_status),
                              )}
                              title={
                                host.expected_code_revision
                                  ? `期望修订 ${host.expected_code_revision}`
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
                      <TableCell className="p-3 text-right">
                        <div className="inline-flex items-center gap-1.5">
                          {host.status === 'ONLINE' && onHotUpdate ? (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                onHotUpdate(host.id);
                              }}
                              disabled={isHotUpdating?.(host.id)}
                              aria-label={`${host.name ?? host.id} 热更新 Agent`}
                              className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium text-primary bg-primary/10 hover:bg-primary/15 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                            >
                              {isHotUpdating?.(host.id) ? '更新中...' : '热更新'}
                            </button>
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
                          {isAdmin && (onEdit || onDelete) && (
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
                      <TableRow className="bg-muted/50/50 hover:bg-muted/50/50">
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
                                    <span className={cn('font-mono', getResourceColor(host.resources.disk_usage))}>
                                      {host.resources.disk_usage.toFixed(1)}%
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
                                        {formatLocalTime(host.agent_code_deployed_at)}
                                      </span>
                                    </div>
                                  )}
                                  <div className="flex justify-between text-xs gap-2">
                                    <span className="text-muted-foreground shrink-0">对齐状态</span>
                                    <span
                                      className={cn(
                                        'rounded-full px-1.5 py-0.5 text-[10px] font-medium',
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
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                );
              })}
            </TableBody>
          </Table>
        </div>

        {filteredHosts.length === 0 && (
          <div className="text-center py-8 text-muted-foreground text-sm">
            {hosts.length === 0 ? '暂无主机' : '没有符合当前筛选条件的主机'}
          </div>
        )}
      </div>
    </TooltipProvider>
  );
}
