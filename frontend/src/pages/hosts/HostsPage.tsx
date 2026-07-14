import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Server } from 'lucide-react';
import { useToast } from '@/hooks/useToast';
import { useConfirm } from '@/hooks/useConfirm';
import { useAuthSession } from '@/hooks/useAuthSession';
import { useHostOperations } from '@/hooks/useHostOperations';
import { ExpandableHostTable, type HostTableData } from '@/components/network/ExpandableHostTable';
import { AddHostModal } from './components/AddHostModal';
import HostHotUpdateConfirmDialog from '@/components/host/HostHotUpdateConfirmDialog';
import HostBulkActionBar from '@/components/host/HostBulkActionBar';
import HostOperationPanel from '@/components/host/HostOperationPanel';
import { api } from '@/utils/api';
import type { Host } from '@/utils/api/types';
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
  const isAdmin = sessionQ.data?.role === 'admin';

  const { data: hosts, isLoading, error } = useQuery({
    queryKey: hostKeys.list(),
    queryFn: () => api.hosts.list(0, 200).then(res => res.items),
    refetchInterval: 10000,
  });

  const { data: devices } = useQuery({
    queryKey: deviceKeys.list(),
    queryFn: () => api.devices.list(0, 200).then(res => res.items),
    refetchInterval: 10000,
  });

  const createMutation = useMutation({
    mutationFn: (data: Parameters<typeof api.hosts.create>[0]) => api.hosts.create(data),
    onSuccess: (host) => {
      queryClient.invalidateQueries({ queryKey: hostKeys.list() });
      setIsModalOpen(false);
      toast.success('主机添加成功');
      if (host.host_key_trust && host.host_key_trust !== 'ok') {
        toast.info(
          `主机密钥自动信任失败（${host.host_key_trust}），热更新/首次安装前请手动 ssh-keyscan`,
        );
      }
    },
    onError: (error: any) => {
      toast.error(`添加主机失败: ${error.response?.data?.detail || error.message}`);
    },
  });

  const [editingHost, setEditingHost] = useState<Host | null>(null);
  const updateMutation = useMutation({
    mutationFn: (vars: { hostId: string | number; data: Parameters<typeof api.hosts.update>[1] }) =>
      api.hosts.update(vars.hostId, vars.data),
    onSuccess: (host) => {
      queryClient.invalidateQueries({ queryKey: hostKeys.list() });
      setEditingHost(null);
      toast.success('主机已更新');
      if (host.host_key_trust && host.host_key_trust !== 'ok') {
        toast.info(`主机密钥自动信任失败（${host.host_key_trust}）`);
      }
    },
    onError: (error: any) => {
      toast.error(`更新主机失败: ${error.response?.data?.detail || error.message}`);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (hostId: string | number) => api.hosts.delete(hostId),
    onSuccess: (_data, hostId) => {
      queryClient.invalidateQueries({ queryKey: hostKeys.list() });
      toast.success(`主机 ${hostId} 已删除`);
    },
    onError: (error: any) => {
      toast.error(`删除主机失败: ${error.response?.data?.detail || error.message}`);
    },
  });

  const [watcherAdminUpdatingHostId, setWatcherAdminUpdatingHostId] = useState<
    string | number | null
  >(null);

  const {
    ops: hostOps,
    panelOpen: opPanelOpen,
    setPanelOpen: setOpPanelOpen,
    startInstallBatch,
    markTerminal,
    closePanel,
    isHostBusy,
  } = useHostOperations({
    concurrency: 2,
    onTerminal: (ev) => {
      if (ev.ok) {
        toast.success(`主机 ${ev.label} Agent 安装完成`);
        queryClient.invalidateQueries({ queryKey: hostKeys.list() });
      } else {
        toast.error(
          `主机 ${ev.label} Agent 安装失败: ${ev.error ?? ev.status}`,
        );
      }
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
    onSuccess: (data, vars) => {
      const depNote = data.deps_refreshed
        ? ' (依赖已刷新)'
        : ' (依赖未变)';
      const verNote = data.code_version ? ` @${data.code_version}` : '';
      toast.success(
        vars.abortRunningJobs
          ? `主机 ${vars.hostId} 已中止活跃 Job 并完成热更新${depNote}${verNote}`
          : `主机 ${vars.hostId} 热更新完成${depNote}${verNote}`,
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

  const handleInstallTerminalStatus = (hostId: string, status: string) => {
    // LiveConsole 终态回调（仅展开行会触发）；与 hook 轮询双通道，markTerminal 幂等
    if (status === 'SUCCESS') {
      markTerminal(hostId, 'success');
    } else if (status === 'FAILED' || status === 'CANCELED') {
      markTerminal(hostId, 'failed', status);
    }
  };

  const resolveInstallTargets = (hostIds: Array<string | number>) => {
    return hostIds
      .map((id) => {
        const full = hosts?.find((h: Host) => h.id === id);
        if (!full) return null;
        if (full.status === 'ONLINE') return null;
        return {
          hostId: full.id,
          label: full.name ?? full.ip ?? String(full.id),
          agentInstalled: Boolean(full.agent_installed),
        };
      })
      .filter((t): t is NonNullable<typeof t> => t != null);
  };

  const handleInstall = async (hostId: number | string) => {
    const targets = resolveInstallTargets([hostId]);
    if (!targets.length) {
      toast.info('该主机在线，请使用热更新');
      return;
    }
    const t = targets[0];
    const ok = await confirmDialog({
      description: t.agentInstalled
        ? `确定重新安装主机「${t.label}」的 Agent？`
        : `确定对主机「${t.label}」执行首次安装？`,
    });
    if (!ok) return;
    await startInstallBatch(targets);
  };

  const handleBulkInstall = async () => {
    const targets = resolveInstallTargets(Array.from(selectedHostIds));
    if (!targets.length) {
      toast.info('选中主机中没有可安装目标（ONLINE 请用热更新）');
      return;
    }
    const first = targets.filter((t) => !t.agentInstalled).length;
    const re = targets.filter((t) => t.agentInstalled).length;
    const ok = await confirmDialog({
      description: `将对 ${targets.length} 台主机安装 Agent（首次 ${first} / 重装 ${re}，并发 2）。是否继续？`,
    });
    if (!ok) return;
    await startInstallBatch(targets);
    setSelectedHostIds(new Set());
  };

  const handleBulkDelete = async () => {
    if (selectedHostIds.size === 0) return;
    const ok = await confirmDialog({
      description: `确定删除选中的 ${selectedHostIds.size} 台主机？此操作不可恢复。`,
    });
    if (!ok) return;
    for (const id of Array.from(selectedHostIds)) {
      try {
        await api.hosts.delete(id);
      } catch (error: any) {
        toast.error(
          `删除 ${id} 失败: ${error?.response?.data?.detail || error?.message || '未知错误'}`,
        );
      }
    }
    toast.success('批量删除已完成');
    setSelectedHostIds(new Set());
    queryClient.invalidateQueries({ queryKey: hostKeys.list() });
  };

  const handleEdit = (host: HostTableData) => {
    const full = hosts?.find((h: any) => h.id === host.id);
    if (full) setEditingHost(full);
  };

  const handleEditSubmit = (data: {
    name: string;
    ip: string;
    ssh_port: number;
    ssh_user: string;
    ssh_password?: string | null;
  }) => {
    if (!editingHost) return;
    updateMutation.mutate({ hostId: editingHost.id, data });
  };

  const handleDelete = async (host: HostTableData) => {
    const ok = await confirmDialog({
      description: `确定删除主机「${host.name ?? host.id}」(${host.ip ?? '?'})？此操作不可恢复。`,
    });
    if (ok) {
      deleteMutation.mutate(host.id);
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
        agent_installed: Boolean(host.agent_installed),
        agent_protocol_version: host.agent_protocol_version ?? host.extra?.agent_version ?? null,
        agent_code_revision: host.agent_code_revision ?? null,
        expected_code_revision: host.expected_code_revision ?? null,
        agent_code_deployed: host.agent_code_deployed ?? null,
        agent_code_deployed_at: host.agent_code_deployed_at ?? null,
        agent_code_sync_status: host.agent_code_sync_status ?? 'unknown',
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

  const bulkCounts = useMemo(() => {
    const selected = Array.from(selectedHostIds)
      .map((id) => hosts?.find((h: Host) => h.id === id))
      .filter((h): h is Host => Boolean(h));
    let firstInstall = 0;
    let reinstall = 0;
    let hotUpdate = 0;
    for (const h of selected) {
      if (h.status === 'ONLINE') {
        hotUpdate += 1;
      } else if (h.agent_installed) {
        reinstall += 1;
      } else {
        firstInstall += 1;
      }
    }
    return {
      selected: selectedHostIds.size,
      firstInstall,
      reinstall,
      hotUpdate,
    };
  }, [selectedHostIds, hosts]);

  const installPending = hostOps.some(
    (op) => op.status === 'pending' || op.status === 'running',
  );

  const handleSelectedHotUpdate = () => {
    if (selectedHostIds.size !== 1) return;
    const [hostId] = Array.from(selectedHostIds);
    const host = hosts?.find((item: Host) => item.id === hostId);
    if (!host || host.status !== 'ONLINE') {
      toast.info('请选择一台在线主机进行热更新');
      return;
    }
    handleHotUpdate(hostId);
  };

  if (isLoading) {
    return (
      <PageContainer width="full">
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
      <PageContainer width="full">
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
      <PageContainer width="full">
        <PageHeader title="主机管理" subtitle="管理和监控测试执行节点" />
        <EmptyState
          title="还没有主机"
          description="添加您的第一台测试执行节点"
          icon={<Server className="w-16 h-16" />}
          action={
            isAdmin ? (
              <Button onClick={() => setIsModalOpen(true)}>
                <Plus className="w-4 h-4 mr-2" />
                添加主机
              </Button>
            ) : undefined
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
    <PageContainer
      width="full"
      className={selectedHostIds.size > 0 ? 'pb-28' : undefined}
    >
      <PageHeader title="主机管理" subtitle="管理和监控测试执行节点" />

      <div className="flex items-center justify-end gap-2 py-2">
        {!opPanelOpen && hostOps.length > 0 && (
          <Button
            variant="outline"
            data-testid="host-op-panel-reopen"
            onClick={() => setOpPanelOpen(true)}
          >
            安装进度
            {installPending
              ? ` (${hostOps.filter((o) => o.status === 'pending' || o.status === 'running').length} 进行中)`
              : ` (${hostOps.filter((o) => o.status === 'success').length} 成功 / ${hostOps.filter((o) => o.status === 'failed').length} 失败)`}
          </Button>
        )}
        {isAdmin && (
          <Button onClick={() => setIsModalOpen(true)}>
            <Plus className="w-4 h-4" />
            添加主机
          </Button>
        )}
      </div>

      {/* Host Table */}
      {tableData.length > 0 ? (
        <ExpandableHostTable
          hosts={tableData}
          onHotUpdate={isAdmin ? handleHotUpdate : undefined}
          isHotUpdating={(hostId: string | number) => hotUpdateMutation.isPending && hotUpdatingHostId === hostId}
          onInstall={isAdmin ? handleInstall : undefined}
          isInstalling={(hostId: string | number) => isHostBusy(hostId)}
          onEdit={isAdmin ? handleEdit : undefined}
          onDelete={isAdmin ? handleDelete : undefined}
          isDeleting={(hostId: string | number) => deleteMutation.isPending && deleteMutation.variables === hostId}
          onWatcherAdminStateChange={handleWatcherAdminStateChange}
          isWatcherAdminStateUpdating={(hostId: string | number) =>
            watcherAdminStateMutation.isPending && watcherAdminUpdatingHostId === hostId
          }
          canManageWatcherAdminState={canManageWatcherAdminState}
          isAdmin={isAdmin}
          selectedIds={selectedHostIds}
          onSelectionChange={setSelectedHostIds}
        />
      ) : (
        <Card className="p-12 text-center">
          <h3 className={cn('text-lg font-medium mb-2', TEXT.heading)}>暂无主机</h3>
          <p className={cn('text-sm mb-4', TEXT.subtitle)}>添加您的第一台主机以开始使用。</p>
          {isAdmin && (
            <Button onClick={() => setIsModalOpen(true)}>
              <Plus className="w-4 h-4" />
              添加主机
            </Button>
          )}
        </Card>
      )}

      {isAdmin && (
        <HostBulkActionBar
          counts={bulkCounts}
          isAdmin={isAdmin}
          installPending={installPending}
          onInstall={handleBulkInstall}
          onHotUpdate={handleSelectedHotUpdate}
          onDelete={handleBulkDelete}
          onClear={() => setSelectedHostIds(new Set())}
        />
      )}

      <AddHostModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSubmit={(data) => createMutation.mutate(data)}
        isSubmitting={createMutation.isPending}
      />

      <AddHostModal
        isOpen={editingHost != null}
        editingHost={editingHost}
        onClose={() => {
          if (!updateMutation.isPending) setEditingHost(null);
        }}
        onSubmit={handleEditSubmit}
        isSubmitting={updateMutation.isPending}
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

      <HostOperationPanel
        open={opPanelOpen}
        ops={hostOps}
        onClose={closePanel}
        onTerminalStatus={handleInstallTerminalStatus}
      />
    </PageContainer>
  );
}
