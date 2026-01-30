import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { HostCard, Host } from '../components/network/HostCard';
import { Device } from '../components/device/DeviceCard';
import { DeviceGrid } from '../components/device/DeviceGrid';
import { PageContainer, StatsGrid } from '../components/layout';
import { useRealtimeDashboard } from '../hooks/useRealtimeDashboard';
import { api } from '../utils/api';
import { WS_DASHBOARD_ENDPOINT } from '../config';

const hostStatusMap: Record<string, Host['status']> = {
  'ONLINE': 'online',
  'OFFLINE': 'offline',
  'DEGRADED': 'warning'
};

function toComponentHost(host: any): Host {
  return {
    ip: host.ip,
    status: hostStatusMap[host.status] || 'offline',
    cpu_load: host.extra?.cpu_load || 0,
    ram_usage: host.extra?.ram_usage || 0,
    disk_usage: host.extra?.disk_usage?.usage_percent || 0,
    mount_status: Object.values(host.mount_status || {}).every((v: any) => v.ok || v === true),
  };
}

const deviceStatusMap: Record<string, Device['status']> = {
  'ONLINE': 'idle',
  'BUSY': 'testing',
  'OFFLINE': 'offline',
  'ERROR': 'error'
};

function toComponentDevice(device: any): Device {
  return {
    serial: device.serial,
    model: device.model || 'Unknown',
    status: deviceStatusMap[device.status as keyof typeof deviceStatusMap] || 'offline',
    // 数据直接从顶层字段读取 (API 现在直接返回这些字段)
    battery_level: device.battery_level ?? 0,
    temperature: device.temperature ?? 0,
    network_latency: device.network_latency ?? null,
  };
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

  // 所有 Hooks 必须在条件返回之前声明
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

  // 条件返回移到所有 Hooks 之后
  if (hostsLoading || devicesLoading) {
    return (
      <PageContainer>
        <div className="flex items-center justify-center h-64 text-slate-500">
          <div className="flex flex-col items-center">
            <div className="w-8 h-8 border-2 border-blue-600 border-t-transparent rounded-full animate-spin mb-3"></div>
            Loading dashboard data...
          </div>
        </div>
      </PageContainer>
    );
  }

  if (hostsError || devicesError) {
    return (
      <PageContainer>
        <div className="p-4 bg-red-50 text-red-700 rounded-lg border border-red-200">
          Error loading data. Please check backend connection.
        </div>
      </PageContainer>
    );
  }

  const statsItems: { label: string; value: string | number; suffix?: string; color?: 'default' | 'green' | 'blue' | 'red' | 'amber' | 'slate' }[] = [
    { label: 'Total Hosts', value: hosts?.length || 0 },
    {
      label: 'Online Devices',
      value: stats.online,
      suffix: `/ ${stats.total}`,
      color: 'green'
    },
    { label: 'Active Testing', value: stats.testing, color: 'blue' },
    {
      label: 'Alerts',
      value: stats.error > 0 ? stats.error : stats.lowBattery > 0 ? stats.lowBattery : stats.highTemp > 0 ? stats.highTemp : 'All Good',
      color: stats.error > 0 ? 'red' : stats.lowBattery > 0 || stats.highTemp > 0 ? 'amber' : 'green'
    },
  ];

  return (
    <PageContainer>
      <div className="flex justify-between items-center text-sm text-slate-500 px-1">
        <div className="flex items-center gap-2">
          <span className={`w-2.5 h-2.5 rounded-full ${wsConnected ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`}></span>
          <span>{wsConnected ? 'Realtime Connected' : 'Disconnected'}</span>
        </div>
        <div className="text-slate-400">
          Updated: {lastUpdateTime.toLocaleTimeString()}
        </div>
      </div>

      <StatsGrid stats={statsItems} columns={4} />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-1 space-y-4">
          <h2 className="text-lg font-semibold text-slate-900">Hosts</h2>
          {hosts?.map((host: any) => (
            <HostCard key={host.id} host={toComponentHost(host)} />
          ))}
        </div>

        <div className="lg:col-span-2 space-y-4">
          <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
            <h2 className="text-lg font-semibold text-slate-900">Devices</h2>

            <div className="flex gap-2 w-full sm:w-auto">
              <input
                type="text"
                placeholder="Search serial or model..."
                value={filterText}
                onChange={(e) => setFilterText(e.target.value)}
                className="px-3 py-1.5 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 w-full sm:w-48"
              />
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="px-3 py-1.5 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="all">All Status</option>
                <option value="idle">Idle</option>
                <option value="testing">Testing</option>
                <option value="offline">Offline</option>
                <option value="error">Error</option>
              </select>
            </div>
          </div>

          <DeviceGrid devices={filteredDevices} />
        </div>
      </div>
    </PageContainer>
  );
}
