import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Loader2 } from 'lucide-react';
import { ExpandableHostTable, type HostTableData } from '../../components/network/ExpandableHostTable';
import { AddHostModal } from './components/AddHostModal';
import { api } from '../../utils/api';
import { CleanCard } from '../../components/ui/clean-card';
import { CleanButton } from '../../components/ui/clean-button';

export default function HostsPage() {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const queryClient = useQueryClient();

  const { data: hosts, isLoading, error } = useQuery({
    queryKey: ['hosts'],
    queryFn: () => api.hosts.list().then(res => res.data),
    refetchInterval: 10000,
  });

  const { data: devices } = useQuery({
    queryKey: ['devices'],
    queryFn: () => api.devices.list().then(res => res.data),
    refetchInterval: 10000,
  });

  const { data: tasks } = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.tasks.list().then(res => res.data),
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

  const [deployingHostId, setDeployingHostId] = useState<number | null>(null);

  const deployMutation = useMutation({
    mutationFn: (hostId: number) => api.deploy.trigger(hostId),
    onSuccess: (_data, hostId) => {
      alert(`Deployment started for host ${hostId}. Check status for updates.`);
      setDeployingHostId(null);
    },
    onError: (error: any) => {
      alert(`Deployment failed: ${error.response?.data?.detail || error.message}`);
      setDeployingHostId(null);
    },
  });

  const handleDeploy = (hostId: number) => {
    if (confirm(`Are you sure you want to deploy to host ${hostId}?`)) {
      setDeployingHostId(hostId);
      deployMutation.mutate(hostId);
    }
  };

  // Calculate device count per host
  const deviceCountMap = useMemo(() => {
    if (!devices) return new Map<number, number>();
    const countMap = new Map<number, number>();
    devices.forEach((device: any) => {
      if (device.host_id) {
        const current = countMap.get(device.host_id) || 0;
        countMap.set(device.host_id, current + 1);
      }
    });
    return countMap;
  }, [devices]);

  // Calculate active tasks per host
  const activeTasksMap = useMemo(() => {
    if (!tasks) return new Map<number, number>();
    const countMap = new Map<number, number>();
    tasks.forEach((task: any) => {
      if (task.host_id && ['PENDING', 'QUEUED', 'RUNNING'].includes(task.status)) {
        const current = countMap.get(task.host_id) || 0;
        countMap.set(task.host_id, current + 1);
      }
    });
    return countMap;
  }, [tasks]);

  // Transform data for expandable table
  const tableData: HostTableData[] = useMemo(() => {
    if (!hosts) return [];
    return hosts.map((host: any) => {
      const extra = host.extra || {};
      const diskInfo = extra.disk_usage || {};

      return {
        id: host.id,
        name: host.name,
        ip: host.ip,
        status: host.status,
        last_heartbeat: host.last_heartbeat,
        resources: host.status === 'ONLINE' ? {
          cpu_load: extra.cpu_load || 0,
          cpu_cores: extra.cpu_cores,
          ram_usage: extra.ram_usage || 0,
          ram_total_gb: extra.ram_total_gb,
          disk_usage: diskInfo.usage_percent || 0,
          disk_total_gb: diskInfo.total_gb,
          temperature: extra.temperature,
          uptime_seconds: extra.uptime_seconds,
        } : undefined,
        mount_status: host.mount_status
          ? Object.entries(host.mount_status).map(([path, info]: [string, any]) => ({
              path,
              mounted: info.ok || info === true,
              available_gb: info.available_gb,
              total_gb: info.total_gb,
            }))
          : [],
        device_count: deviceCountMap.get(host.id) || 0,
        active_tasks: activeTasksMap.get(host.id) || 0,
      };
    });
  }, [hosts, deviceCountMap, activeTasksMap]);

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">主机管理</h2>
          <p className="text-sm text-gray-400">管理和监控测试执行节点</p>
        </div>
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">主机管理</h2>
          <p className="text-sm text-gray-400">管理和监控测试执行节点</p>
        </div>
        <div className="p-4 bg-red-50 text-red-600 rounded-lg border border-red-100">
          Error loading hosts. Please check backend connection.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">主机管理</h2>
          <p className="text-sm text-gray-400">管理和监控测试执行节点</p>
        </div>
        <CleanButton variant="primary" onClick={() => setIsModalOpen(true)}>
          <Plus className="w-4 h-4" />
          添加主机
        </CleanButton>
      </div>

      {/* Host Table */}
      {tableData.length > 0 ? (
        <ExpandableHostTable
          hosts={tableData}
          onDeploy={handleDeploy}
          isDeploying={(hostId: number) => deployMutation.isPending && deployingHostId === hostId}
        />
      ) : (
        <CleanCard className="p-12 text-center">
          <h3 className="text-lg font-medium text-gray-900 mb-2">暂无主机</h3>
          <p className="text-sm text-gray-400 mb-4">添加您的第一台主机以开始使用。</p>
          <CleanButton variant="primary" onClick={() => setIsModalOpen(true)}>
            <Plus className="w-4 h-4" />
            添加主机
          </CleanButton>
        </CleanCard>
      )}

      <AddHostModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSubmit={(data) => createMutation.mutate(data)}
        isSubmitting={createMutation.isPending}
      />
    </div>
  );
}
