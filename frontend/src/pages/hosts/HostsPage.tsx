import { useMemo, useState } from 'react';
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
  // E1：此前 canManageWatcherAdminState 与 isAdmin 是**同一判定的两个独立表达式**，
  // 名字暗示两种权限，实际永远同值——改一处忘另一处就会静默漂移。能力名保留
  // （ExpandableHostTable 按能力收 prop，将来分化时不必改调用方），真值来源只留一个。
  const isAdmin = sessionQ.data?.role === 'admin';
  const canManageWatcherAdminState = isAdmin;

  // ADR-0038 D5：「显示已退役」开关——默认走共享缓存键（三页同口径：不含退役），
  // 打开时走独立键（避免把含退役的列表写进三页共享缓存），前缀同为 ['hosts']，
  // 既有 invalidateQueries({queryKey: ['hosts']}) 仍同时覆盖两者。
  const [showRetired, setShowRetired] = useState(false);
  const { data: hostsData, isLoading, error } = useQuery({
    queryKey: showRetired ? hostKeys.retiredList() : hostKeys.list(),
    queryFn: () => fetchHostList(0, 200, showRetired),
    refetchInterval: 10000,
  });
  const hosts = useMemo(() => coerceHostList(hostsData), [hostsData]);
  const liveHostIds = useMemo(
    () => new Set(hosts.map((host) => String(host.id))),
    [hosts],
  );
  const visibleSelectedHostIds = useMemo(() => {
    if (selectedHostIds.size === 0) return selectedHostIds;
    const next = new Set(
      Array.from(selectedHostIds).filter((id) => liveHostIds.has(String(id))),
    );
    return next.size === selectedHostIds.size ? selectedHostIds : next;
  }, [selectedHostIds, liveHostIds]);

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

  // ADR-0038 D2：退役 / 解除退役（admin；原因必填，写入审计 who/when/reason）
  const retireMutation = useMutation({
    mutationFn: ({ hostId, reason }: { hostId: string | number; reason: string }) =>
      api.hosts.retire(hostId, reason),
    onSuccess: (host) => {
      void queryClient.invalidateQueries({ queryKey: hostKeys.list() });
      void queryClient.invalidateQueries({ queryKey: hostKeys.retiredList() });
      toast.success(`主机 ${host.id} 已退役`);
    },
    onError: (error: unknown) => {
      toast.error(`退役失败: ${toApiError(error).message}`);
    },
  });

  const unretireMutation = useMutation({
    mutationFn: ({ hostId, reason }: { hostId: string | number; reason: string }) =>
      api.hosts.unretire(hostId, reason),
    onSuccess: (host) => {
      void queryClient.invalidateQueries({ queryKey: hostKeys.list() });
      void queryClient.invalidateQueries({ queryKey: hostKeys.retiredList() });
      toast.success(`主机 ${host.id} 已解除退役`);
    },
    onError: (error: unknown) => {
      toast.error(`解除退役失败: ${toApiError(error).message}`);
    },
  });

  const askRetireReason = (action: '退役' | '解除退役', label: string): string | null => {
    const raw = window.prompt(`${action} ${label} 的原因（必填，写入审计）`);
    if (raw === null) return null;
    const reason = raw.trim();
    if (!reason) {
      toast.error(`${action}原因不能为空`);
      return null;
    }
    return reason;
  };

  const handleRetire = (host: HostTableData) => {
    const reason = askRetireReason('退役', host.name || String(host.id));
    if (reason) retireMutation.mutate({ hostId: host.id, reason });
  };

  const handleUnretire = (host: HostTableData) => {
    const reason = askRetireReason('解除退役', host.name || String(host.id));
    if (reason) unretireMutation.mutate({ hostId: host.id, reason });
  };

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
        // ADR-0038 D5：退役主机不接受控制面动作（仅 unretire 除外）
        if (full.retired_at) return null;
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
    const selectedIds = Array.from(visibleSelectedHostIds);
    const retiredCount = selectedIds.filter((id) =>
      hosts?.some((h: Host) => h.id === id && h.retired_at),
    ).length;
    if (retiredCount > 0) {
      toast.error(`已跳过 ${retiredCount} 台已退役主机（不接受安装/热更新）`);
    }
    const targets = resolveInstallTargets(selectedIds);
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
    if (visibleSelectedHostIds.size === 0) return;
    const ok = await confirmDialog({
      description: `确定删除选中的 ${visibleSelectedHostIds.size} 台主机？此操作不可恢复。`,
      variant: 'destructive',
    });
    if (!ok) return;
    // C6：受控并发（与 DevicesPage 批量标签同模式），失败汇总而非逐条静默
    const ids = Array.from(visibleSelectedHostIds);
    let cursor = 0;
    let succeeded = 0;
    const failed: string[] = [];
    const workers = Array.from({ length: Math.min(5, ids.length) }, async () => {
      while (cursor < ids.length) {
        const id = ids[cursor++];
        try {
          await api.hosts.delete(id);
          succeeded += 1;
        } catch (err: unknown) {
          // #1807：不得吞 409 文案（退役/有历史依赖等阻断原因要透出给操作者）
          const message = toApiError(err).message;
          failed.push(message ? `${id}（${message}）` : String(id));
        }
      }
    });
    await Promise.all(workers);
    if (failed.length > 0) {
      toast.error(
        `批量删除完成：成功 ${succeeded}，失败 ${failed.length}（${failed.slice(0, 3).join('、')}${failed.length > 3 ? ' 等' : ''}）`,
      );
    } else {
      toast.success(`已删除 ${succeeded} 台主机`);
    }
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
        retired_at: host.retired_at ?? null,
        retired_by: host.retired_by ?? null,
        retire_reason: host.retire_reason ?? null,
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
        usb_device_count: optionalNumber(host.capacity?.usb_device_count) ?? null,
        claim_hint: claimHint,
        active_tasks: host.capacity?.active_jobs ?? host.active_job_count ?? 0,
        health_status: host.health?.status,
        health_reasons: host.health?.reasons,
      };
    });
  }, [hosts]);

  const bulkCounts = useMemo(() => {
    const selected = Array.from(visibleSelectedHostIds)
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
      selected: visibleSelectedHostIds.size,
      firstInstall,
      reinstall,
      hotUpdate,
    };
  }, [visibleSelectedHostIds, hosts]);

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
    if (visibleSelectedHostIds.size === 0 || bulkHotUpdateProgress) return;
    if (visibleSelectedHostIds.size === 1) {
      const [hostId] = Array.from(visibleSelectedHostIds);
      const host = hosts?.find((item: Host) => item.id === hostId);
      if (!host || host.status !== 'ONLINE') {
        toast.info('请选择一台在线主机进行热更新');
        return;
      }
      handleHotUpdate(hostId);
      return;
    }

    const targets = Array.from(visibleSelectedHostIds)
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

  // E2：新增 / 编辑两个模态此前在 empty 分支与主分支各声明一遍（共 3 处），
  // 任一 prop 改漏一处就会静默不同步。抽成片段后全局只有这一份。
  const hostModals = (
    <>
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
    </>
  );

  // #2051：开关必须在**两个**分支都可见——`tableData` 由 `include_retired`
  // 过滤而来，最后一台在用主机被退役后页面只剩空态；开关若只在有数据分支渲染，
  // 用户就再也没有 UI 路径勾选它看到退役主机、进而解除退役（ADR-0038 回收路径断头）。
  const retiredToggle = (
    <label className="flex items-center gap-2 text-sm text-muted-foreground">
      <input
        type="checkbox"
        checked={showRetired}
        onChange={(e) => setShowRetired(e.target.checked)}
        data-testid="hosts-show-retired"
        className="rounded"
      />
      显示已退役
    </label>
  );

  if (isLoading) {
    return (
      <PageContainer width="wide">
        <PageHeader title="主机集群" subtitle="管理和监控测试执行节点" />
        <PageSkeleton>
          <PageSkeleton.Stats count={4} />
          <PageSkeleton.Block size="lg" />
        </PageSkeleton>
      </PageContainer>
    );
  }

  if (error) {
    // D3：无 HTTP 状态码 = 请求压根没到后端（网络 / 服务未起）；有状态码 =
    // 后端给出的业务错误，把状态码与后端 message 说出来，别一律甩给
    // 「请检查后端服务连接」——那会把 4xx 误报成运维故障。
    const apiError = toApiError(error);
    const unreachable = apiError.status == null;
    return (
      <PageContainer width="wide">
        <PageHeader title="主机集群" subtitle="管理和监控测试执行节点" />
        <ErrorState
          title={unreachable ? '无法连接后端服务' : `加载主机失败（${apiError.status}）`}
          description={unreachable ? '请检查后端服务连接与网络。' : apiError.message}
          onRetry={() => queryClient.invalidateQueries({ queryKey: hostKeys.list() })}
        />
      </PageContainer>
    );
  }

  if (tableData.length === 0) {
    return (
      <PageContainer width="wide">
        <PageHeader title="主机集群" subtitle="管理和监控测试执行节点" />
        {/* #2051：空态也要给「显示已退役」开关，否则全退役后无法解除退役 */}
        <div className="flex items-center justify-end gap-2 py-2">{retiredToggle}</div>
        <EmptyState
          title={showRetired ? '还没有主机' : '没有在用主机'}
          description={
            showRetired
              ? '当前没有任何主机记录。'
              : '所有主机都已退役。勾选「显示已退役」可查看并解除退役。'
          }
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
        {hostModals}
      </PageContainer>
    );
  }

  return (
    <PageContainer width="wide">
      <PageHeader title="主机集群" subtitle="管理和监控测试执行节点" />

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
        {retiredToggle}
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
          (bulkHotUpdateProgress != null && visibleSelectedHostIds.has(hostId))
        }
        onInstall={isAdmin ? handleInstall : undefined}
        isInstalling={(hostId: string | number) =>
          isHostOpBusy(hostId, ['install', 'reinstall'])
        }
        onEdit={isAdmin ? handleEdit : undefined}
        onDelete={isAdmin ? handleDelete : undefined}
        onRetire={isAdmin ? handleRetire : undefined}
        onUnretire={isAdmin ? handleUnretire : undefined}
        isDeleting={(hostId: string | number) => deleteMutation.isPending && deleteMutation.variables === hostId}
        onWatcherAdminStateChange={handleWatcherAdminStateChange}
        isWatcherAdminStateUpdating={(hostId: string | number) =>
          watcherAdminStateMutation.isPending && watcherAdminUpdatingHostId === hostId
        }
        canManageWatcherAdminState={canManageWatcherAdminState}
        isAdmin={isAdmin}
        selectedIds={visibleSelectedHostIds}
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

      {hostModals}

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
