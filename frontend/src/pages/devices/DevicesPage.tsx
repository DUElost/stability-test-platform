import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Smartphone, AlertCircle } from 'lucide-react';
import { Device } from '../../components/device/DeviceCard';
import { DeviceGrid } from '../../components/device/DeviceGrid';
import { DeviceToolbar } from './components/DeviceToolbar';
import { AddDeviceModal } from './components/AddDeviceModal';
import { PageContainer, PageHeader, StatsGrid } from '../../components/layout';
import { api } from '../../utils/api';

const deviceStatusMap: Record<string, Device['status']> = {
  'ONLINE': 'idle',
  'BUSY': 'testing',
  'OFFLINE': 'offline',
  'ERROR': 'error'
};

function toComponentDevice(device: any, hostMap: Map<number, any>): Device {
  const host = device.host_id ? hostMap.get(device.host_id) : null;
  return {
    serial: device.serial,
    model: device.model || 'Unknown',
    status: deviceStatusMap[device.status as keyof typeof deviceStatusMap] || 'offline',
    battery_level: device.battery_level ?? 0,
    temperature: device.temperature ?? 0,
    network_latency: device.network_latency ?? null,
    host_id: device.host_id,
    host_name: host?.ip ?? null,
  };
}

export default function DevicesPage() {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [filterText, setFilterText] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const queryClient = useQueryClient();

  const { data: devices, isLoading, error } = useQuery({
    queryKey: ['devices'],
    queryFn: () => api.devices.list().then(res => res.data),
    refetchInterval: 10000,
  });

  // 同时加载 hosts 数据用于显示设备所属主机
  const { data: hosts } = useQuery({
    queryKey: ['hosts'],
    queryFn: () => api.hosts.list().then(res => res.data),
    refetchInterval: 10000,
  });

  const createMutation = useMutation({
    mutationFn: (data: { serial: string; model?: string; host_id?: number; tags?: string[] }) =>
      api.devices.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['devices'] });
      setIsModalOpen(false);
      alert('Device added successfully');
    },
    onError: (error: any) => {
      alert(`Failed to add device: ${error.response?.data?.detail || error.message}`);
    },
  });

  // 构建 hostMap 用于查找设备所属主机
  const hostMap = useMemo(() => {
    if (!hosts) return new Map<number, any>();
    return new Map(hosts.map((h: any) => [h.id, h]));
  }, [hosts]);

  const filteredDevices = useMemo(() => {
    if (!devices) return [];

    return devices.filter((device: any) => {
      const mappedStatus = deviceStatusMap[device.status] || 'offline';
      const matchesStatus = statusFilter === 'all' || mappedStatus === statusFilter;
      const matchesSearch =
        device.serial.toLowerCase().includes(filterText.toLowerCase()) ||
        (device.model && device.model.toLowerCase().includes(filterText.toLowerCase()));
      return matchesStatus && matchesSearch;
    }).map((d: any) => toComponentDevice(d, hostMap));
  }, [devices, statusFilter, filterText, hostMap]);

  const stats = useMemo(() => {
    if (!devices) return { total: 0, online: 0, offline: 0, testing: 0, error: 0 };
    return {
      total: devices.length,
      online: devices.filter((d: any) => d.status === 'ONLINE').length,
      offline: devices.filter((d: any) => d.status === 'OFFLINE').length,
      testing: devices.filter((d: any) => d.status === 'BUSY').length,
      error: devices.filter((d: any) => d.status === 'ERROR').length,
    };
  }, [devices]);

  if (isLoading) {
    return (
      <div className="p-8 text-center text-slate-500">
        <Smartphone className="w-8 h-8 mx-auto mb-2 animate-pulse" />
        Loading devices...
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 bg-red-50 text-red-700 rounded-lg border border-red-200 flex items-center gap-2">
        <AlertCircle size={20} />
        Error loading devices. Please check backend connection.
      </div>
    );
  }

  const statsItems = [
    { label: 'Total', value: stats.total },
    { label: 'Online', value: stats.online, color: 'green' as const },
    { label: 'Testing', value: stats.testing, color: 'blue' as const },
    { label: 'Offline', value: stats.offline, color: 'slate' as const },
    { label: 'Error', value: stats.error, color: 'red' as const },
  ];

  const actionButton = (
    <button
      onClick={() => setIsModalOpen(true)}
      className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg transition-all shadow-sm hover:shadow btn-press"
    >
      <Plus size={18} />
      Add Device
    </button>
  );

  return (
    <PageContainer>
      <PageHeader
        title="Device Management"
        subtitle="Manage and monitor test devices."
        action={actionButton}
        breadcrumbs={[{ label: 'Devices' }]}
      />

      <StatsGrid stats={statsItems} columns={5} />

      {/* Toolbar */}
      <DeviceToolbar
        filterText={filterText}
        onFilterTextChange={setFilterText}
        statusFilter={statusFilter}
        onStatusFilterChange={setStatusFilter}
      />

      {/* Device Grid */}
      {filteredDevices.length > 0 ? (
        <DeviceGrid devices={filteredDevices} />
      ) : (
        <div className="bg-white rounded-lg border border-slate-200 p-12 text-center">
          <Smartphone className="w-12 h-12 mx-auto text-slate-300 mb-4" />
          <h3 className="text-lg font-medium text-slate-900 mb-2">
            {devices && devices.length > 0
              ? 'No devices match current filters'
              : 'No devices found'}
          </h3>
          <p className="text-slate-500 mb-4">
            {devices && devices.length > 0
              ? 'Try adjusting your search or filter criteria.'
              : 'Add your first device to get started.'}
          </p>
          {(!devices || devices.length === 0) && (
            <button
              onClick={() => setIsModalOpen(true)}
              className="inline-flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg transition-all btn-press"
            >
              <Plus size={18} />
              Add Device
            </button>
          )}
        </div>
      )}

      <AddDeviceModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSubmit={(data) => createMutation.mutate(data)}
        isSubmitting={createMutation.isPending}
      />
    </PageContainer>
  );
}
