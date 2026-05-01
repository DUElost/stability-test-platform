import { useState, useMemo } from 'react';
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
import { ChevronDown, Server, Cpu, HardDrive, MemoryStick, Clock, Activity, AlertTriangle, CheckCircle2 } from 'lucide-react';

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
  id: number;
  name: string;
  ip: string;
  status: 'ONLINE' | 'OFFLINE' | 'DEGRADED';
  last_heartbeat?: string;
  resources?: HostResources;
  mount_status?: MountStatus[];
  device_count?: number;
  active_tasks?: number;
  // ADR-0019 Phase 3c: structured capacity/health
  max_concurrent_jobs?: number;
  effective_slots?: number;
  health_status?: 'HEALTHY' | 'DEGRADED' | 'UNSCHEDULABLE';
  health_reasons?: string[];
}

interface ExpandableHostTableProps {
  hosts: HostTableData[];
  onDeploy?: (hostId: number) => void;
  isDeploying?: (hostId: number) => boolean;
  selectedIds?: Set<number>;
  onSelectionChange?: (ids: Set<number>) => void;
}

const statusConfig = {
  ONLINE: { label: '在线', variant: 'success' as const, icon: CheckCircle2, bgColor: 'bg-emerald-50', textColor: 'text-emerald-600', borderColor: 'border-emerald-200' },
  OFFLINE: { label: '离线', variant: 'secondary' as const, icon: AlertTriangle, bgColor: 'bg-gray-50', textColor: 'text-gray-500', borderColor: 'border-gray-200' },
  DEGRADED: { label: '告警', variant: 'warning' as const, icon: AlertTriangle, bgColor: 'bg-amber-50', textColor: 'text-amber-600', borderColor: 'border-amber-200' },
};

function formatBytes(gb: number): string {
  if (gb >= 1024) return `${(gb / 1024).toFixed(1)} TB`;
  return `${gb.toFixed(1)} GB`;
}

