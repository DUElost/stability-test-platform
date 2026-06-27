import { useState, useMemo, useEffect, Fragment } from 'react';
import { cn } from '@/lib/utils';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Progress } from '@/components/ui/progress';
import { StatusBadge } from '@/components/ui/status-badge';
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Smartphone,
  Battery,
  Thermometer,
  Activity,
  Wifi,
  WifiOff,
  Clock,
  AlertTriangle,
  CheckCircle2,
  Zap,
  Search,
} from 'lucide-react';
import { ENTITY_STATUS_COLORS } from '@/design-system/colors';
import { FORM, resourceUsageBgClass, resourceUsageTextClass, TEXT } from '@/design-system/tokens';
import { formatDateTimeFull } from '@/utils/format';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';

export type DeviceStatus = 'idle' | 'testing' | 'offline' | 'error';

export interface DeviceTableData {
  id: number;
  serial: string;
  model: string;
  status: DeviceStatus;
  battery_level?: number;
  temperature?: number;
  network_latency?: number | null;
  build_display_id?: string | null;
  host_id?: number;
  host_name?: string | null;
  current_task?: string;
  last_seen?: string;
}

interface ExpandableDeviceTableProps {
  devices: DeviceTableData[];
  onViewMetrics?: (device: DeviceTableData) => void;
}

