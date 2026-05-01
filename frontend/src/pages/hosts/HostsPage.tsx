import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Loader2, Rocket } from 'lucide-react';
import { useToast } from '../../components/ui/toast';
import { useConfirm } from '../../hooks/useConfirm';
import { ExpandableHostTable, type HostTableData } from '../../components/network/ExpandableHostTable';
import { AddHostModal } from './components/AddHostModal';
import { api } from '../../utils/api';
import { CleanCard } from '../../components/ui/clean-card';
import { CleanButton } from '../../components/ui/clean-button';

export default function HostsPage() {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedHostIds, setSelectedHostIds] = useState<Set<number>>(new Set());
  const queryClient = useQueryClient();
  const toast = useToast();
  const confirmDialog = useConfirm();

  const { data: hosts, isLoading, error } = useQuery({
    queryKey: ['hosts'],
    queryFn: () => api.hosts.list(0, 200).then(res => res.data.items),
    refetchInterval: 10000,
  });

  const { data: devices } = useQuery({
    queryKey: ['devices'],
    queryFn: () => api.devices.list(0, 200).then(res => res.data.items),
    refetchInterval: 10000,
  });

  const { data: activeJobs } = useQuery({
    queryKey: ['active-jobs'],
    queryFn: async () => {
      const [pending, running] = await Promise.all([
        api.execution.listJobs(0, 200, undefined, 'PENDING'),
        api.execution.listJobs(0, 200, undefined, 'RUNNING'),
      ]);
      return [...pending.items, ...running.items];
    },
    refetchInterval: 10000,
  });

  const createMutation = useMutation({
    mutationFn: (data: { name: string; ip: string; ssh_port?: number; ssh_user?: string }) =>
      api.hosts.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['hosts'] });
      setIsModalOpen(false);
      toast.success('主机添加成功');
    },
    onError: (error: any) => {
      toast.error(`添加主机失败: ${error.response?.data?.detail || error.message}`);
    },
  });

  const [deployingHostId, setDeployingHostId] = useState<number | null>(null);

  const deployMutation = useMutation({
    mutationFn: (hostId: number) => api.deploy.trigger(hostId),
    onSuccess: (_data, hostId) => {
      toast.success(`主机 ${hostId} 部署已启动`);
      setDeployingHostId(null);
    },
    onError: (error: any) => {
      toast.error(`部署失败: ${error.response?.data?.detail || error.message}`);
      setDeployingHostId(null);
    },
  });

  const handleDeploy = async (hostId: number) => {
    const ok = await confirmDialog({ description: `确定要部署到主机 ${hostId} 吗？` });
    if (ok) {
      setDeployingHostId(hostId);
      deployMutation.mutate(hostId);
    }
  };

  const batchDeployMutation = useMutation({
    mutationFn: (hostIds: number[]) => api.deploy.batchDeploy(hostIds),
    onSuccess: () => {
      toast.success(`已启动 ${selectedHostIds.size} 台主机的批量部署`);
      setSelectedHostIds(new Set());
      queryClient.invalidateQueries({ queryKey: ['hosts'] });
    },
    onError: (error: any) => {
      toast.error(`批量部署失败: ${error.response?.data?.detail || error.message}`);
    },
  });

  const handleBatchDeploy = async () => {
    if (selectedHostIds.size === 0) return;
    const ok = await confirmDialog({ description: `确定要部署到 ${selectedHostIds.size} 台选中主机吗？` });
    if (ok) {
      batchDeployMutation.mutate(Array.from(selectedHostIds));
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

  const activeTasksMap = useMemo(() => {
    if (!activeJobs) return new Map<number, number>();
    const countMap = new Map<number, number>();
    activeJobs.forEach((job: any) => {
      if (job.host_id) {
        const hostKey = Number(job.host_id);
        if (!isNaN(hostKey)) {
          countMap.set(hostKey, (countMap.get(hostKey) || 0) + 1);
        }
      }
    });
    return countMap;
  }, [activeJobs]);

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
        // ADR-0019 Phase 3c: structured capacity/health
        max_concurrent_jobs: host.max_concurrent_jobs,
        effective_slots: host.capacity?.effective_slots,
        health_status: host.health?.status,
        health_reasons: host.health?.reasons,
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
          加载主机失败，请检查后端服务连接。
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
        <div className="flex items-center gap-2">
          {selectedHostIds.size > 0 && (
            <CleanButton
              variant="default"
              onClick={handleBatchDeploy}
              disabled={batchDeployMutation.isPending}
            >
              <Rocket className="w-4 h-4" />
              {batchDeployMutation.isPending ? '部署中...' : `批量部署 (${selectedHostIds.size})`}
            </CleanButton>
          )}
          <CleanButton variant="primary" onClick={() => setIsModalOpen(true)}>
            <Plus className="w-4 h-4" />
            添加主机
          </CleanButton>
        </div>
      </div>

      {/* Host Table */}
      {tableData.length > 0 ? (
        <ExpandableHostTable
          hosts={tableData}
          onDeploy={handleDeploy}
          isDeploying={(hostId: number) => deployMutation.isPending && deployingHostId === hostId}
          selectedIds={selectedHostIds}
          onSelectionChange={setSelectedHostIds}
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
