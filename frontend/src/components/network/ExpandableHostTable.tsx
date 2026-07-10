import { useState, useMemo, Fragment } from 'react';
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
import { TooltipProvider } from '@/components/ui/tooltip';
import { StatusBadge } from '@/components/ui/status-badge';
import { ChevronDown, Server, Cpu, HardDrive, MemoryStick, Clock, Activity, AlertTriangle, CheckCircle2, Pencil, Trash2 } from 'lucide-react';
import {
  resourceUsageBgClass,
  resourceUsageTextClass,
} from '@/design-system/tokens';
import { formatBytesFromGb, formatDurationSeconds, formatLocalTime } from '@/utils/format';

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

export interface HostTableData {
  id: string | number;
  name: string;
  ip: string;
  status: 'ONLINE' | 'OFFLINE' | 'DEGRADED';
  watcher_admin_active?: boolean;
  last_heartbeat?: string;
  /** 与 status 正交：曾安装成功 / 有过心跳 */
  agent_installed?: boolean;
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
  const selectable = !!onSelectionChange;

  const toggleSelect = (id: string | number, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!onSelectionChange || !selectedIds) return;
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id); else next.add(id);
    onSelectionChange(next);
  };

  const toggleAll = () => {
    if (!onSelectionChange || !selectedIds) return;
    if (selectedIds.size === hosts.length) {
      onSelectionChange(new Set());
    } else {
      onSelectionChange(new Set(hosts.map(h => h.id)));
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

  const stats = useMemo(() => ({
    total: hosts.length,
    online: hosts.filter(h => h.status === 'ONLINE').length,
    offline: hosts.filter(h => h.status === 'OFFLINE').length,
    degraded: hosts.filter(h => h.status === 'DEGRADED').length,
  }), [hosts]);

  return (
    <TooltipProvider>
      <div className="space-y-4">
        {/* Summary Stats - 简洁版本 */}
        <div className="grid grid-cols-4 gap-3">
          <div className="bg-card rounded-lg border border-border p-3 flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-muted/50 flex items-center justify-center">
              <Server className="w-5 h-5 text-muted-foreground" />
            </div>
            <div>
              <div className="text-xl font-semibold text-foreground">{stats.total}</div>
              <div className="text-xs text-muted-foreground">主机总数</div>
            </div>
          </div>
          <div className="bg-card rounded-lg border border-success/30 p-3 flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-success/10 flex items-center justify-center">
              <CheckCircle2 className="w-5 h-5 text-success" />
            </div>
            <div>
              <div className="text-xl font-semibold text-success">{stats.online}</div>
              <div className="text-xs text-muted-foreground">在线</div>
            </div>
          </div>
          <div className="bg-card rounded-lg border border-warning/30 p-3 flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-warning/10 flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-warning" />
            </div>
            <div>
              <div className="text-xl font-semibold text-warning">{stats.degraded}</div>
              <div className="text-xs text-muted-foreground">告警</div>
            </div>
          </div>
          <div className="bg-card rounded-lg border border-border p-3 flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-muted/50 flex items-center justify-center">
              <Activity className="w-5 h-5 text-muted-foreground" />
            </div>
            <div>
              <div className="text-xl font-semibold text-muted-foreground">{stats.offline}</div>
              <div className="text-xs text-muted-foreground">离线</div>
            </div>
          </div>
        </div>

        {/* Table */}
        <div className="bg-card rounded-xl border border-border overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/50 hover:bg-muted/50">
                {selectable && (
                  <TableHead className="w-10 p-3">
                    <input
                      type="checkbox"
                      checked={selectedIds ? selectedIds.size === hosts.length && hosts.length > 0 : false}
                      onChange={toggleAll}
                      className="rounded border-border"
                    />
                  </TableHead>
                )}
                <TableHead className="w-10"></TableHead>
                <TableHead className="font-medium">主机名称</TableHead>
                <TableHead className="font-medium">IP地址</TableHead>
                <TableHead className="font-medium">状态</TableHead>
                <TableHead className="font-medium text-center">设备数</TableHead>
                <TableHead className="font-medium text-center">任务数</TableHead>
                <TableHead className="font-medium">CPU</TableHead>
                <TableHead className="font-medium">内存</TableHead>
                <TableHead className="font-medium">磁盘</TableHead>
                <TableHead className="font-medium text-center whitespace-nowrap">Watch状态</TableHead>
                <TableHead className="font-medium text-right">心跳</TableHead>
                <TableHead className="font-medium text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {hosts.map((host) => {
                const isExpanded = expandedRows.has(host.id);

                return (
                  <Fragment key={host.id}>
                    <TableRow
                      key={host.id}
                      className={cn(
                        'cursor-pointer hover:bg-muted/50 transition-colors',
                        isExpanded && 'bg-muted/50'
                      )}
                      onClick={() => toggleRow(host.id)}
                    >
                      {selectable && (
                        <TableCell className="p-3">
                          <input
                            type="checkbox"
                            checked={selectedIds?.has(host.id) ?? false}
                            onClick={(e) => toggleSelect(host.id, e)}
                            onChange={() => {}}
                            className="rounded border-border"
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
                      <TableCell className="p-3 font-medium text-foreground max-w-[200px] truncate" title={host.name ?? ''}>
                        {host.name}
                      </TableCell>
                      <TableCell className="p-3 text-muted-foreground font-mono text-sm">
                        {host.ip}
                      </TableCell>
                      <TableCell className="p-3">
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
                      </TableCell>
                      <TableCell className="p-3 text-center">
                        <span
                          className={cn(
                          'inline-flex items-center justify-center min-w-[32px] px-2 py-0.5 rounded-full text-xs font-medium',
                          (host.device_count || 0) > 0 ? 'bg-primary/10 text-primary' : 'bg-muted/50 text-muted-foreground'
                        )}
                          title={host.claim_hint ?? undefined}
                        >
                          {host.device_count || 0}
                        </span>
                      </TableCell>
                      <TableCell className="p-3 text-center">
                        <span className={cn(
                          'inline-flex items-center justify-center min-w-[32px] px-2 py-0.5 rounded-full text-xs font-medium',
                          (host.active_tasks || 0) > 0 ? 'bg-info/10 text-info' : 'bg-muted/50 text-muted-foreground'
                        )}>
                          {host.active_tasks || 0}
                        </span>
                      </TableCell>
                      <TableCell className="p-3">
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
                      <TableCell className="p-3">
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
                      <TableCell className="p-3">
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
                      <TableCell className="p-3 text-center">
                        <div className="inline-flex items-center gap-2">
                          <span
                            className={cn(
                              'rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap',
                              host.watcher_admin_active !== false
                                ? 'bg-success/10 text-success'
                                : 'bg-destructive/10 text-destructive'
                            )}
                          >
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
                                host.watcher_admin_active !== false
                                  ? 'bg-success'
                                  : 'bg-muted',
                              )}
                            >
                              <span
                                className={cn(
                                  'pointer-events-none inline-block h-4 w-4 rounded-full bg-card shadow-sm',
                                  'ring-0 transition-transform',
                                  host.watcher_admin_active !== false
                                    ? 'translate-x-4'
                                    : 'translate-x-0',
                                )}
                              />
                            </button>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="p-3 text-right text-xs text-muted-foreground">
                        {host.last_heartbeat
                          ? formatLocalTime(host.last_heartbeat)
                          : '-'}
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
                              className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium text-amber-600 bg-amber-500/10 hover:bg-amber-500/20 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
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
                          {isAdmin && onEdit && (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                onEdit(host);
                              }}
                              aria-label={`${host.name ?? host.id} 编辑`}
                              className="inline-flex items-center justify-center p-1 text-muted-foreground hover:text-primary hover:bg-primary/10 rounded transition-colors"
                            >
                              <Pencil className="w-3.5 h-3.5" />
                            </button>
                          )}
                          {isAdmin && onDelete && (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                onDelete(host);
                              }}
                              disabled={isDeleting?.(host.id)}
                              aria-label={`${host.name ?? host.id} 删除`}
                              className="inline-flex items-center justify-center p-1 text-muted-foreground hover:text-destructive hover:bg-destructive/10 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>

                    {/* Expanded Details */}
                    {isExpanded && (
                      <TableRow className="bg-muted/50/50 hover:bg-muted/50/50">
                        <TableCell colSpan={selectable ? 13 : 12} className="p-4">
                          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
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

                            {/* Other Info */}
                            <div className="bg-card rounded-lg border border-border p-3">
                              <div className="flex items-center gap-2 mb-2">
                                <Clock className="w-4 h-4 text-muted-foreground" />
                                <span className="text-sm font-medium text-foreground">其他</span>
                              </div>
                              {host.resources ? (
                                <div className="space-y-1">
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
                                </div>
                              ) : (
                                <span className="text-xs text-muted-foreground">无数据</span>
                              )}
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
      </div>
    </TooltipProvider>
  );
}
