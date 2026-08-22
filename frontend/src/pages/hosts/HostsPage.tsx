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
import { api, coerceHostList, fetchHostList, toApiError } from '@/utils/api';
import type { Host } from '@/utils/api/types';
import { hostKeys } from '@/utils/api/queryKeys';
import { Button } from '@/components/ui/button';
import { PageContainer, PageHeader } from '@/components/layout';
import { ErrorState } from '@/components/ui/error-state';
import { EmptyState } from '@/components/ui/empty-state';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import { BULK_HOT_UPDATE_SKIP_LABEL, precheckBulkHotUpdate } from './bulkHotUpdate';

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function optionalNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

/** 把批处理返回的 id 对齐回列表里的原始类型，避免 number/string 对不上勾选。 */
function resolveSelectedHostId(
  id: string | number,
  hostList: Host[],
): string | number {
  const key = String(id);
  const found = hostList.find((host) => String(host.id) === key);
  return found ? found.id : key;
}

export default function HostsPage() {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedHostIds, setSelectedHostIds] = useState<Set<string | number>>(new Set());
  const queryClient = useQueryClient();
  const toast = useToast();
  const confirmDialog = useConfirm();
  const sessionQ = useAuthSession();
  const canManageWatcherAdminState = sessionQ.data?.role === 'admin';
  const isAdmin = sessionQ.data?.role === 'admin';

  const { data: hostsData, isLoading, error } = useQuery({
    queryKey: hostKeys.list(),
    queryFn: () => fetchHostList(0, 200),
    refetchInterval: 10000,
  });
  const hosts = useMemo(() => coerceHostList(hostsData), [hostsData]);

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
    onError: (error: unknown) => {
      toast.error(`添加主机失败: ${toApiError(error).message}`);
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
    onError: (error: unknown) => {
      toast.error(`更新主机失败: ${toApiError(error).message}`);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (hostId: string | number) => api.hosts.delete(hostId),
    onSuccess: (_data, hostId) => {
      queryClient.invalidateQueries({ queryKey: hostKeys.list() });
      toast.success(`主机 ${hostId} 已删除`);
    },
    onError: (error: unknown) => {
      toast.error(`删除主机失败: ${toApiError(error).message}`);
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
    startHotUpdateBatch,
    markTerminal,
    closePanel,
    isHostOpBusy,
  } = useHostOperations({
    concurrency: 2,
    onTerminal: (ev) => {
      if (ev.kind === 'hot_update') {
        if (ev.ok) {
          queryClient.invalidateQueries({ queryKey: hostKeys.list() });
          queryClient.invalidateQueries({ queryKey: ['host-detail', ev.hostId] });
        }
        return;
      }
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

  const [pendingHotUpdateHostId, setPendingHotUpdateHostId] = useState<
    number | string | null
  >(null);
  const [pendingRetryAfter, setPendingRetryAfter] = useState<number | undefined>(
    undefined,
  );
  const [bulkHotUpdateProgress, setBulkHotUpdateProgress] = useState<{
    phase: 'checking' | 'updating';
    completed: number;
    total: number;
  } | null>(null);

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
    onError: (error: unknown, vars) => {
      toast.error(
        `更新 Watch 状态失败: ${toApiError(error).message || `host ${vars.hostId}`}`,
      );
      setWatcherAdminUpdatingHostId(null);
    },
  });

  const handleHotUpdate = (hostId: number | string) => {
    if (bulkHotUpdateProgress) {
      toast.info('安全批量热更新正在执行，请等待完成');
      return;
    }
    setPendingRetryAfter(undefined);
    setPendingHotUpdateHostId(hostId);
  };

  const handleHotUpdateConfirm = async (
    hostId: number | string,
    opts: { abortRunningJobs: boolean },
  ) => {
    const host = hosts?.find((item: Host) => item.id === hostId);
    const label = host?.name ?? host?.ip ?? String(hostId);
    setPendingHotUpdateHostId(null);
    setPendingRetryAfter(undefined);
    const result = await startHotUpdateBatch([
      { hostId, label, abortRunningJobs: opts.abortRunningJobs },
    ]);
    if (!result) {
      toast.info('已有主机操作进行中，请等待完成后再热更新');
      return;
    }
    const conflict = result.skipped.find((item) => item.httpStatus === 409);
    if (conflict) {
      toast.error(
        conflict.activeJobCount
          ? `主机 ${label} 仍有 ${conflict.activeJobCount} 个活跃 Job — 请勾选「中止并热更新」`
          : `主机 ${label} 热更新被拒绝: ${conflict.error}`,
      );
      setPendingHotUpdateHostId(hostId);
      setPendingRetryAfter(conflict.retryAfterSeconds);
      return;
    }
    if (result.failed[0]) {
      toast.error(`主机 ${label} 热更新失败: ${result.failed[0].error}`);
      return;
    }
    const data = result.succeeded[0]?.result;
    if (!data) return;
    const depNote = data.deps_refreshed ? ' (依赖已刷新)' : ' (依赖未变)';
    const verNote = data.code_version ? ` @${data.code_version}` : '';
    toast.success(
      opts.abortRunningJobs
        ? `主机 ${label} 已中止活跃 Job 并完成热更新${depNote}${verNote}`
        : `主机 ${label} 热更新完成${depNote}${verNote}`,
    );
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
      variant: 'destructive',
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
      variant: 'destructive',
    });
    if (!ok) return;
    await startInstallBatch(targets);
    setSelectedHostIds(new Set());
  };

  const handleBulkDelete = async () => {
    if (selectedHostIds.size === 0) return;
    const ok = await confirmDialog({
      description: `确定删除选中的 ${selectedHostIds.size} 台主机？此操作不可恢复。`,
      variant: 'destructive',
    });
    if (!ok) return;
    for (const id of Array.from(selectedHostIds)) {
      try {
        await api.hosts.delete(id);
      } catch (error: unknown) {
        toast.error(`删除 ${id} 失败: ${toApiError(error).message}`);
      }
    }
    toast.success('批量删除已完成');
    setSelectedHostIds(new Set());
    queryClient.invalidateQueries({ queryKey: hostKeys.list() });
  };

  const handleEdit = (host: HostTableData) => {
    const full = hosts?.find((h) => h.id === host.id);
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
      variant: 'destructive',
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
        variant: 'destructive',
      });
      if (!ok) return;
    }
    setWatcherAdminUpdatingHostId(hostId);
    watcherAdminStateMutation.mutate({
      hostId,
      watcher_admin_active: nextActive,
    });
  };

  // Transform data for expandable table
  const tableData: HostTableData[] = useMemo(() => {
    if (!hosts) return [];
    return hosts.map((host) => {
      const extra = host.extra && typeof host.extra === 'object'
        ? host.extra as Record<string, unknown> : {};
      const diskInfo = extra.disk_usage && typeof extra.disk_usage === 'object'
        ? extra.disk_usage as Record<string, unknown> : {};
      const onlineDevices =
        host.status === 'ONLINE'
          ? (host.capacity?.online_healthy_devices ?? 0)
          : 0;
      let claimHint: string | null = null;
      if (onlineDevices > 0 && host.status === 'ONLINE') {
        const busy = host.capacity?.active_devices ?? 0;
        const claimable =
          host.capacity?.available_slots
          ?? Math.max(0, onlineDevices - busy);
        const parts = [`${claimable} 可认领`];
        if (busy > 0) {
          parts.push(`${busy} 租约占用`);
        }
        claimHint = parts.join(' · ');
      }

      return {
        id: host.id,
        name: host.name ?? '',
        ip: host.ip ?? '',
        status: host.status,
        watcher_admin_active: host.watcher_admin_active !== false,
        last_heartbeat: host.last_heartbeat ?? undefined,
        agent_installed: Boolean(host.agent_installed),
        agent_protocol_version:
          host.agent_protocol_version ??
          (typeof host.extra?.agent_version === 'string' ? host.extra.agent_version : null),
        agent_code_revision: host.agent_code_revision ?? null,
        expected_code_revision: host.expected_code_revision ?? null,
        agent_code_deployed: host.agent_code_deployed ?? null,
        agent_code_deployed_at: host.agent_code_deployed_at ?? null,
        agent_code_sync_status: host.agent_code_sync_status ?? 'unknown',
        resources: host.status === 'ONLINE' ? {
          cpu_load: asNumber(extra.cpu_load),
          cpu_cores: optionalNumber(extra.cpu_cores),
          ram_usage: asNumber(extra.ram_usage),
          ram_total_gb: optionalNumber(extra.ram_total_gb),
          disk_usage: optionalNumber(diskInfo.usage_percent) ?? null,
          disk_total_gb: optionalNumber(diskInfo.total_gb),
          temperature: optionalNumber(extra.temperature),
          uptime_seconds: optionalNumber(extra.uptime_seconds),
        } : undefined,
        mount_status: host.mount_status
          ? Object.entries(host.mount_status).map(([path, info]: [string, unknown]) => {
              const mount = typeof info === 'object' && info !== null
                ? info as Record<string, unknown> : {};
              return {
                path,
                mounted: mount.ok === true || info === true,
                available_gb: typeof mount.available_gb === 'number' ? mount.available_gb : undefined,
                total_gb: typeof mount.total_gb === 'number' ? mount.total_gb : undefined,
              };
            })
          : [],
        device_count: onlineDevices,
        claim_hint: claimHint,
        active_tasks: host.capacity?.active_jobs ?? host.active_job_count ?? 0,
        health_status: host.health?.status,
        health_reasons: host.health?.reasons,
      };
    });
  }, [hosts]);

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
    (op) =>
      (op.kind === 'install' || op.kind === 'reinstall') &&
      (op.status === 'pending' || op.status === 'running'),
  );
  const hotUpdateOpPending = hostOps.some(
    (op) => op.kind === 'hot_update' && (op.status === 'pending' || op.status === 'running'),
  );
  const hotUpdatePanelOps = hostOps.some((op) => op.kind === 'hot_update');

  const handleSelectedHotUpdate = async () => {
    if (selectedHostIds.size === 0 || bulkHotUpdateProgress) return;
    if (selectedHostIds.size === 1) {
      const [hostId] = Array.from(selectedHostIds);
      const host = hosts?.find((item: Host) => item.id === hostId);
      if (!host || host.status !== 'ONLINE') {
        toast.info('请选择一台在线主机进行热更新');
        return;
      }
      handleHotUpdate(hostId);
      return;
    }

    const targets = Array.from(selectedHostIds)
      .map((id) => hosts?.find((host: Host) => host.id === id))
      .filter((host): host is Host => Boolean(host))
      .map((host) => ({
        id: host.id,
        label: host.name ?? host.ip ?? String(host.id),
      }));
    if (targets.length === 0) return;

    setBulkHotUpdateProgress({ phase: 'checking', completed: 0, total: targets.length });
    try {
      const precheck = await precheckBulkHotUpdate(
        targets,
        api.hosts.getDetail,
        (completed, total) => setBulkHotUpdateProgress({ phase: 'checking', completed, total }),
      );
      const activeJobs = precheck.skipped.filter((item) => item.reason === 'active_jobs').length;
      const unavailable = precheck.skipped.filter(
        (item) => item.reason === 'offline' || item.reason === 'not_installed',
      ).length;
      const checkFailed = precheck.skipped.filter((item) => item.reason === 'precheck_failed').length;

      const skippedSeeds = precheck.skipped.map((item) => ({
        hostId: item.id,
        label: item.label,
        error: BULK_HOT_UPDATE_SKIP_LABEL[item.reason],
      }));

      if (precheck.eligible.length === 0) {
        toast.info(
          `没有可安全热更新的主机：活跃 Job ${activeJobs} 台，离线/未安装 ${unavailable} 台，预检失败 ${checkFailed} 台`,
        );
        const skippedOnly = await startHotUpdateBatch([], { skipped: skippedSeeds });
        if (skippedOnly) {
          setSelectedHostIds(
            new Set(
              skippedOnly.skipped.map((item) => resolveSelectedHostId(item.hostId, hosts ?? [])),
            ),
          );
        }
        return;
      }

      const ok = await confirmDialog({
        title: '确认安全批量热更新',
        description:
          `预检完成：可热更新 ${precheck.eligible.length} 台；` +
          `将跳过活跃 Job ${activeJobs} 台、离线/未安装 ${unavailable} 台、预检失败 ${checkFailed} 台。` +
          '系统将以并发 2 逐台重启 Agent，执行期间不会中止任何 Job。是否继续？',
        confirmText: `热更新 ${precheck.eligible.length} 台`,
      });
      if (!ok) return;

      setBulkHotUpdateProgress({ phase: 'updating', completed: 0, total: precheck.eligible.length });
      const result = await startHotUpdateBatch(
        precheck.eligible.map((item) => ({ hostId: item.id, label: item.label })),
        {
          skipped: skippedSeeds,
          onProgress: (completed, total) =>
            setBulkHotUpdateProgress({ phase: 'updating', completed, total }),
        },
      );
      if (!result) {
        toast.info('已有主机操作进行中，请等待完成后再热更新');
        return;
      }
      const remainingIds = new Set<string | number>([
        ...result.skipped.map((item) => resolveSelectedHostId(item.hostId, hosts ?? [])),
        ...result.failed.map((item) => resolveSelectedHostId(item.hostId, hosts ?? [])),
      ]);
      setSelectedHostIds(remainingIds);
      queryClient.invalidateQueries({ queryKey: hostKeys.list() });
      queryClient.invalidateQueries({ queryKey: ['host-detail'] });

      const skippedCount = result.skipped.length;
      const summary = `安全批量热更新完成：成功 ${result.succeeded.length} 台，跳过 ${skippedCount} 台，失败 ${result.failed.length} 台`;
      if (result.failed.length > 0) toast.error(summary);
      else toast.success(summary);
    } finally {
      setBulkHotUpdateProgress(null);
    }
  };

  if (isLoading) {
    return (
      <PageContainer width="wide">
        <PageHeader title="主机管理" subtitle="管理和监控测试执行节点" />
        <PageSkeleton>
          <PageSkeleton.Stats count={4} />
          <PageSkeleton.Block size="lg" />
        </PageSkeleton>
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
    <PageContainer width="wide">
      <PageHeader title="主机管理" subtitle="管理和监控测试执行节点" />

      <div className="flex items-center justify-end gap-2 py-2">
        {!opPanelOpen && hostOps.length > 0 && (
          <Button
            variant="outline"
            data-testid="host-op-panel-reopen"
            onClick={() => setOpPanelOpen(true)}
          >
            {hotUpdatePanelOps ? '热更新进度' : '安装进度'}
            {installPending || hotUpdateOpPending
              ? ` (${hostOps.filter((o) => o.status === 'pending' || o.status === 'running').length} 进行中)`
              : ` (${hostOps.filter((o) => o.status === 'success').length} 成功 / ${hostOps.filter((o) => o.status === 'failed').length} 失败${hostOps.some((o) => o.status === 'skipped') ? ` / ${hostOps.filter((o) => o.status === 'skipped').length} 跳过` : ''})`}
          </Button>
        )}
        {isAdmin && (
          <Button onClick={() => setIsModalOpen(true)}>
            <Plus className="w-4 h-4" />
            添加主机
          </Button>
        )}
      </div>

      {/* Host Table —— tableData 为空的分支在 :567 已提前 return，此处无需再判 */}
      <ExpandableHostTable
        hosts={tableData}
        onHotUpdate={isAdmin ? handleHotUpdate : undefined}
        isHotUpdating={(hostId: string | number) =>
          isHostOpBusy(hostId, 'hot_update') ||
          (bulkHotUpdateProgress != null && selectedHostIds.has(hostId))
        }
        onInstall={isAdmin ? handleInstall : undefined}
        isInstalling={(hostId: string | number) =>
          isHostOpBusy(hostId, ['install', 'reinstall'])
        }
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

      {isAdmin && (
        <HostBulkActionBar
          counts={bulkCounts}
          isAdmin={isAdmin}
          installPending={installPending || hotUpdateOpPending}
          hotUpdatePending={bulkHotUpdateProgress != null || hotUpdateOpPending || installPending}
          hotUpdateProgressLabel={bulkHotUpdateProgress
            ? `${bulkHotUpdateProgress.phase === 'checking' ? '预检' : '热更新'} ${bulkHotUpdateProgress.completed}/${bulkHotUpdateProgress.total}`
            : hotUpdateOpPending
              ? `热更新 ${hostOps.filter((o) => o.kind === 'hot_update' && o.status === 'success').length}/${hostOps.filter((o) => o.kind === 'hot_update' && o.status !== 'skipped').length}`
              : undefined}
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
          if (!hotUpdateOpPending) setPendingHotUpdateHostId(null);
        }}
        onConfirm={handleHotUpdateConfirm}
        isHotUpdatePending={hotUpdateOpPending}
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
