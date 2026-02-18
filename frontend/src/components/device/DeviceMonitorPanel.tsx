import { useState, useEffect } from 'react';
import { useWebSocket } from '@/hooks/useWebSocket';
import { DeviceCard, type Device } from './DeviceCard';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  LayoutGrid,
  List,
  RefreshCw,
  Wifi,
  WifiOff,
  Activity,
  Search,
  Filter,
  Smartphone,
  AlertTriangle,
} from 'lucide-react';
import { cn } from '@/lib/utils';

export type DeviceStatus = 'idle' | 'testing' | 'offline' | 'error';

// Stable reconnect config to avoid useWebSocket reconnection loop
const STABLE_RECONNECT_CONFIG = {
  initialDelay: 1000,
  maxDelay: 30000,
  exponent: 2,
  maxRetries: 0, // 无限重试
};

export interface MonitorDevice extends Device {
  lastUpdate?: Date;
  isStale?: boolean;
  last_seen?: string | null;
}

interface DeviceMonitorPanelProps {
  devices: MonitorDevice[];
  onRefresh?: () => void;
  onDeviceClick?: (device: MonitorDevice) => void;
  wsUrl?: string;
  autoRefresh?: boolean;
  refreshInterval?: number;
}

const statusConfig = {
  all: { label: 'All Devices', color: 'bg-primary' },
  idle: { label: 'Idle', color: 'bg-success' },
  testing: { label: 'Testing', color: 'bg-primary' },
  offline: { label: 'Offline', color: 'bg-muted' },
  error: { label: 'Error', color: 'bg-destructive' },
};

type ViewMode = 'grid' | 'list';
type SortBy = 'status' | 'name' | 'lastSeen';

export function DeviceMonitorPanel({
  devices: initialDevices,
  onRefresh,
  onDeviceClick,
  wsUrl,
  autoRefresh = true,
  refreshInterval = 30000,
}: DeviceMonitorPanelProps) {
  const [devices, setDevices] = useState<MonitorDevice[]>(initialDevices);
  const [statusFilter, setStatusFilter] = useState<keyof typeof statusConfig>('all');
  const [viewMode, setViewMode] = useState<ViewMode>('grid');
  const [searchQuery, setSearchQuery] = useState('');
  const [sortBy, setSortBy] = useState<SortBy>('status');
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date());

  // WebSocket for real-time updates
  const { lastMessage, connectionStatus } = useWebSocket(wsUrl || '', {
    enabled: !!wsUrl && autoRefresh,
    reconnectConfig: STABLE_RECONNECT_CONFIG,
  });

  // Update devices when initialDevices change
  useEffect(() => {
    setDevices(initialDevices.map(d => ({ ...d, lastUpdate: new Date() })));
    setLastUpdate(new Date());
  }, [initialDevices]);

  // Handle WebSocket messages
  useEffect(() => {
    if (lastMessage) {
      try {
        const data = lastMessage as any;
        if (data.type === 'device_update') {
          setDevices(prev => {
            const updated = prev.map(device => {
              if (device.id === data.deviceId) {
                return { ...device, ...data.payload, lastUpdate: new Date() };
              }
              return device;
            });
            return updated;
          });
          setLastUpdate(new Date());
        }
      } catch {
        // Ignore invalid messages
      }
    }
  }, [lastMessage]);

  // Check for stale devices
  useEffect(() => {
    const interval = setInterval(() => {
      const now = new Date();
      setDevices(prev =>
        prev.map(device => ({
          ...device,
          isStale: device.lastUpdate
            ? now.getTime() - device.lastUpdate.getTime() > 60000
            : false,
        }))
      );
    }, 10000);

    return () => clearInterval(interval);
  }, []);

  // Auto refresh polling fallback
  useEffect(() => {
    if (!wsUrl && autoRefresh && onRefresh) {
      const interval = setInterval(onRefresh, refreshInterval);
      return () => clearInterval(interval);
    }
  }, [wsUrl, autoRefresh, refreshInterval, onRefresh]);

  const filteredDevices = devices
    .filter(device => {
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
    })
    .sort((a, b) => {
      switch (sortBy) {
        case 'status':
          const statusOrder = { error: 0, testing: 1, idle: 2, offline: 3 };
          return statusOrder[a.status] - statusOrder[b.status];
        case 'name':
          return (a.model || '').localeCompare(b.model || '');
        case 'lastSeen':
          return ((b as any).last_seen || '').localeCompare((a as any).last_seen || '');
        default:
          return 0;
      }
    });

  const stats = {
    total: devices.length,
    idle: devices.filter(d => d.status === 'idle').length,
    testing: devices.filter(d => d.status === 'testing').length,
    offline: devices.filter(d => d.status === 'offline').length,
    error: devices.filter(d => d.status === 'error').length,
  };

  const isConnected = connectionStatus === 'connected';
  const isStale = new Date().getTime() - lastUpdate.getTime() > 60000;

  return (
    <div className="space-y-4">
      {/* Header Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        {Object.entries(statusConfig).map(([key, config]) => {
          const count = key === 'all' ? stats.total : stats[key as keyof typeof stats];
          const isActive = statusFilter === key;
          return (
            <button
              key={key}
              onClick={() => setStatusFilter(key as keyof typeof statusConfig)}
              className={cn(
                'flex items-center justify-between p-3 rounded-lg border transition-all',
                isActive
                  ? 'border-primary bg-primary/5 shadow-sm'
                  : 'border-border bg-card hover:bg-accent/50'
              )}
            >
              <div className="flex items-center gap-2">
                <div className={cn('w-2 h-2 rounded-full', config.color)} />
                <span className="text-sm font-medium">{config.label}</span>
              </div>
              <span className={cn(
                'text-lg font-bold',
                isActive ? 'text-primary' : 'text-muted-foreground'
              )}>
                {count}
              </span>
            </button>
          );
        })}
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3 p-3 bg-card rounded-lg border">
        <div className="flex items-center gap-2 flex-1 min-w-[200px]">
          <Search className="h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search devices..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="h-8 text-sm"
          />
        </div>

        <div className="flex items-center gap-2">
          <Filter className="h-4 w-4 text-muted-foreground" />
          <Select value={sortBy} onValueChange={(v: string) => setSortBy(v as SortBy)}>
            <SelectTrigger className="w-32 h-8 text-xs">
              <SelectValue placeholder="Sort by" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="status">Status</SelectItem>
              <SelectItem value="name">Name</SelectItem>
              <SelectItem value="lastSeen">Last Seen</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="flex items-center gap-1 border-l pl-3">
          <Button
            variant={viewMode === 'grid' ? 'default' : 'ghost'}
            size="sm"
            className="h-8 w-8 p-0"
            onClick={() => setViewMode('grid')}
          >
            <LayoutGrid className="h-4 w-4" />
          </Button>
          <Button
            variant={viewMode === 'list' ? 'default' : 'ghost'}
            size="sm"
            className="h-8 w-8 p-0"
            onClick={() => setViewMode('list')}
          >
            <List className="h-4 w-4" />
          </Button>
        </div>

        {wsUrl && (
          <Badge
            variant={isConnected ? 'default' : 'destructive'}
            className="flex items-center gap-1"
          >
            {isConnected ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
            {isConnected ? 'Live' : 'Offline'}
          </Badge>
        )}

        <Button
          variant="outline"
          size="sm"
          className="h-8"
          onClick={onRefresh}
          disabled={!onRefresh}
        >
          <RefreshCw className={cn('mr-1 h-3 w-3', !isStale && 'animate-spin')} />
          刷新
        </Button>
      </div>

      {/* Stale Warning */}
      {isStale && (
        <div className="flex items-center gap-2 p-3 bg-warning/10 border border-warning/20 rounded-lg text-warning text-sm">
          <AlertTriangle className="h-4 w-4" />
          <span>数据已过期，上次更新: {lastUpdate.toLocaleTimeString()}</span>
        </div>
      )}

      {/* Device Grid/List */}
      {filteredDevices.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
          <Smartphone className="h-12 w-12 mb-4 opacity-50" />
          <p className="text-lg font-medium">暂无设备</p>
          <p className="text-sm">请调整筛选条件</p>
        </div>
      ) : viewMode === 'grid' ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {filteredDevices.map((device) => (
            <DeviceCard
              key={device.id}
              device={device}
              onClick={onDeviceClick}
            />
          ))}
        </div>
      ) : (
        <div className="space-y-2">
          {filteredDevices.map((device) => (
            <DeviceListItem
              key={device.id}
              device={device}
              onClick={() => onDeviceClick?.(device)}
            />
          ))}
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between text-xs text-muted-foreground pt-2">
        <span>
          显示 {filteredDevices.length} / {devices.length} 台设备
        </span>
        <span>上次更新: {lastUpdate.toLocaleTimeString()}</span>
      </div>
    </div>
  );
}

