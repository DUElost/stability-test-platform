import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Server, AlertCircle } from 'lucide-react';
import { HostCard, Host } from '../../components/network/HostCard';
import { AddHostModal } from './components/AddHostModal';
import { PageContainer, PageHeader, StatsGrid } from '../../components/layout';
import { api } from '../../utils/api';

const hostStatusMap: Record<string, Host['status']> = {
  'ONLINE': 'online',
  'OFFLINE': 'offline',
  'DEGRADED': 'warning'
};

function toComponentHost(host: any, deviceCountMap: Map<number, number>): Host {
  return {
    ip: host.ip,
    status: hostStatusMap[host.status] || 'offline',
    cpu_load: host.extra?.cpu_load || 0,
    ram_usage: host.extra?.ram_usage || 0,
    disk_usage: host.extra?.disk_usage?.usage_percent || 0,
    mount_status: Object.values(host.mount_status || {}).every((v: any) => v.ok || v === true),
    device_count: deviceCountMap.get(host.id) || 0,
  };
}

export default function HostsPage() {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const queryClient = useQueryClient();

  const { data: hosts, isLoading, error } = useQuery({
    queryKey: ['hosts'],
    queryFn: () => api.hosts.list().then(res => res.data),
    refetchInterval: 10000,
  });

  // 同时加载 devices 数据用于计算每个主机的设备数量
  const { data: devices } = useQuery({
    queryKey: ['devices'],
    queryFn: () => api.devices.list().then(res => res.data),
    refetchInterval: 10000,
  });

  const createMutation = useMutation({
    mutationFn: (data: { name: string; ip: string; ssh_port?: number; ssh_user?: string }) =>
      api.hosts.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['hosts'] });
      setIsModalOpen(false);
      alert('Host added successfully');
    },
    onError: (error: any) => {
      alert(`Failed to add host: ${error.response?.data?.detail || error.message}`);
    },
  });

  // 计算每个主机的设备数量（只统计在线设备）
  const deviceCountMap = useMemo(() => {
    if (!devices) return new Map<number, number>();
    const countMap = new Map<number, number>();
    devices.forEach((device: any) => {
      if (device.host_id && device.status !== 'OFFLINE') {
        const current = countMap.get(device.host_id) || 0;
        countMap.set(device.host_id, current + 1);
      }
    });
    return countMap;
  }, [devices]);

  // 所有 Hooks 必须在任何条件 return 之前调用
  const statsItems = useMemo(() => [
    { label: 'Total Hosts', value: hosts?.length || 0 },
    { label: 'Online', value: hosts?.filter((h: any) => h.status === 'ONLINE').length || 0, color: 'green' as const },
    { label: 'Offline', value: hosts?.filter((h: any) => h.status === 'OFFLINE').length || 0, color: 'slate' as const },
    { label: 'Degraded', value: hosts?.filter((h: any) => h.status === 'DEGRADED').length || 0, color: 'amber' as const },
  ], [hosts]);

  // 在 Hooks 之后进行条件渲染
  if (isLoading) {
    return (
      <div className="p-8 text-center text-slate-500">
        <Server className="w-8 h-8 mx-auto mb-2 animate-pulse" />
        Loading hosts...
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 bg-red-50 text-red-700 rounded-lg border border-red-200 flex items-center gap-2">
        <AlertCircle size={20} />
        Error loading hosts. Please check backend connection.
      </div>
    );
  }

  const actionButton = (
    <button
      onClick={() => setIsModalOpen(true)}
      className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg transition-all shadow-sm hover:shadow btn-press"
    >
      <Plus size={18} />
      Add Host
    </button>
  );

  return (
    <PageContainer>
      <PageHeader
        title="Host Management"
        subtitle="Monitor and manage test execution nodes."
        action={actionButton}
        breadcrumbs={[{ label: 'Hosts' }]}
      />

      <StatsGrid stats={statsItems} columns={4} />

      {/* Host Grid */}
      {hosts && hosts.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
          {hosts.map((host: any) => (
            <HostCard key={host.id} host={toComponentHost(host, deviceCountMap)} />
          ))}
        </div>
      ) : (
        <div className="bg-white rounded-lg border border-slate-200 p-12 text-center">
          <Server className="w-12 h-12 mx-auto text-slate-300 mb-4" />
          <h3 className="text-lg font-medium text-slate-900 mb-2">No hosts found</h3>
          <p className="text-slate-500 mb-4">Add your first host to get started.</p>
          <button
            onClick={() => setIsModalOpen(true)}
            className="inline-flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg transition-all btn-press"
          >
            <Plus size={18} />
            Add Host
          </button>
        </div>
      )}

      <AddHostModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSubmit={(data) => createMutation.mutate(data)}
        isSubmitting={createMutation.isPending}
      />
    </PageContainer>
  );
}
