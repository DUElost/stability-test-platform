import { useState, useMemo, useEffect } from 'react';
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
} from 'lucide-react';

export type DeviceStatus = 'idle' | 'testing' | 'offline' | 'error';

export interface DeviceTableData {
  id: number;
  serial: string;
  model: string;
  status: DeviceStatus;
  battery_level?: number;
  temperature?: number;
  network_latency?: number | null;
  host_id?: number;
  host_name?: string | null;
  current_task?: string;
  last_seen?: string;
}

interface ExpandableDeviceTableProps {
  devices: DeviceTableData[];
}

const statusConfig = {
  idle: { label: '空闲', variant: 'success' as const, icon: CheckCircle2, bgColor: 'bg-emerald-50', textColor: 'text-emerald-600' },
  testing: { label: '测试中', variant: 'default' as const, icon: Zap, bgColor: 'bg-blue-50', textColor: 'text-blue-600' },
  offline: { label: '离线', variant: 'secondary' as const, icon: WifiOff, bgColor: 'bg-gray-50', textColor: 'text-gray-500' },
  error: { label: '错误', variant: 'destructive' as const, icon: AlertTriangle, bgColor: 'bg-red-50', textColor: 'text-red-600' },
};

export function ExpandableDeviceTable({ devices }: ExpandableDeviceTableProps) {
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set());
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [currentPage, setCurrentPage] = useState(1);
  const pageSize = 50;

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
      if (searchQuery) {
        const query = searchQuery.toLowerCase();
        return (
          device.model?.toLowerCase().includes(query) ||
          device.serial.toLowerCase().includes(query) ||
          device.host_name?.toLowerCase().includes(query)
        );
      }
      return true;
    });
  }, [devices, statusFilter, searchQuery]);

  // Reset to page 1 when filter or search changes
  useEffect(() => {
    setCurrentPage(1);
  }, [statusFilter, searchQuery]);

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
            'bg-white rounded-lg border p-3 flex items-center gap-3 transition-all',
            statusFilter === 'all' ? 'border-gray-400 shadow-sm' : 'border-gray-200'
          )}
        >
          <div className="w-10 h-10 rounded-lg bg-gray-50 flex items-center justify-center">
            <Smartphone className="w-5 h-5 text-gray-600" />
          </div>
          <div>
            <div className="text-xl font-semibold text-gray-900">{stats.total}</div>
            <div className="text-xs text-gray-500">全部设备</div>
          </div>
        </button>
        <button
          onClick={() => setStatusFilter('idle')}
          className={cn(
            'bg-white rounded-lg border p-3 flex items-center gap-3 transition-all',
            statusFilter === 'idle' ? 'border-emerald-400 shadow-sm' : 'border-emerald-200'
          )}
        >
          <div className="w-10 h-10 rounded-lg bg-emerald-50 flex items-center justify-center">
            <CheckCircle2 className="w-5 h-5 text-emerald-600" />
          </div>
          <div>
            <div className="text-xl font-semibold text-emerald-600">{stats.idle}</div>
            <div className="text-xs text-gray-500">空闲</div>
          </div>
        </button>
        <button
          onClick={() => setStatusFilter('testing')}
          className={cn(
            'bg-white rounded-lg border p-3 flex items-center gap-3 transition-all',
            statusFilter === 'testing' ? 'border-blue-400 shadow-sm' : 'border-blue-200'
          )}
        >
          <div className="w-10 h-10 rounded-lg bg-blue-50 flex items-center justify-center">
            <Zap className="w-5 h-5 text-blue-600" />
          </div>
          <div>
            <div className="text-xl font-semibold text-blue-600">{stats.testing}</div>
            <div className="text-xs text-gray-500">测试中</div>
          </div>
        </button>
        <button
          onClick={() => setStatusFilter('offline')}
          className={cn(
            'bg-white rounded-lg border p-3 flex items-center gap-3 transition-all',
            statusFilter === 'offline' ? 'border-gray-400 shadow-sm' : 'border-gray-200'
          )}
        >
          <div className="w-10 h-10 rounded-lg bg-gray-50 flex items-center justify-center">
            <WifiOff className="w-5 h-5 text-gray-500" />
          </div>
          <div>
            <div className="text-xl font-semibold text-gray-600">{stats.offline}</div>
            <div className="text-xs text-gray-500">离线</div>
          </div>
        </button>
        <button
          onClick={() => setStatusFilter('error')}
          className={cn(
            'bg-white rounded-lg border p-3 flex items-center gap-3 transition-all',
            statusFilter === 'error' ? 'border-red-400 shadow-sm' : 'border-red-200'
          )}
        >
          <div className="w-10 h-10 rounded-lg bg-red-50 flex items-center justify-center">
            <AlertTriangle className="w-5 h-5 text-red-600" />
          </div>
          <div>
            <div className="text-xl font-semibold text-red-600">{stats.error}</div>
            <div className="text-xs text-gray-500">错误</div>
          </div>
        </button>
      </div>

      {/* Search */}
      <div className="bg-white rounded-lg border border-gray-200 p-3">
        <input
          type="text"
          placeholder="搜索设备序列号/型号/主机..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="w-full px-3 py-2 text-sm bg-gray-50 border-0 rounded-lg focus:outline-none focus:ring-1 focus:ring-gray-200"
        />
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl border border-gray-100 overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow className="bg-gray-50 hover:bg-gray-50">
              <TableHead className="w-10"></TableHead>
              <TableHead className="font-medium">序列号</TableHead>
              <TableHead className="font-medium">型号</TableHead>
              <TableHead className="font-medium">状态</TableHead>
              <TableHead className="font-medium">所属主机</TableHead>
              <TableHead className="font-medium">电量</TableHead>
              <TableHead className="font-medium">温度</TableHead>
              <TableHead className="font-medium">网络</TableHead>
              <TableHead className="font-medium text-right">最后活跃</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {paginatedDevices.map((device) => {
              const isExpanded = expandedRows.has(device.id);
              const config = statusConfig[device.status];
              const StatusIcon = config.icon;

              return (
                <>
                  <TableRow
                    key={device.id}
                    className={cn(
                      'cursor-pointer hover:bg-gray-50 transition-colors',
                      isExpanded && 'bg-gray-50'
                    )}
                    onClick={() => toggleRow(device.id)}
                  >
                    <TableCell className="p-3">
                      <ChevronDown
                        className={cn(
                          'w-4 h-4 text-gray-400 transition-transform',
                          !isExpanded && '-rotate-90'
                        )}
                      />
                    </TableCell>
                    <TableCell className="p-3 font-mono text-sm text-gray-700">
                      {device.serial}
                    </TableCell>
                    <TableCell className="p-3 font-medium text-gray-900">
                      {device.model}
                    </TableCell>
                    <TableCell className="p-3">
                      <span className={cn(
                        'inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-xs font-medium',
                        config.bgColor, config.textColor
                      )}>
                        <StatusIcon className="w-3 h-3" />
                        {config.label}
                      </span>
                    </TableCell>
                    <TableCell className="p-3 text-gray-500 text-sm">
                      {device.host_name || '-'}
                    </TableCell>
                    <TableCell className="p-3">
                      {device.status !== 'offline' && device.battery_level !== undefined ? (
                        <div className="flex items-center gap-2">
                          <Battery className={cn(
                            'w-4 h-4',
                            device.battery_level < 20 ? 'text-red-500' :
                            device.battery_level < 50 ? 'text-amber-500' : 'text-emerald-500'
                          )} />
                          <span className={cn(
                            'text-xs font-mono',
                            device.battery_level < 20 ? 'text-red-500' :
                            device.battery_level < 50 ? 'text-amber-500' : 'text-gray-700'
                          )}>
                            {device.battery_level}%
                          </span>
                        </div>
                      ) : (
                        <span className="text-gray-300">-</span>
                      )}
                    </TableCell>
                    <TableCell className="p-3">
                      {device.status !== 'offline' && device.temperature !== undefined ? (
                        <div className="flex items-center gap-2">
                          <Thermometer className={cn(
                            'w-4 h-4',
                            device.temperature > 45 ? 'text-red-500' :
                            device.temperature > 40 ? 'text-amber-500' : 'text-gray-400'
                          )} />
                          <span className={cn(
                            'text-xs font-mono',
                            device.temperature > 45 ? 'text-red-500' :
                            device.temperature > 40 ? 'text-amber-500' : 'text-gray-700'
                          )}>
                            {device.temperature}°C
                          </span>
                        </div>
                      ) : (
                        <span className="text-gray-300">-</span>
                      )}
                    </TableCell>
                    <TableCell className="p-3">
                      {device.status !== 'offline' && device.network_latency !== undefined && device.network_latency !== null ? (
                        <div className="flex items-center gap-2">
                          <Wifi className="w-4 h-4 text-emerald-500" />
                          <span className="text-xs font-mono text-gray-700">
                            {device.network_latency}ms
                          </span>
                        </div>
                      ) : device.status === 'offline' ? (
                        <div className="flex items-center gap-2">
                          <WifiOff className="w-4 h-4 text-gray-400" />
                          <span className="text-xs text-gray-400">离线</span>
                        </div>
                      ) : (
                        <span className="text-gray-300">-</span>
                      )}
                    </TableCell>
                    <TableCell className="p-3 text-right text-xs text-gray-400">
                      {device.last_seen
                        ? new Date(device.last_seen).toLocaleTimeString()
                        : '-'}
                    </TableCell>
                  </TableRow>

                  {/* Expanded Details */}
                  {isExpanded && (
                    <TableRow className="bg-gray-50/50 hover:bg-gray-50/50">
                      <TableCell colSpan={9} className="p-4">
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                          {/* Device Info */}
                          <div className="bg-white rounded-lg border border-gray-100 p-3">
                            <div className="flex items-center gap-2 mb-2">
                              <Smartphone className="w-4 h-4 text-gray-500" />
                              <span className="text-sm font-medium text-gray-700">设备信息</span>
                            </div>
                            <div className="space-y-1 text-xs">
                              <div className="flex justify-between">
                                <span className="text-gray-500">序列号</span>
                                <span className="font-mono text-gray-700">{device.serial}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-gray-500">型号</span>
                                <span className="text-gray-700">{device.model}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-gray-500">设备ID</span>
                                <span className="font-mono text-gray-700">{device.id}</span>
                              </div>
                            </div>
                          </div>

                          {/* Status & Battery */}
                          <div className="bg-white rounded-lg border border-gray-100 p-3">
                            <div className="flex items-center gap-2 mb-2">
                              <Activity className="w-4 h-4 text-gray-500" />
                              <span className="text-sm font-medium text-gray-700">状态与电量</span>
                            </div>
                            <div className="space-y-2">
                              <div>
                                <div className="flex justify-between text-xs mb-1">
                                  <span className="text-gray-500">电量</span>
                                  <span className={cn(
                                    'font-mono',
                                    (device.battery_level ?? 0) < 20 ? 'text-red-500' :
                                    (device.battery_level ?? 0) < 50 ? 'text-amber-500' : 'text-gray-700'
                                  )}>
                                    {device.battery_level ?? 0}%
                                  </span>
                                </div>
                                <Progress
                                  value={device.battery_level ?? 0}
                                  className="h-2"
                                  indicatorClassName={cn(
                                    (device.battery_level ?? 0) < 20 ? 'bg-red-500' :
                                    (device.battery_level ?? 0) < 50 ? 'bg-amber-500' : 'bg-emerald-500'
                                  )}
                                />
                              </div>
                            </div>
                          </div>

                          {/* Current Task */}
                          <div className="bg-white rounded-lg border border-gray-100 p-3">
                            <div className="flex items-center gap-2 mb-2">
                              <Clock className="w-4 h-4 text-gray-500" />
                              <span className="text-sm font-medium text-gray-700">当前任务</span>
                            </div>
                            <div className="text-xs text-gray-500">
                              {device.current_task || '无任务'}
                            </div>
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

        {/* Pagination */}
        {filteredDevices.length > pageSize && (
          <div className="p-3 border-t border-gray-100 flex items-center justify-between">
            <div className="text-xs text-gray-400">
              显示第 {(currentPage - 1) * pageSize + 1} - {Math.min(currentPage * pageSize, filteredDevices.length)} 条，
              共 {filteredDevices.length} 条设备
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                disabled={currentPage === 1}
                className="p-1.5 rounded-md border border-gray-200 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <ChevronLeft className="w-4 h-4 text-gray-600" />
              </button>
              <span className="text-xs text-gray-500">
                {currentPage} / {totalPages}
              </span>
              <button
                onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
                disabled={currentPage === totalPages}
                className="p-1.5 rounded-md border border-gray-200 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <ChevronRight className="w-4 h-4 text-gray-600" />
              </button>
            </div>
          </div>
        )}

        {filteredDevices.length === 0 && (
          <div className="p-8 text-center text-gray-400">
            未找到匹配条件的设备
          </div>
        )}
      </div>
    </div>
  );
}