export function ExpandableDeviceTable({ devices, onViewMetrics }: ExpandableDeviceTableProps) {
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set());
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [currentPage, setCurrentPage] = useState(1);
  const pageSize = 50;

  // 防抖搜索，减少不必要的过滤计算
  const debouncedSearch = useDebouncedValue(searchQuery, 300);

  const toggleRow = (id: number) => {
    const newExpanded = new Set(expandedRows);
    if (newExpanded.has(id)) {
      newExpanded.delete(id);
    } else {
      newExpanded.add(id);
    }
    setExpandedRows(newExpanded);
  };

  const filteredDevices = useMemo(() => {
    return devices.filter(device => {
      if (statusFilter !== 'all' && device.status !== statusFilter) return false;
      if (debouncedSearch) {
        const query = debouncedSearch.toLowerCase();
        return (
          device.model?.toLowerCase().includes(query) ||
          device.serial.toLowerCase().includes(query) ||
          device.host_name?.toLowerCase().includes(query)
        );
      }
      return true;
    });
  }, [devices, statusFilter, debouncedSearch]);

  // Reset to page 1 when filter or search changes
  useEffect(() => {
    setCurrentPage(1);
  }, [statusFilter, debouncedSearch]);

  const totalPages = Math.ceil(filteredDevices.length / pageSize);
  const paginatedDevices = filteredDevices.slice(
    (currentPage - 1) * pageSize,
    currentPage * pageSize
  );

  const stats = useMemo(() => ({
    total: devices.length,
    idle: devices.filter(d => d.status === 'idle').length,
    testing: devices.filter(d => d.status === 'testing').length,
    offline: devices.filter(d => d.status === 'offline').length,
    error: devices.filter(d => d.status === 'error').length,
  }), [devices]);

  return (
    <div className="space-y-4">
      {/* Summary Stats */}
      <div className="grid grid-cols-5 gap-3">
        <button
          onClick={() => setStatusFilter('all')}
          className={cn(
            'bg-card rounded-lg border p-3 flex items-center gap-3 transition-all',
            statusFilter === 'all' ? 'border-muted-foreground shadow-sm' : 'border-border'
          )}
        >
          <div className="w-10 h-10 rounded-lg bg-muted/50 flex items-center justify-center">
            <Smartphone className="w-5 h-5 text-muted-foreground" />
          </div>
          <div>
            <div className="text-xl font-semibold text-foreground">{stats.total}</div>
            <div className="text-xs text-muted-foreground">全部设备</div>
          </div>
        </button>
        <button
          onClick={() => setStatusFilter('idle')}
          className={cn(
            'bg-card rounded-lg border p-3 flex items-center gap-3 transition-all',
            statusFilter === 'idle'
              ? 'border-success shadow-md bg-success/5'
              : 'border-success/20 hover:border-success/40 hover:bg-success/5'
          )}
        >
          <div className="w-10 h-10 rounded-lg bg-success/10 flex items-center justify-center">
            <CheckCircle2 className={`w-5 h-5 ${ENTITY_STATUS_COLORS.device.idle}`} />
          </div>
          <div>
            <div className={`text-xl font-semibold ${ENTITY_STATUS_COLORS.device.idle}`}>{stats.idle}</div>
            <div className="text-xs text-muted-foreground">空闲</div>
          </div>
        </button>
        <button
          onClick={() => setStatusFilter('testing')}
          className={cn(
            'bg-card rounded-lg border p-3 flex items-center gap-3 transition-all',
            statusFilter === 'testing'
              ? 'border-primary shadow-md bg-primary/5'
              : 'border-primary/20 hover:border-primary/40 hover:bg-primary/5'
          )}
        >
          <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center">
            <Zap className={`w-5 h-5 ${ENTITY_STATUS_COLORS.device.testing}`} />
          </div>
          <div>
            <div className={`text-xl font-semibold ${ENTITY_STATUS_COLORS.device.testing}`}>{stats.testing}</div>
            <div className="text-xs text-muted-foreground">测试中</div>
          </div>
        </button>
        <button
          onClick={() => setStatusFilter('offline')}
          className={cn(
            'bg-card rounded-lg border p-3 flex items-center gap-3 transition-all',
            statusFilter === 'offline' ? 'border-muted-foreground shadow-sm' : 'border-border'
          )}
        >
          <div className="w-10 h-10 rounded-lg bg-muted/50 flex items-center justify-center">
            <WifiOff className="w-5 h-5 text-muted-foreground" />
          </div>
          <div>
            <div className="text-xl font-semibold text-muted-foreground">{stats.offline}</div>
            <div className="text-xs text-muted-foreground">离线</div>
          </div>
        </button>
        <button
          onClick={() => setStatusFilter('error')}
          className={cn(
            'bg-card rounded-lg border p-3 flex items-center gap-3 transition-all',
            statusFilter === 'error'
              ? 'border-destructive shadow-md bg-destructive/5'
              : 'border-destructive/20 hover:border-destructive/40 hover:bg-destructive/5'
          )}
        >
          <div className="w-10 h-10 rounded-lg bg-destructive/10 flex items-center justify-center">
            <AlertTriangle className={`w-5 h-5 ${ENTITY_STATUS_COLORS.device.error}`} />
          </div>
          <div>
            <div className={`text-xl font-semibold ${ENTITY_STATUS_COLORS.device.error}`}>{stats.error}</div>
            <div className="text-xs text-muted-foreground">错误</div>
          </div>
        </button>
      </div>

      {/* Search */}
      <div className="relative">
        <Search className={cn('absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 pointer-events-none', TEXT.subtitle)} />
        <input
          id="device-search"
          name="device-search"
          aria-label="搜索设备"
          type="text"
          placeholder="搜索设备序列号/型号/主机..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className={cn('w-full pl-9', FORM.inputSm)}
        />
      </div>

      {/* Table */}
      <div className="bg-card rounded-xl border border-border overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/50 hover:bg-muted/50">
              <TableHead className="w-10"></TableHead>
              <TableHead className="font-medium">序列号</TableHead>
              <TableHead className="font-medium">型号</TableHead>
              <TableHead className="font-medium">版本</TableHead>
              <TableHead className="font-medium">状态</TableHead>
              <TableHead className="font-medium">所属主机</TableHead>
              <TableHead className="font-medium text-right">最后活跃</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {paginatedDevices.map((device) => {
              const isExpanded = expandedRows.has(device.id);

              return (
                <Fragment key={device.id}>
                  <TableRow
                    key={device.id}
                    className={cn(
                      'cursor-pointer hover:bg-muted/50 transition-colors',
                      isExpanded && 'bg-muted/50'
                    )}
                    onClick={() => toggleRow(device.id)}
                  >
                    <TableCell className="p-3">
                      <ChevronDown
                        className={cn(
                          'w-4 h-4 text-muted-foreground transition-transform',
                          !isExpanded && '-rotate-90'
                        )}
                      />
                    </TableCell>
                    <TableCell className="p-3 font-mono text-sm text-foreground">
                      {device.serial}
                    </TableCell>
                    <TableCell className="p-3 font-medium text-foreground max-w-[160px] truncate" title={device.model ?? ''}>
                      {device.model}
                    </TableCell>
                    <TableCell className="p-3 text-xs text-muted-foreground font-mono whitespace-nowrap">
                      {device.build_display_id || '-'}
                    </TableCell>
                    <TableCell className="p-3">
                      <StatusBadge kind="device-ui" status={device.status} size="sm" />
                    </TableCell>
                    <TableCell className="p-3 text-muted-foreground text-sm">
                      {device.host_name || '-'}
                    </TableCell>
                    <TableCell className="p-3 text-right text-xs text-muted-foreground">
                      {device.last_seen
                        ? formatDateTimeFull(device.last_seen)
                        : '-'}
                    </TableCell>
                  </TableRow>

                  {/* Expanded Details */}
                  {isExpanded && (
                    <TableRow className="bg-muted/50/50 hover:bg-muted/50/50">
                      <TableCell colSpan={7} className="p-4">
                        <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
                          {/* Device Info */}
                          <div className="bg-card rounded-lg border border-border p-3">
                            <div className="flex items-center gap-2 mb-2">
                              <Smartphone className="w-4 h-4 text-muted-foreground" />
                              <span className="text-sm font-medium text-foreground">设备信息</span>
                            </div>
                            <div className="space-y-1 text-xs">
                              <div className="flex justify-between">
                                <span className="text-muted-foreground">设备ID</span>
                                <span className="font-mono text-foreground">{device.id}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-muted-foreground">序列号</span>
                                <span className="font-mono text-foreground">{device.serial}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-muted-foreground">型号</span>
                                <span className="text-foreground">{device.model}</span>
                              </div>
                            </div>
                          </div>

                          {/* Battery */}
                          <div className="bg-card rounded-lg border border-border p-3">
                            <div className="flex items-center gap-2 mb-2">
                              <Battery className="w-4 h-4 text-muted-foreground" />
                              <span className="text-sm font-medium text-foreground">电量</span>
                            </div>
                            <div className="space-y-2">
                              <div className="flex justify-between text-xs mb-1">
                                <span className="text-muted-foreground">当前电量</span>
                                <span className={cn(
                                  'font-mono font-medium',
                                  resourceUsageTextClass(100 - (device.battery_level ?? 0)),
                                )}>
                                  {device.battery_level ?? 0}%
                                </span>
                              </div>
                              <Progress
                                value={device.battery_level ?? 0}
                                className="h-2"
                                indicatorClassName={resourceUsageBgClass(100 - (device.battery_level ?? 0))}
                              />
                            </div>
                          </div>

                          {/* Temperature */}
                          <div className="bg-card rounded-lg border border-border p-3">
                            <div className="flex items-center gap-2 mb-2">
                              <Thermometer className="w-4 h-4 text-muted-foreground" />
                              <span className="text-sm font-medium text-foreground">温度</span>
                            </div>
                            <div className="flex items-center gap-2">
                              <span className={cn(
                                'text-2xl font-semibold font-mono',
                                (device.temperature ?? 0) > 45 ? 'text-destructive' :
                                (device.temperature ?? 0) > 40 ? 'text-warning' : 'text-foreground'
                              )}>
                                {device.temperature ?? '-'}
                              </span>
                              {device.temperature != null && <span className="text-sm text-muted-foreground">°C</span>}
                            </div>
                          </div>

                          {/* Network */}
                          <div className="bg-card rounded-lg border border-border p-3">
                            <div className="flex items-center gap-2 mb-2">
                              {device.network_latency != null ? (
                                <Wifi className="w-4 h-4 text-success" />
                              ) : (
                                <WifiOff className="w-4 h-4 text-muted-foreground" />
                              )}
                              <span className="text-sm font-medium text-foreground">网络延迟</span>
                            </div>
                            <div className="flex items-center gap-2">
                              <span className="text-2xl font-semibold font-mono text-foreground">
                                {device.network_latency != null ? device.network_latency : '-'}
                              </span>
                              {device.network_latency != null && <span className="text-sm text-muted-foreground">ms</span>}
                            </div>
                          </div>

                          {/* Current Task & Actions */}
                          <div className="bg-card rounded-lg border border-border p-3">
                            <div className="flex items-center gap-2 mb-2">
                              <Clock className="w-4 h-4 text-muted-foreground" />
                              <span className="text-sm font-medium text-foreground">当前任务</span>
                            </div>
                            <div className="text-xs text-muted-foreground">
                              {device.current_task || '无任务'}
                            </div>
                            {onViewMetrics && (
                              <button
                                onClick={(e) => { e.stopPropagation(); onViewMetrics(device); }}
                                className="mt-2 text-xs text-primary hover:text-primary/80 flex items-center gap-1"
                              >
                                <Activity className="w-3 h-3" />
                                查看指标历史
                              </button>
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

        {/* Pagination */}
        {filteredDevices.length > pageSize && (
          <div className="p-3 border-t border-border flex items-center justify-between">
            <div className="text-xs text-muted-foreground">
              显示第 {(currentPage - 1) * pageSize + 1} - {Math.min(currentPage * pageSize, filteredDevices.length)} 条，
              共 {filteredDevices.length} 条设备
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                disabled={currentPage === 1}
                aria-label="上一页"
                className="p-1.5 rounded-md border border-border hover:bg-muted/50 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <ChevronLeft className="w-4 h-4 text-muted-foreground" />
              </button>
              <span className="text-xs text-muted-foreground">
                {currentPage} / {totalPages}
              </span>
              <button
                onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
                disabled={currentPage === totalPages}
                aria-label="下一页"
                className="p-1.5 rounded-md border border-border hover:bg-muted/50 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <ChevronRight className="w-4 h-4 text-muted-foreground" />
              </button>
            </div>
          </div>
        )}

        {filteredDevices.length === 0 && (
          <div className="p-8 text-center text-muted-foreground">
            未找到匹配条件的设备
          </div>
        )}
      </div>
    </div>
  );
}