// List view item component
function DeviceListItem({
  device,
  onClick,
}: {
  device: MonitorDevice;
  onClick?: () => void;
}) {
  const statusColors = {
    idle: 'border-l-success bg-success/5',
    testing: 'border-l-primary bg-primary/5',
    offline: 'border-l-muted bg-muted/50',
    error: 'border-l-destructive bg-destructive/5',
  };

  return (
    <div
      onClick={onClick}
      className={cn(
        'flex items-center gap-4 p-3 rounded-lg border border-l-4 cursor-pointer transition-all hover:shadow-md',
        statusColors[device.status],
        device.isStale && 'opacity-60'
      )}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <h4 className="font-medium truncate">{device.model}</h4>
          <Badge variant="outline" className="text-xs">
            {device.status}
          </Badge>
          {device.isStale && (
            <Badge variant="destructive" className="text-xs">
              Stale
            </Badge>
          )}
        </div>
        <p className="text-xs text-muted-foreground font-mono">{device.serial}</p>
      </div>

      <div className="flex items-center gap-6 text-sm">
        {device.host_name && (
          <div className="text-muted-foreground">
            <span className="text-xs">Host:</span> {device.host_name}
          </div>
        )}

        {device.status !== 'offline' && (
          <>
            <div className="flex items-center gap-1">
              <Activity className="h-3 w-3 text-muted-foreground" />
              <span className={cn(
                device.temperature > 40 ? 'text-destructive' : 'text-muted-foreground'
              )}>
                {device.temperature}°C
              </span>
            </div>
            <div className="flex items-center gap-1">
              <span className={cn(
                'text-xs',
                device.battery_level < 20 ? 'text-destructive' : 'text-muted-foreground'
              )}>
                {device.battery_level}%
              </span>
            </div>
          </>
        )}

        <div className="text-xs text-muted-foreground">
          {(device as any).last_seen
            ? new Date((device as any).last_seen).toLocaleTimeString()
            : 'Never'}
        </div>
      </div>
    </div>
  );
}