function formatDuration(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function getResourceColor(percentage: number): string {
  if (percentage >= 90) return 'text-red-600';
  if (percentage >= 70) return 'text-amber-500';
  return 'text-emerald-500';
}

const REASON_LABELS: Record<string, string> = {
  cpu_high: 'CPU 过高',
  ram_high: '内存过高',
  disk_high: '磁盘过高',
  mount_failed: '挂载失败',
  adb_low_healthy_devices: '无健康设备',
};


function getProgressColor(percentage: number): string {
  if (percentage >= 90) return 'bg-red-500';
  if (percentage >= 70) return 'bg-amber-500';
  return 'bg-emerald-500';
}

export function ExpandableHostTable({ hosts, onDeploy: _onDeploy, isDeploying: _isDeploying, selectedIds, onSelectionChange }: ExpandableHostTableProps) {
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set());
  const selectable = !!onSelectionChange;

  const toggleSelect = (id: number, e: React.MouseEvent) => {
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

  const toggleRow = (id: number) => {
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
          <div className="bg-white rounded-lg border border-gray-200 p-3 flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-gray-50 flex items-center justify-center">
              <Server className="w-5 h-5 text-gray-600" />
            </div>
            <div>
              <div className="text-xl font-semibold text-gray-900">{stats.total}</div>
              <div className="text-xs text-gray-500">主机总数</div>
            </div>
          </div>
          <div className="bg-white rounded-lg border border-emerald-200 p-3 flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-emerald-50 flex items-center justify-center">
              <CheckCircle2 className="w-5 h-5 text-emerald-600" />
            </div>
            <div>
              <div className="text-xl font-semibold text-emerald-600">{stats.online}</div>
              <div className="text-xs text-gray-500">在线</div>
            </div>
          </div>
          <div className="bg-white rounded-lg border border-amber-200 p-3 flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-amber-50 flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-amber-600" />
            </div>
            <div>
              <div className="text-xl font-semibold text-amber-600">{stats.degraded}</div>
              <div className="text-xs text-gray-500">告警</div>
            </div>
          </div>
          <div className="bg-white rounded-lg border border-gray-200 p-3 flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-gray-50 flex items-center justify-center">
              <Activity className="w-5 h-5 text-gray-500" />
            </div>
            <div>
              <div className="text-xl font-semibold text-gray-600">{stats.offline}</div>
              <div className="text-xs text-gray-500">离线</div>
            </div>
          </div>
        </div>

        {/* Table */}
        <div className="bg-white rounded-xl border border-gray-100 overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow className="bg-gray-50 hover:bg-gray-50">
                {selectable && (
                  <TableHead className="w-10 p-3">
                    <input
                      type="checkbox"
                      checked={selectedIds ? selectedIds.size === hosts.length && hosts.length > 0 : false}
                      onChange={toggleAll}
                      className="rounded border-gray-300"
                    />
                  </TableHead>
                )}
                <TableHead className="w-10"></TableHead>
                <TableHead className="font-medium">主机名称</TableHead>
                <TableHead className="font-medium">IP地址</TableHead>
                <TableHead className="font-medium">状态</TableHead>
                <TableHead className="font-medium text-center">槽位</TableHead>
                <TableHead className="font-medium text-center">设备数</TableHead>
                <TableHead className="font-medium text-center">任务数</TableHead>
                <TableHead className="font-medium">CPU</TableHead>
                <TableHead className="font-medium">内存</TableHead>
                <TableHead className="font-medium">磁盘</TableHead>
                <TableHead className="font-medium text-right">心跳</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {hosts.map((host) => {
                const isExpanded = expandedRows.has(host.id);
                const config = statusConfig[host.status];
                const StatusIcon = config.icon;

                return (
                  <>
                    <TableRow
                      key={host.id}
                      className={cn(
                        'cursor-pointer hover:bg-gray-50 transition-colors',
                        isExpanded && 'bg-gray-50'
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
                            className="rounded border-gray-300"
                          />
                        </TableCell>
                      )}
                      <TableCell className="p-3">
                        <ChevronDown
                          className={cn(
                            'w-4 h-4 text-gray-400 transition-transform',
                            !isExpanded && '-rotate-90'
                          )}
                        />
                      </TableCell>
                      <TableCell className="p-3 font-medium text-gray-900">
                        {host.name}
                      </TableCell>
                      <TableCell className="p-3 text-gray-500 font-mono text-sm">
                        {host.ip}
                      </TableCell>
                      <TableCell className="p-3">
                        <div className="flex items-center gap-1.5">
                          <span className={cn(
                            'inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-xs font-medium',
                            config.bgColor, config.textColor
                          )}>
                            <StatusIcon className="w-3 h-3" />
                            {config.label}
                          </span>
                          {host.health_status && host.health_status !== 'HEALTHY' && (
                            <span
                              className={cn(
                                'inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-xs font-medium cursor-help',
                                host.health_status === 'UNSCHEDULABLE'
                                  ? 'bg-red-50 text-red-600'
                                  : 'bg-amber-50 text-amber-600'
                              )}
                              title={host.health_reasons?.map(r => REASON_LABELS[r] || r).join(', ') || ''}
                            >
                              {host.health_status === 'UNSCHEDULABLE' ? '禁调' : '降级'}
                            </span>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="p-3 text-center">
                        {host.max_concurrent_jobs != null ? (
                          <span className="text-xs font-mono">
                            <span className={cn(
                              (host.effective_slots ?? 0) > 0 ? 'text-emerald-600' : 'text-gray-400'
                            )}>
                              {host.effective_slots ?? 0}
                            </span>
                            <span className="text-gray-300">/{host.max_concurrent_jobs}</span>
                          </span>
                        ) : (
                          <span className="text-gray-300">-</span>
                        )}
                      </TableCell>
                      <TableCell className="p-3 text-center">
                        <span className={cn(
                          'inline-flex items-center justify-center min-w-[32px] px-2 py-0.5 rounded-full text-xs font-medium',
                          (host.device_count || 0) > 0 ? 'bg-blue-50 text-blue-600' : 'bg-gray-50 text-gray-400'
                        )}>
                          {host.device_count || 0}
                        </span>
                      </TableCell>
                      <TableCell className="p-3 text-center">
                        <span className={cn(
                          'inline-flex items-center justify-center min-w-[32px] px-2 py-0.5 rounded-full text-xs font-medium',
                          (host.active_tasks || 0) > 0 ? 'bg-purple-50 text-purple-600' : 'bg-gray-50 text-gray-400'
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
                          <span className="text-gray-300">-</span>
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
                          <span className="text-gray-300">-</span>
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
                          <span className="text-gray-300">-</span>
                        )}
                      </TableCell>
                      <TableCell className="p-3 text-right text-xs text-gray-400">
                        {host.last_heartbeat
                          ? new Date(host.last_heartbeat).toLocaleTimeString()
                          : '-'}
                      </TableCell>
                    </TableRow>

                    {/* Expanded Details */}
                    {isExpanded && (
                      <TableRow className="bg-gray-50/50 hover:bg-gray-50/50">
                        <TableCell colSpan={selectable ? 12 : 11} className="p-4">
                          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                            {/* CPU Details */}
                            <div className="bg-white rounded-lg border border-gray-100 p-3">
                              <div className="flex items-center gap-2 mb-2">
                                <Cpu className="w-4 h-4 text-gray-500" />
                                <span className="text-sm font-medium text-gray-700">CPU</span>
                              </div>
                              {host.resources ? (
                                <div className="space-y-1">
                                  <div className="flex justify-between text-xs">
                                    <span className="text-gray-500">负载</span>
                                    <span className={cn('font-mono', getResourceColor(host.resources.cpu_load))}>
                                      {host.resources.cpu_load.toFixed(1)}%
                                    </span>
                                  </div>
                                  {host.resources.cpu_cores && (
                                    <div className="flex justify-between text-xs">
                                      <span className="text-gray-500">核心数</span>
                                      <span className="font-mono text-gray-700">{host.resources.cpu_cores}</span>
                                    </div>
                                  )}
                                </div>
                              ) : (
                                <span className="text-xs text-gray-400">无数据</span>
                              )}
                            </div>

                            {/* Memory Details */}
                            <div className="bg-white rounded-lg border border-gray-100 p-3">
                              <div className="flex items-center gap-2 mb-2">
                                <MemoryStick className="w-4 h-4 text-gray-500" />
                                <span className="text-sm font-medium text-gray-700">内存</span>
                              </div>
                              {host.resources ? (
                                <div className="space-y-1">
                                  <div className="flex justify-between text-xs">
                                    <span className="text-gray-500">使用率</span>
                                    <span className={cn('font-mono', getResourceColor(host.resources.ram_usage))}>
                                      {host.resources.ram_usage.toFixed(1)}%
                                    </span>
                                  </div>
                                  {host.resources.ram_total_gb && (
                                    <div className="flex justify-between text-xs">
                                      <span className="text-gray-500">总量</span>
                                      <span className="font-mono text-gray-700">{formatBytes(host.resources.ram_total_gb)}</span>
                                    </div>
                                  )}
                                </div>
                              ) : (
                                <span className="text-xs text-gray-400">无数据</span>
                              )}
                            </div>

                            {/* Disk Details */}
                            <div className="bg-white rounded-lg border border-gray-100 p-3">
                              <div className="flex items-center gap-2 mb-2">
                                <HardDrive className="w-4 h-4 text-gray-500" />
                                <span className="text-sm font-medium text-gray-700">磁盘</span>
                              </div>
                              {host.resources ? (
                                <div className="space-y-1">
                                  <div className="flex justify-between text-xs">
                                    <span className="text-gray-500">使用率</span>
                                    <span className={cn('font-mono', getResourceColor(host.resources.disk_usage))}>
                                      {host.resources.disk_usage.toFixed(1)}%
                                    </span>
                                  </div>
                                  {host.resources.disk_total_gb && (
                                    <div className="flex justify-between text-xs">
                                      <span className="text-gray-500">总量</span>
                                      <span className="font-mono text-gray-700">{formatBytes(host.resources.disk_total_gb)}</span>
                                    </div>
                                  )}
                                </div>
                              ) : (
                                <span className="text-xs text-gray-400">无数据</span>
                              )}
                            </div>

                            {/* Other Info */}
                            <div className="bg-white rounded-lg border border-gray-100 p-3">
                              <div className="flex items-center gap-2 mb-2">
                                <Clock className="w-4 h-4 text-gray-500" />
                                <span className="text-sm font-medium text-gray-700">其他</span>
                              </div>
                              {host.resources ? (
                                <div className="space-y-1">
                                  {host.resources.temperature !== undefined && (
                                    <div className="flex justify-between text-xs">
                                      <span className="text-gray-500">温度</span>
                                      <span className={cn(
                                        'font-mono',
                                        host.resources.temperature > 80 ? 'text-red-600' :
                                        host.resources.temperature > 60 ? 'text-amber-500' : 'text-gray-700'
                                      )}>
                                        {host.resources.temperature.toFixed(1)}°C
                                      </span>
                                    </div>
                                  )}
                                  {host.resources.uptime_seconds !== undefined && (
                                    <div className="flex justify-between text-xs">
                                      <span className="text-gray-500">运行时间</span>
                                      <span className="font-mono text-gray-700">{formatDuration(host.resources.uptime_seconds)}</span>
                                    </div>
                                  )}
                                </div>
                              ) : (
                                <span className="text-xs text-gray-400">无数据</span>
                              )}
                            </div>
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </div>
    </TooltipProvider>
  );
}
