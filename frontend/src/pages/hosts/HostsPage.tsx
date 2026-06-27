import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Rocket, Server } from 'lucide-react';
import { useToast } from '@/hooks/useToast';
import { useConfirm } from '@/hooks/useConfirm';
import { useAuthSession } from '@/hooks/useAuthSession';
import { ExpandableHostTable, type HostTableData } from '@/components/network/ExpandableHostTable';
import { AddHostModal } from './components/AddHostModal';
import HostHotUpdateConfirmDialog from '@/components/host/HostHotUpdateConfirmDialog';
import { api } from '@/utils/api';
import { deviceKeys, hostKeys } from '@/utils/api/queryKeys';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { PageContainer, PageHeader } from '@/components/layout';
import { ErrorState } from '@/components/ui/error-state';
import { EmptyState } from '@/components/ui/empty-state';
import { SKELETON_BLOCK, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';

export default function HostsPage() {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedHostIds, setSelectedHostIds] = useState<Set<string | number>>(new Set());
  const queryClient = useQueryClient();
  const toast = useToast();
  const confirmDialog = useConfirm();
  const sessionQ = useAuthSession();
  const canManageWatcherAdminState = sessionQ.data?.role === 'admin';

  const { data: hosts, isLoading, error } = useQuery({
    queryKey: hostKeys.list(),
    queryFn: () => api.hosts.list(0, 200).then(res => res.data.items),
    refetchInterval: 10000,
  });

  const { data: devices } = useQuery({
    queryKey: deviceKeys.list(),
    queryFn: () => api.devices.list(0, 200).then(res => res.data.items),
    refetchInterval: 10000,
  });

  const createMutation = useMutation({
    mutationFn: (data: { name: string; ip: string; ssh_port?: number; ssh_user?: string }) =>
      api.hosts.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: hostKeys.list() });
      setIsModalOpen(false);
      toast.success('主机添加成功');
    },
    onError: (error: any) => {
      toast.error(`添加主机失败: ${error.response?.data?.detail || error.message}`);
    },
  });

  const [deployingHostId, setDeployingHostId] = useState<string | number | null>(null);
  const [watcherAdminUpdatingHostId, setWatcherAdminUpdatingHostId] = useState<
    string | number | null
  >(null);

  const deployMutation = useMutation({
    mutationFn: (hostId: string | number) => api.deploy.trigger(hostId),
    onSuccess: (_data, hostId) => {
      toast.success(`主机 ${hostId} 部署已启动`);
      setDeployingHostId(null);
    },
    onError: (error: any) => {
      toast.error(`部署失败: ${error.response?.data?.detail || error.message}`);
      setDeployingHostId(null);
    },
  });

  const handleDeploy = async (hostId: string | number) => {
    const ok = await confirmDialog({ description: `确定要部署到主机 ${hostId} 吗？` });
    if (ok) {
      setDeployingHostId(hostId);
      deployMutation.mutate(hostId);
    }
  };

  const batchDeployMutation = useMutation({
    mutationFn: (hostIds: Array<string | number>) => api.deploy.batchDeploy(hostIds),
    onSuccess: () => {
      toast.success(`已启动 ${selectedHostIds.size} 台主机的批量部署`);
      setSelectedHostIds(new Set());
      queryClient.invalidateQueries({ queryKey: hostKeys.list() });
    },
    onError: (error: any) => {
      toast.error(`批量部署失败: ${error.response?.data?.detail || error.message}`);
    },
  });

  const [hotUpdatingHostId, setHotUpdatingHostId] = useState<number | string | null>(null);
  const [pendingHotUpdateHostId, setPendingHotUpdateHostId] = useState<
    number | string | null
  >(null);
  const [pendingRetryAfter, setPendingRetryAfter] = useState<number | undefined>(
    undefined,
  );

  const watcherAdminStateMutation = useMutation({
    mutationFn: (vars: { hostId: string | number; watcher_admin_active: boolean }) =>
      api.hosts.updateWatcherAdminState(vars.hostId, {
        watcher_admin_active: vars.watcher_admin_active,
      }),
    onSuccess: (_data, vars) => {
      toast.success(
        vars.watcher_admin_active ? `主机 ${vars.hostId} 已设为已激活` : `主机 ${vars.hostId} 已设为未激活`,
      );
      setWatcherAdminUpdatingHostId(null);
      queryClient.invalidateQueries({ queryKey: hostKeys.list() });
      queryClient.invalidateQueries({ queryKey: ['host-detail', vars.hostId] });
    },
    onError: (error: any, vars) => {
      toast.error(
        `更新 Watch 状态失败: ${
          error?.response?.data?.detail || error?.message || `host ${vars.hostId}`
        }`,
      );
      setWatcherAdminUpdatingHostId(null);
    },
  });

  const hotUpdateMutation = useMutation({
    mutationFn: (vars: { hostId: number | string; abortRunningJobs: boolean }) =>
      api.hotUpdate.trigger(vars.hostId, { abortRunningJobs: vars.abortRunningJobs }),
    onSuccess: (_data, vars) => {
      toast.success(
        vars.abortRunningJobs
          ? `主机 ${vars.hostId} 已中止活跃 Job 并完成热更新`
          : `主机 ${vars.hostId} 热更新完成`,
      );
      setHotUpdatingHostId(null);
      setPendingHotUpdateHostId(null);
      queryClient.invalidateQueries({ queryKey: hostKeys.list() });
      queryClient.invalidateQueries({ queryKey: ['host-detail', vars.hostId] });
    },
    onError: (error: any, vars) => {
      // 409 with active_jobs surfaces here when the user (or our default
      // path) requested a hot-update without abort_running_jobs.  The dialog
      // itself prevents this by enforcing the toggle before enabling
      // confirm, but the mutation is still defensive for direct API misuse.
      const detail = error?.response?.data?.detail;
      if (
        error?.response?.status === 409 &&
        detail &&
        typeof detail === 'object' &&
        Array.isArray(detail.active_jobs)
      ) {
        toast.error(
          `主机 ${vars.hostId} 仍有 ${detail.active_jobs.length} 个活跃 Job — 请勾选「中止并热更新」`,
        );
        // Re-open the dialog so the user can opt into the abort path.
        setPendingHotUpdateHostId(vars.hostId);
        setPendingRetryAfter(
          typeof detail.retry_after_seconds === 'number'
            ? detail.retry_after_seconds
            : undefined,
        );
      } else {
        toast.error(
          `热更新失败: ${
            typeof detail === 'string' ? detail : error?.message ?? '未知错误'
          }`,
        );
      }
      setHotUpdatingHostId(null);
    },
  });

  const handleHotUpdate = (hostId: number | string) => {
    setPendingHotUpdateHostId(hostId);
  };

  const handleHotUpdateConfirm = (
    hostId: number | string,
    opts: { abortRunningJobs: boolean },
  ) => {
    setHotUpdatingHostId(hostId);
    hotUpdateMutation.mutate({ hostId, abortRunningJobs: opts.abortRunningJobs });
  };

  const handleBatchDeploy = async () => {
    if (selectedHostIds.size === 0) return;
    const ok = await confirmDialog({ description: `确定要部署到 ${selectedHostIds.size} 台选中主机吗？` });
    if (ok) {
      batchDeployMutation.mutate(Array.from(selectedHostIds));
    }
  };

  const handleWatcherAdminStateChange = async (
    hostId: string | number,
    nextActive: boolean,
  ) => {
    if (!canManageWatcherAdminState) return;
    if (!nextActive) {
      const ok = await confirmDialog({
        description:
          '将节点设为未激活后，只影响后续新派发任务；正在运行的任务不受影响。是否继续？',
      });
      if (!ok) return;
    }
    setWatcherAdminUpdatingHostId(hostId);
    watcherAdminStateMutation.mutate({
      hostId,
      watcher_admin_active: nextActive,
    });
  };

  // Calculate device count + claim exclusion hints per host
  const hostDeviceStats = useMemo(() => {
    const stats = new Map<
      string | number,
      {
        total: number;
        adbExcluded: number;
        leaseBusy: number;
        claimable: number;
      }
    >();
    if (!devices) return stats;

    const isAdbExcluded = (device: {
      adb_connected?: boolean | null;
      adb_state?: string | null;
      status?: string;
    }) =>
      device.adb_connected === false ||
      device.adb_state === 'offline' ||
      device.adb_state === 'unknown' ||
      device.status === 'OFFLINE';

    devices.forEach((device: {
      host_id?: string | number | null;
      status?: string;
      adb_connected?: boolean | null;
      adb_state?: string | null;
    }) => {
      if (!device.host_id) return;
      const cur = stats.get(device.host_id) ?? {
        total: 0,
        adbExcluded: 0,
        leaseBusy: 0,
        claimable: 0,
      };
      cur.total += 1;
      if (isAdbExcluded(device)) {
        cur.adbExcluded += 1;
      } else if (device.status === 'BUSY') {
        cur.leaseBusy += 1;
      } else {
        cur.claimable += 1;
      }
      stats.set(device.host_id, cur);
    });
    return stats;
  }, [devices]);

  const deviceCountMap = useMemo(() => {
    const countMap = new Map<number | string, number>();
    hostDeviceStats.forEach((v, hostId) => {
      countMap.set(hostId, v.total);
    });
    return countMap;
  }, [hostDeviceStats]);

  // Transform data for expandable table
  const tableData: HostTableData[] = useMemo(() => {
    if (!hosts) return [];
    return hosts.map((host: any) => {
      const extra = host.extra || {};
      const diskInfo = extra.disk_usage || {};
      const devStats = hostDeviceStats.get(host.id);
      let claimHint: string | null = null;
      if (devStats && devStats.total > 0) {
        const parts = [`${devStats.claimable} 可认领`];
        if (devStats.adbExcluded > 0) {
          parts.push(`${devStats.adbExcluded} adb 离线排除`);
        }
        if (devStats.leaseBusy > 0) {
          parts.push(`${devStats.leaseBusy} 租约占用`);
        }
        claimHint = parts.join(' · ');
      }

      return {
        id: host.id,
        name: host.name,
        ip: host.ip,
        status: host.status,
        watcher_admin_active: host.watcher_admin_active !== false,
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
        claim_hint: claimHint,
        active_tasks: host.capacity?.active_jobs ?? host.active_job_count ?? 0,
        health_status: host.health?.status,
        health_reasons: host.health?.reasons,
      };
    });
  }, [hosts, deviceCountMap, hostDeviceStats]);

  if (isLoading) {
    return (
      <PageContainer width="wide">
        <PageHeader title="主机管理" subtitle="管理和监控测试执行节点" />
        <div className="space-y-4">
          <div className={cn('h-32', SKELETON_BLOCK)} />
          <div className={cn('h-64', SKELETON_BLOCK)} />
        </div>
      </PageContainer>
    );
  }

  if (error) {
    return (
      <PageContainer width="wide">
        <PageHeader title="主机管理" subtitle="管理和监控测试执行节点" />
        <ErrorState
          title="加载主机失败"
          description="请检查后端服务连接"
          onRetry={() => queryClient.invalidateQueries({ queryKey: hostKeys.list() })}
        />
      </PageContainer>
    );
  }

  if (tableData.length === 0) {
    return (
      <PageContainer width="wide">
        <PageHeader title="主机管理" subtitle="管理和监控测试执行节点" />
        <EmptyState
          title="还没有主机"
          description="添加您的第一台测试执行节点"
          icon={<Server className="w-16 h-16" />}
          action={
            <Button onClick={() => setIsModalOpen(true)}>
              <Plus className="w-4 h-4 mr-2" />
              添加主机
            </Button>
          }
        />
        <AddHostModal
          isOpen={isModalOpen}
          onClose={() => setIsModalOpen(false)}
          onSubmit={createMutation.mutate}
          isSubmitting={createMutation.isPending}
        />
      </PageContainer>
    );
  }

  return (
    <PageContainer width="wide">
      <PageHeader title="主机管理" subtitle="管理和监控测试执行节点" />

      <div className="flex items-center justify-end gap-2 py-2">
        {selectedHostIds.size > 0 && (
          <Button
            variant="outline"
            onClick={handleBatchDeploy}
            disabled={batchDeployMutation.isPending}
          >
            <Rocket className="w-4 h-4" />
            {batchDeployMutation.isPending ? '部署中...' : `批量部署 (${selectedHostIds.size})`}
          </Button>
        )}
        <Button onClick={() => setIsModalOpen(true)}>
          <Plus className="w-4 h-4" />
          添加主机
        </Button>
      </div>

      {/* Host Table */}
      {tableData.length > 0 ? (
        <ExpandableHostTable
          hosts={tableData}
          onDeploy={handleDeploy}
          isDeploying={(hostId: string | number) => deployMutation.isPending && deployingHostId === hostId}
          onHotUpdate={handleHotUpdate}
          isHotUpdating={(hostId: string | number) => hotUpdateMutation.isPending && hotUpdatingHostId === hostId}
          onWatcherAdminStateChange={handleWatcherAdminStateChange}
          isWatcherAdminStateUpdating={(hostId: string | number) =>
            watcherAdminStateMutation.isPending && watcherAdminUpdatingHostId === hostId
          }
          canManageWatcherAdminState={canManageWatcherAdminState}
          selectedIds={selectedHostIds}
          onSelectionChange={setSelectedHostIds}
        />
      ) : (
        <Card className="p-12 text-center">
          <h3 className={cn('text-lg font-medium mb-2', TEXT.heading)}>暂无主机</h3>
          <p className={cn('text-sm mb-4', TEXT.subtitle)}>添加您的第一台主机以开始使用。</p>
          <Button onClick={() => setIsModalOpen(true)}>
            <Plus className="w-4 h-4" />
            添加主机
          </Button>
        </Card>
      )}

      <AddHostModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSubmit={(data) => createMutation.mutate(data)}
        isSubmitting={createMutation.isPending}
      />

      <HostHotUpdateConfirmDialog
        hostId={pendingHotUpdateHostId}
        onClose={() => {
          if (!hotUpdateMutation.isPending) setPendingHotUpdateHostId(null);
        }}
        onConfirm={handleHotUpdateConfirm}
        isHotUpdatePending={hotUpdateMutation.isPending}
        retryAfterSeconds={pendingRetryAfter}
      />
    </PageContainer>
  );
}
