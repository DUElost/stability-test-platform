import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { HostCard, Host } from '../components/network/HostCard';
import { Device } from '../components/device/DeviceCard';
import { DeviceGrid } from '../components/device/DeviceGrid';
import { PageContainer } from '../components/layout';
import { useRealtimeDashboard } from '../hooks/useRealtimeDashboard';
import { api } from '../utils/api';
import { WS_DASHBOARD_ENDPOINT } from '../config';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Wifi, WifiOff, Clock, Server, Smartphone, Activity, AlertCircle, CheckCircle2, Search, BarChart3 } from 'lucide-react';
import { DeviceStatusChart, HostResourceChart, ActivityChart } from '@/components/charts';
import { type HostDTO, type DeviceDTO, mapHostToViewModel, mapDeviceToViewModel } from '@/mappers';

const hostStatusMap: Record<string, Host['status']> = {
  'ONLINE': 'online',
  'OFFLINE': 'offline',
  'DEGRADED': 'warning'
};

function toComponentHost(host: HostDTO): Host {
  const viewModel = mapHostToViewModel(host);
  return {
    ip: viewModel.ip,
    status: hostStatusMap[viewModel.status] || 'offline',
    cpu_load: viewModel.extra?.cpu_load as number || 0,
    ram_usage: viewModel.extra?.ram_usage as number || 0,
    disk_usage: (viewModel.extra?.disk_usage as { usage_percent?: number })?.usage_percent || 0,
    mount_status: viewModel.mountStatusOk,
  };
}

const deviceStatusMap: Record<string, Device['status']> = {
  'ONLINE': 'idle',
  'BUSY': 'testing',
  'OFFLINE': 'offline',
  'ERROR': 'error'
};

function toComponentDevice(device: DeviceDTO): Device {
  const viewModel = mapDeviceToViewModel(device);
  return {
    serial: viewModel.serial,
    model: viewModel.model || 'Unknown',
    status: deviceStatusMap[viewModel.status] || 'offline',
    battery_level: viewModel.battery_level ?? 0,
    temperature: viewModel.temperature ?? 0,
    network_latency: viewModel.network_latency ?? null,
  };
}

