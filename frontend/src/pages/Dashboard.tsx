import { useQuery } from '@tanstack/react-query';
import { HostCard, Host } from '../components/network/HostCard';
import { DeviceCard, Device } from '../components/device/DeviceCard';
import { api } from '../utils/api';

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
    status: deviceStatusMap[device.status] || 'offline',
    battery_level: device.extra?.battery_level ?? 0,
    temperature: device.extra?.temperature ?? 0,
    network_latency: device.extra?.network_latency ?? null,
  };
}

export default function Dashboard() {
  const { data: hosts, isLoading: hostsLoading, error: hostsError } = useQuery({
    queryKey: ['hosts'],
    queryFn: () => api.hosts.list().then(res => res.data),
    refetchInterval: 5000, // 每 5 秒轮询一次
  });

  const { data: devices, isLoading: devicesLoading, error: devicesError } = useQuery({
    queryKey: ['devices'],
    queryFn: () => api.devices.list().then(res => res.data),
    refetchInterval: 5000, // 每 5 秒轮询一次
  });

  if (hostsLoading || devicesLoading) {
    return <div className="p-8 text-center text-slate-500">Loading dashboard data...</div>;
  }

  if (hostsError || devicesError) {
    return (
      <div className="p-4 bg-red-50 text-red-700 rounded-lg border border-red-200">
        Error loading data. Please check backend connection.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200">
          <h3 className="text-sm font-medium text-slate-500">Total Hosts</h3>
          <p className="text-3xl font-bold text-slate-900 mt-2">{hosts?.length || 0}</p>
        </div>
        <div className="bg-white p-6 rounded-lg shadow-sm border border-slate-200">
          <h3 className="text-sm font-medium text-slate-500">Connected Devices</h3>
          <p className="text-3xl font-bold text-slate-900 mt-2">{devices?.length || 0}</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-1 space-y-4">
          <h2 className="text-lg font-semibold text-slate-900">Hosts</h2>
          {hosts?.map((host) => (
            <HostCard key={host.id} host={toComponentHost(host)} />
          ))}
        </div>

        <div className="lg:col-span-2 space-y-4">
          <h2 className="text-lg font-semibold text-slate-900">Devices</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {devices?.map((device) => (
              <DeviceCard key={device.id} device={toComponentDevice(device)} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