function StatCard({
  title,
  value,
  suffix,
  icon: Icon,
  color,
  isLoading
}: {
  title: string;
  value: string | number;
  suffix?: string;
  icon: React.ElementType;
  color: 'primary' | 'success' | 'destructive' | 'warning';
  isLoading?: boolean;
}) {
  const colorClasses = {
    primary: 'bg-primary/10 text-primary border-primary/20',
    success: 'bg-success/10 text-success border-success/20',
    destructive: 'bg-destructive/10 text-destructive border-destructive/20',
    warning: 'bg-warning/10 text-warning border-warning/20',
  };

  return (
    <Card className="border-l-4 border-l-transparent hover:shadow-md transition-shadow">
      <CardContent className="p-4">
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <p className="text-xs text-muted-foreground uppercase tracking-wider mb-1">{title}</p>
            {isLoading ? (
              <Skeleton className="h-8 w-16" />
            ) : (
              <div className="flex items-baseline gap-1">
                <span className="text-2xl font-bold text-card-foreground">{value}</span>
                {suffix && <span className="text-sm text-muted-foreground">{suffix}</span>}
              </div>
            )}
          </div>
          <div className={`p-2 rounded-lg border ${colorClasses[color]}`}>
            <Icon size={20} />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export default function Dashboard() {
  const [filterText, setFilterText] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');

  const { data: hosts, isLoading: hostsLoading, error: hostsError } = useQuery({
    queryKey: ['hosts'],
    queryFn: () => api.hosts.list().then(res => res.data),
    refetchInterval: 5000,
  });

  const {
    devices: rawDevices,
    isConnected: wsConnected,
    lastUpdateTime,
    isLoading: devicesLoading,
    isError: devicesError
  } = useRealtimeDashboard(WS_DASHBOARD_ENDPOINT);

  const devices = useMemo(() => {
    if (!rawDevices || rawDevices.length === 0) return [];
    return rawDevices.map(toComponentDevice);
  }, [rawDevices]);

  const stats = useMemo(() => ({
    total: devices.length,
    online: devices.filter((d: Device) => d.status === 'idle').length,
    offline: devices.filter((d: Device) => d.status === 'offline').length,
    testing: devices.filter((d: Device) => d.status === 'testing').length,
    error: devices.filter((d: Device) => d.status === 'error').length,
    lowBattery: devices.filter((d: Device) => d.battery_level < 20).length,
    highTemp: devices.filter((d: Device) => d.temperature > 45).length,
  }), [devices]);

  const filteredDevices = useMemo(() => devices.filter((d: Device) => {
    const matchesStatus = statusFilter === 'all' || d.status === statusFilter;
    const matchesSearch = d.serial.toLowerCase().includes(filterText.toLowerCase()) ||
                          d.model.toLowerCase().includes(filterText.toLowerCase());
    return matchesStatus && matchesSearch;
  }), [devices, statusFilter, filterText]);

  const isLoading = hostsLoading || devicesLoading;
  const hasError = hostsError || devicesError;

  // Calculate alerts count
  const alertsCount = stats.error + stats.lowBattery + stats.highTemp;
  const alertsLabel = alertsCount > 0 ? alertsCount : 'All Good';

  // Prepare chart data
  const deviceStatusData = useMemo(() => ({
    idle: stats.online,
    testing: stats.testing,
    offline: stats.offline,
    error: stats.error,
  }), [stats]);

  const hostResourceData = useMemo(() => {
    if (!hosts) return [];
    return (hosts as HostDTO[]).map((host) => ({
      ip: host.ip,
      cpu_load: (host.extra?.cpu_load as number) || 0,
      ram_usage: (host.extra?.ram_usage as number) || 0,
      disk_usage: (host.extra?.disk_usage as { usage_percent?: number })?.usage_percent || 0,
    }));
  }, [hosts]);

  if (hasError) {
    return (
      <PageContainer>
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="p-6">
            <div className="flex items-center gap-3 text-destructive">
              <AlertCircle size={24} />
              <div>
                <h3 className="font-semibold">Error loading data</h3>
                <p className="text-sm text-destructive/80">Please check backend connection.</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </PageContainer>
    );
  }

  return (
    <PageContainer>
      {/* Connection Status Bar */}
      <div className="flex justify-between items-center text-sm px-1 mb-4">
        <div className="flex items-center gap-2">
          <Badge
            variant={wsConnected ? 'success' : 'destructive'}
            className="gap-1.5"
          >
            {wsConnected ? <Wifi size={12} /> : <WifiOff size={12} />}
            {wsConnected ? 'Realtime Connected' : 'Disconnected'}
          </Badge>
        </div>
        <div className="flex items-center gap-2 text-muted-foreground">
          <Clock size={14} />
          <span>Updated: {lastUpdateTime.toLocaleTimeString()}</span>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard
          title="Total Hosts"
          value={hosts?.length || 0}
          icon={Server}
          color="primary"
          isLoading={isLoading}
        />
        <StatCard
          title="Online Devices"
          value={stats.online}
          suffix={`/ ${stats.total}`}
          icon={CheckCircle2}
          color="success"
          isLoading={isLoading}
        />
        <StatCard
          title="Active Testing"
          value={stats.testing}
          icon={Activity}
          color="primary"
          isLoading={isLoading}
        />
        <StatCard
          title="Alerts"
          value={alertsLabel}
          icon={AlertCircle}
          color={alertsCount > 0 ? 'destructive' : 'success'}
          isLoading={isLoading}
        />
      </div>

      {/* Charts Section */}
      <div className="mb-6">
        <div className="flex items-center gap-2 mb-4">
          <BarChart3 size={18} className="text-primary" />
          <h2 className="text-lg font-semibold text-card-foreground">Analytics</h2>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <DeviceStatusChart data={deviceStatusData} isLoading={isLoading} />
          <HostResourceChart hosts={hostResourceData} isLoading={isLoading} />
          <ActivityChart isLoading={isLoading} />
        </div>
      </div>

      {/* Main Content */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Hosts Section */}
        <div className="lg:col-span-1 space-y-4">
          <div className="flex items-center gap-2">
            <Server size={18} className="text-primary" />
            <h2 className="text-lg font-semibold text-card-foreground">Hosts</h2>
          </div>
          {isLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-32" />
              <Skeleton className="h-32" />
            </div>
          ) : (
            <div className="space-y-3">
              {(hosts as HostDTO[] | undefined)?.map((host) => (
                <HostCard key={host.id} host={toComponentHost(host)} />
              ))}
            </div>
          )}
        </div>

        {/* Devices Section */}
        <div className="lg:col-span-2 space-y-4">
          <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
            <div className="flex items-center gap-2">
              <Smartphone size={18} className="text-primary" />
              <h2 className="text-lg font-semibold text-card-foreground">Devices</h2>
            </div>

            <div className="flex gap-2 w-full sm:w-auto">
              <div className="relative flex-1 sm:w-48">
                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <Input
                  type="text"
                  placeholder="Search serial or model..."
                  aria-label="Search devices by serial or model"
                  value={filterText}
                  onChange={(e) => setFilterText(e.target.value)}
                  className="pl-9"
                />
              </div>
              <select
                aria-label="Filter devices by status"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="px-3 py-1.5 text-sm border border-input rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <option value="all">All Status</option>
                <option value="idle">Idle</option>
                <option value="testing">Testing</option>
                <option value="offline">Offline</option>
                <option value="error">Error</option>
              </select>
            </div>
          </div>

          {isLoading ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <Skeleton className="h-48" />
              <Skeleton className="h-48" />
              <Skeleton className="h-48" />
              <Skeleton className="h-48" />
            </div>
          ) : (
            <DeviceGrid devices={filteredDevices} />
          )}
        </div>
      </div>
    </PageContainer>
  );
}
