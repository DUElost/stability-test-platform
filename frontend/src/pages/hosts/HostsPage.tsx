import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { keepPreviousData, useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Server } from 'lucide-react';
import { useUrlFilterParams } from '@/hooks/useUrlFilterState';
import { useToast } from '@/hooks/useToast';
import { useConfirm } from '@/hooks/useConfirm';
import { useAuthSession } from '@/hooks/useAuthSession';
import { useHostOperations } from '@/hooks/useHostOperations';
import {
  ExpandableHostTable,
  type HostHealthFilter,
  type HostSortDirection,
  type HostSortKey,
  type HostSyncFilter,
  type HostTableData,
} from '@/components/network/ExpandableHostTable';
import { AddHostModal } from './components/AddHostModal';
import { RetireHostDialog, type HostRetireMode } from './components/RetireHostDialog';
import HostHotUpdateConfirmDialog from '@/components/host/HostHotUpdateConfirmDialog';
import HostBulkActionBar from '@/components/host/HostBulkActionBar';
import HostOperationPanel from '@/components/host/HostOperationPanel';
import { api, toApiError } from '@/utils/api';
import type {
  Host,
  HostFilterQueryParams,
  HostListSortBy,
  HostSelectionPreview,
} from '@/utils/api/types';
import { hostKeys, scriptPresenceKeys } from '@/utils/api/queryKeys';
import { Button } from '@/components/ui/button';
import { PageContainer, PageHeader } from '@/components/layout';
import { ErrorState } from '@/components/ui/error-state';
import { EmptyState } from '@/components/ui/empty-state';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import { BULK_HOT_UPDATE_SKIP_LABEL, precheckBulkHotUpdate } from './bulkHotUpdate';

const PAGE_SIZE_OPTIONS = [50, 100, 200] as const;

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

function mapHostToTableData(host: Host): HostTableData {
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

  const capRaw =
    host.capacity && typeof host.capacity === 'object'
      ? (host.capacity as Record<string, unknown>)
      : {};
  const topoRaw =
    capRaw.usb_hub_topology && typeof capRaw.usb_hub_topology === 'object'
      ? (capRaw.usb_hub_topology as Record<string, unknown>)
      : null;
  const usbHubTopology =
    topoRaw &&
    typeof topoRaw.cascade_hubs === 'number' &&
    typeof topoRaw.unbound_hubs === 'number' &&
    typeof topoRaw.unbound_idle_bus_hubs === 'number'
      ? {
          cascade_hubs: topoRaw.cascade_hubs,
          unbound_hubs: topoRaw.unbound_hubs,
          unbound_idle_bus_hubs: topoRaw.unbound_idle_bus_hubs,
        }
      : null;
  const kerrRaw =
    capRaw.usb_kernel_errors_1h && typeof capRaw.usb_kernel_errors_1h === 'object'
      ? (capRaw.usb_kernel_errors_1h as Record<string, unknown>)
      : null;
  const usbKernelErrors1h =
    kerrRaw &&
    typeof kerrRaw.link_errors === 'number' &&
    typeof kerrRaw.cable_suspect === 'number' &&
    typeof kerrRaw.disconnects === 'number' &&
    typeof kerrRaw.resets === 'number' &&
    typeof kerrRaw.enum_errors === 'number' &&
    typeof kerrRaw.hub_port_errors === 'number'
      ? {
          link_errors: kerrRaw.link_errors,
          cable_suspect: kerrRaw.cable_suspect,
          disconnects: kerrRaw.disconnects,
          resets: kerrRaw.resets,
          enum_errors: kerrRaw.enum_errors,
          hub_port_errors: kerrRaw.hub_port_errors,
        }
      : null;
  const ctrlRaw =
    capRaw.usb_controller_health && typeof capRaw.usb_controller_health === 'object'
      ? (capRaw.usb_controller_health as Record<string, unknown>)
      : null;
  const usbControllerHealth =
    ctrlRaw &&
    typeof ctrlRaw.unbound_xhci_controllers === 'number' &&
    typeof ctrlRaw.disabled_root_ports === 'number' &&
    typeof ctrlRaw.disabled_hub_ports === 'number' &&
    typeof ctrlRaw.over_current_ports === 'number' &&
    typeof ctrlRaw.stuck_default_ports === 'number'
      ? {
          unbound_xhci_controllers: ctrlRaw.unbound_xhci_controllers,
          disabled_root_ports: ctrlRaw.disabled_root_ports,
          disabled_hub_ports: ctrlRaw.disabled_hub_ports,
          over_current_ports: ctrlRaw.over_current_ports,
          stuck_default_ports: ctrlRaw.stuck_default_ports,
        }
      : null;
  const adbStatesRaw =
    capRaw.adb_state_counts && typeof capRaw.adb_state_counts === 'object'
      ? (capRaw.adb_state_counts as Record<string, unknown>)
      : null;
  const adbStateCounts = adbStatesRaw
    ? {
        device: optionalNumber(adbStatesRaw.device),
        offline: optionalNumber(adbStatesRaw.offline),
        unauthorized: optionalNumber(adbStatesRaw.unauthorized),
        other: optionalNumber(adbStatesRaw.other),
      }
    : null;
  const rawKernelLog = capRaw.usb_kernel_log;
  const usbKernelLog =
    rawKernelLog === 'ok' || rawKernelLog === 'unavailable' || rawKernelLog === 'unknown'
      ? rawKernelLog
      : null;
  const probeCandidate =
    (host as { health_probe?: unknown }).health_probe ?? extra.health_probe;
  const probeRaw =
    probeCandidate && typeof probeCandidate === 'object'
      ? (probeCandidate as Record<string, unknown>)
      : null;
  const healthProbe = probeRaw
    ? {
        checked_at: typeof probeRaw.checked_at === 'string' ? probeRaw.checked_at : undefined,
        verdict: typeof probeRaw.verdict === 'string' ? probeRaw.verdict : undefined,
        signals: Array.isArray(probeRaw.signals)
          ? probeRaw.signals.filter((s): s is string => typeof s === 'string')
          : undefined,
        topology: typeof probeRaw.topology === 'string' ? probeRaw.topology : undefined,
        recent_verdicts: Array.isArray(probeRaw.recent_verdicts)
          ? probeRaw.recent_verdicts.filter((s): s is string => typeof s === 'string')
          : undefined,
        strike_open: typeof probeRaw.strike_open === 'boolean' ? probeRaw.strike_open : undefined,
        strike_need: typeof probeRaw.strike_need === 'number' ? probeRaw.strike_need : undefined,
        uhubctl_status:
          typeof probeRaw.uhubctl_status === 'string' ? probeRaw.uhubctl_status : undefined,
        powered_off_ports: Array.isArray(probeRaw.powered_off_ports)
          ? probeRaw.powered_off_ports.filter((s): s is string => typeof s === 'string')
          : undefined,
      }
    : null;
  const powerSlotsRaw =
    capRaw.usb_power_slots && typeof capRaw.usb_power_slots === 'object'
      ? (capRaw.usb_power_slots as Record<string, unknown>)
      : null;
  const usbPowerSlotSerials: Record<string, string> | null = powerSlotsRaw
    ? (() => {
        const inverted: Record<string, string> = {};
        for (const [serial, entry] of Object.entries(powerSlotsRaw)) {
          if (Array.isArray(entry) && typeof entry[0] === 'string' && entry[0].trim()) {
            inverted[entry[0].trim()] = serial;
          }
        }
        return Object.keys(inverted).length > 0 ? inverted : null;
      })()
    : null;
  const selfHealCandidate =
    (host as { self_heal?: unknown }).self_heal ?? extra.self_heal;
  const selfHealRaw =
    selfHealCandidate && typeof selfHealCandidate === 'object'
      ? (selfHealCandidate as Record<string, unknown>)
      : null;
  const selfHeal = selfHealRaw
    ? {
        state: typeof selfHealRaw.state === 'string' ? selfHealRaw.state : undefined,
        fuse_open: typeof selfHealRaw.fuse_open === 'boolean' ? selfHealRaw.fuse_open : undefined,
        fuse_reason: typeof selfHealRaw.fuse_reason === 'string' ? selfHealRaw.fuse_reason : null,
        xhci:
          selfHealRaw.xhci && typeof selfHealRaw.xhci === 'object'
            ? (selfHealRaw.xhci as Host['self_heal'] extends infer T ? T extends { xhci?: infer X } ? X : never : never)
            : undefined,
        adb_reconnect:
          selfHealRaw.adb_reconnect && typeof selfHealRaw.adb_reconnect === 'object'
            ? (selfHealRaw.adb_reconnect as Host['self_heal'] extends infer T ? T extends { adb_reconnect?: infer A } ? A : never : never)
            : undefined,
      }
    : null;

  return {
    id: host.id,
    name: host.name ?? '',
    retired_at: host.retired_at ?? null,
    retired_by: host.retired_by ?? null,
    retire_reason: host.retire_reason ?? null,
    emptied_at: host.emptied_at ?? null,
    emptied_by: host.emptied_by ?? null,
    emptied_reason: host.emptied_reason ?? null,
    ip: host.ip ?? '',
    status: host.status,
    watcher_admin_active: host.watcher_admin_active !== false,
    last_heartbeat: host.last_heartbeat ?? undefined,
    agent_installed: Boolean(host.agent_installed),
    agent_protocol_version:
      host.agent_protocol_version ??
      (typeof host.extra?.agent_version === 'string' ? host.extra.agent_version : null),
    agent_code_revision: host.agent_code_revision ?? null,
    agent_artifact_digest: host.agent_artifact_digest ?? null,
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
    ledger_device_count: optionalNumber(host.ledger_device_count) ?? null,
    ledger_latest_seen_at: host.ledger_latest_seen_at ?? null,
    usb_device_count: optionalNumber(host.capacity?.usb_device_count) ?? null,
    adb_interface_count: optionalNumber(capRaw.adb_interface_count) ?? null,
    adb_state_counts: adbStateCounts,
    usb_kernel_log: usbKernelLog,
    usb_hub_topology: usbHubTopology,
    usb_kernel_errors_1h: usbKernelErrors1h,
    usb_controller_health: usbControllerHealth,
    usb_power_slot_serials: usbPowerSlotSerials,
    health_probe: healthProbe,
    self_heal: selfHeal,
    claim_hint: claimHint,
    active_tasks: host.capacity?.active_jobs ?? host.active_job_count ?? 0,
    health_status: host.health?.status,
    health_reasons: host.health?.reasons,
  };
}

/** 跨页冻结批量动作时解析不在当前页的主机详情，保证资格复验与标签准确且不重跑筛选。 */
async function resolveHostRecordsForIds(
  ids: Array<string | number>,
  pageHosts: Host[],
): Promise<Host[]> {
  const byKey = new Map<string, Host>();
  for (const h of pageHosts) {
    byKey.set(String(h.id), h);
  }
  const missing = ids.filter((id) => !byKey.has(String(id)));
  if (missing.length > 0) {
    let cursor = 0;
    const workers = Array.from({ length: Math.min(5, missing.length) }, async () => {
      while (cursor < missing.length) {
        const id = missing[cursor++];
        try {
          const detail = await api.hosts.getDetail(id);
          if (detail) byKey.set(String(detail.id), detail);
        } catch {
          // 离线/单机拉取失败时跳过，交由下游汇总反馈
        }
      }
    });
    await Promise.all(workers);
  }
  return ids
    .map((id) => byKey.get(String(id)))
    .filter((h): h is Host => Boolean(h));
}

export default function HostsPage() {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedHostIds, setSelectedHostIds] = useState<Set<string | number>>(new Set());
  const [crossPagePreview, setCrossPagePreview] = useState<HostSelectionPreview | null>(null);
  const [crossPagePreviewLoading, setCrossPagePreviewLoading] = useState(false);
  const queryClient = useQueryClient();
  const toast = useToast();
  const confirmDialog = useConfirm();
  const sessionQ = useAuthSession();
  // E1：此前 canManageWatcherAdminState 与 isAdmin 是**同一判定的两个独立表达式**，
  // 名字暗示两种权限，实际永远同值——改一处忘另一处就会静默漂移。能力名保留
  // （ExpandableHostTable 按能力收 prop，将来分化时不必改调用方），真值来源只留一个。
  const isAdmin = sessionQ.data?.role === 'admin';
  const canManageWatcherAdminState = isAdmin;

  const navigate = useNavigate();
  const { getString, getBool, updateParams, clearParams } = useUrlFilterParams();

  const clearSelection = useCallback(() => {
    setSelectedHostIds(new Set());
    setCrossPagePreview(null);
  }, []);

  const updateFilterParams = useCallback(
    (patch: Record<string, string | boolean | undefined>) => {
      clearSelection();
      updateParams({ ...patch, page: undefined });
    },
    [clearSelection, updateParams],
  );

  // ADR-0038 D5：「显示已退役」开关——同步到 URL 参数 retired=1
  const showRetired = getBool('retired');
  const setShowRetired = (next: boolean) => updateFilterParams({ retired: next });
  const rawStatus = getString('status', 'all');
  const statusFilter: 'all' | HostTableData['status'] =
    rawStatus === 'ONLINE' || rawStatus === 'OFFLINE' || rawStatus === 'DEGRADED'
      ? rawStatus
      : 'all';
  const searchQuery = getString('q', '');
  const rawHealth = getString('health', 'all');
  const healthFilter: HostHealthFilter =
    rawHealth === 'HEALTHY' ||
    rawHealth === 'DEGRADED' ||
    rawHealth === 'UNSCHEDULABLE' ||
    rawHealth === 'unhealthy'
      ? rawHealth
      : 'all';
  const rawSync = getString('sync', 'all');
  const syncFilter: HostSyncFilter =
    rawSync === 'upgrade_pending' ||
    rawSync === 'drift' ||
    rawSync === 'matched' ||
    rawSync === 'mount_error' ||
    rawSync === 'usb_fault'
      ? rawSync
      : 'all';
  const drawerHostId = getString('drawer', '') || null;

  const rawPage = Number.parseInt(getString('page', '1'), 10);
  const page = Number.isFinite(rawPage) && rawPage >= 1 ? rawPage : 1;
  const rawLimit = Number.parseInt(getString('limit', '50'), 10);
  const pageSize: number = (PAGE_SIZE_OPTIONS as readonly number[]).includes(rawLimit)
    ? rawLimit
    : 50;
  const rawSortBy = getString('sort_by', 'default');
  const sortKey: HostSortKey =
    rawSortBy === 'name' ||
    rawSortBy === 'devices' ||
    rawSortBy === 'cpu' ||
    rawSortBy === 'ram' ||
    rawSortBy === 'disk' ||
    rawSortBy === 'heartbeat'
      ? rawSortBy
      : 'default';
  const rawSortDir = getString('sort_dir', 'desc');
  const sortDirection: HostSortDirection = rawSortDir === 'asc' ? 'asc' : 'desc';

  const hasActiveFilters =
    statusFilter !== 'all' ||
    healthFilter !== 'all' ||
    syncFilter !== 'all' ||
    Boolean(searchQuery.trim());

  // 契约 §4：将前端筛选状态映射为后端增量子集参数（upgrade_pending 复用精确谓词）
  const hostFilterParams = useMemo<HostFilterQueryParams>(() => {
    const params: HostFilterQueryParams = {};
    if (showRetired) params.include_retired = true;
    if (statusFilter !== 'all') params.status = statusFilter;
    if (healthFilter !== 'all') params.health_status = healthFilter;
    if (syncFilter === 'upgrade_pending' || syncFilter === 'drift' || syncFilter === 'matched') {
      params.sync_status = syncFilter;
    } else if (syncFilter === 'usb_fault') {
      params.usb_anomaly = true;
    } else if (syncFilter === 'mount_error') {
      params.mount_anomaly = true;
    }
    if (searchQuery.trim()) params.q = searchQuery.trim();
    return params;
  }, [showRetired, statusFilter, healthFilter, syncFilter, searchQuery]);

  const apiSortBy: HostListSortBy = sortKey === 'default' ? 'priority' : sortKey;
  const apiSortDir: HostSortDirection = sortKey === 'default' ? 'desc' : sortDirection;
  const pageQueryParams = useMemo(
    () => ({
      ...hostFilterParams,
      skip: (page - 1) * pageSize,
      limit: pageSize,
      sort_by: apiSortBy,
      sort_dir: apiSortDir,
    }),
    [hostFilterParams, page, pageSize, apiSortBy, apiSortDir],
  );

  // B3-2：主列表走服务端单页分页（独立缓存键 hostKeys.page，不污染 ['hosts']）
  const {
    data: hostsPageData,
    isLoading,
    isFetching: isFetchingPage,
    error,
  } = useQuery({
    queryKey: hostKeys.page(pageQueryParams),
    queryFn: () => api.hosts.listPage(pageQueryParams),
    placeholderData: keepPreviousData,
    refetchInterval: 10000,
  });

  // B3-2：独立 10 项主机 Facets（仅依赖 showRetired，不受搜索/排序/翻页影响）
  const facetsQ = useQuery({
    queryKey: hostKeys.facets(showRetired),
    queryFn: () => api.hosts.facets(showRetired),
    refetchInterval: 10000,
  });

  const hosts = useMemo(() => hostsPageData?.items ?? [], [hostsPageData]);
  const filteredTotal = hostsPageData?.total ?? hosts.length;

  // 页码越界保护（例如末页主机被删后自动回落到末页）
  useEffect(() => {
    if (!hostsPageData) return;
    const maxPage = Math.max(1, Math.ceil(hostsPageData.total / pageSize));
    if (page > maxPage) {
      updateParams({ page: maxPage > 1 ? String(maxPage) : undefined });
    }
  }, [hostsPageData, page, pageSize, updateParams]);

  // 契约 §6.1：跨页 `?drawer=<id>` 深链不在当前页时独立拉单机详情
  const drawerHostInPage = useMemo(
    () => Boolean(drawerHostId && hosts.some((h) => String(h.id) === drawerHostId)),
    [drawerHostId, hosts],
  );
  const drawerHostQ = useQuery({
    queryKey: hostKeys.detail(drawerHostId ?? ''),
    queryFn: () => api.hosts.get(drawerHostId!),
    enabled: Boolean(drawerHostId) && !drawerHostInPage,
  });
  const drawerHostOverride = useMemo<HostTableData | null>(() => {
    if (!drawerHostId || drawerHostInPage || !drawerHostQ.data) return null;
    return mapHostToTableData(drawerHostQ.data);
  }, [drawerHostId, drawerHostInPage, drawerHostQ.data]);

  // #2362：空态区分「从未接入」与「全部退役」——仅在无筛选且在用全集为 0 时探针一次含退役 Facets
  const isTrulyEmptyScope =
    !showRetired &&
    !hasActiveFilters &&
    !isLoading &&
    filteredTotal === 0 &&
    (facetsQ.data ? facetsQ.data.all === 0 : true);
  const retiredPeekQ = useQuery({
    queryKey: hostKeys.retiredPeek(),
    queryFn: () => api.hosts.facets(true),
    enabled: isTrulyEmptyScope,
    refetchInterval: 10000,
  });
  const hasAnyHost =
    retiredPeekQ.data === undefined
      ? null
      : retiredPeekQ.data.all > 0;

  const loadHostScriptPresence = (hostId: string | number) =>
    api.scriptPresence.host(hostId);
  const refreshHostScriptPresence = async (hostId: string | number) => {
    const result = await api.scriptPresence.refresh(hostId);
    void queryClient.invalidateQueries({ queryKey: scriptPresenceKeys.summary() });
    return result;
  };

  const liveHostIds = useMemo(
    () => new Set(hosts.map((host) => String(host.id))),
    [hosts],
  );
  const selectionScope: 'page' | 'all_filtered' = crossPagePreview ? 'all_filtered' : 'page';
  const visibleSelectedHostIds = useMemo(() => {
    if (crossPagePreview) {
      return new Set<string | number>(
        crossPagePreview.host_ids.map((id) => resolveSelectedHostId(id, hosts)),
      );
    }
    if (selectedHostIds.size === 0) return selectedHostIds;
    const next = new Set(
      Array.from(selectedHostIds).filter((id) => liveHostIds.has(String(id))),
    );
    return next.size === selectedHostIds.size ? selectedHostIds : next;
  }, [crossPagePreview, selectedHostIds, liveHostIds, hosts]);

  const handlePageSelectionChange = useCallback((nextIds: Set<string | number>) => {
    setCrossPagePreview(null);
    setSelectedHostIds(nextIds);
  }, []);

  const handleSelectAllFiltered = useCallback(async () => {
    setCrossPagePreviewLoading(true);
    try {
      const preview = await api.hosts.selectionPreview(hostFilterParams);
      setCrossPagePreview(preview);
      setSelectedHostIds(
        new Set(preview.host_ids.map((id) => resolveSelectedHostId(id, hosts))),
      );
      toast.info(`已冻结全部筛选结果（共 ${preview.total_matched} 台主机）`);
    } catch (err: unknown) {
      toast.error(`冻结筛选结果失败: ${toApiError(err).message}`);
    } finally {
      setCrossPagePreviewLoading(false);
    }
  }, [hostFilterParams, hosts, toast]);

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

  // ADR-0038 D2：退役 / 解除退役（admin；通过标准 RetireHostDialog 收集必填原因，写入审计）
  const [retireDialog, setRetireDialog] = useState<{
    host: HostTableData;
    mode: HostRetireMode;
  } | null>(null);

  const retireMutation = useMutation({
    mutationFn: ({ hostId, reason }: { hostId: string | number; reason: string }) =>
      api.hosts.retire(hostId, reason),
    onSuccess: (host) => {
      void queryClient.invalidateQueries({ queryKey: hostKeys.list() });
      void queryClient.invalidateQueries({ queryKey: hostKeys.retiredList() });
      setRetireDialog(null);
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
      setRetireDialog(null);
      toast.success(`主机 ${host.id} 已解除退役`);
    },
    onError: (error: unknown) => {
      toast.error(`解除退役失败: ${toApiError(error).message}`);
    },
  });

  const handleRetire = (host: HostTableData) => {
    setRetireDialog({ host, mode: 'retire' });
  };

  const handleUnretire = (host: HostTableData) => {
    setRetireDialog({ host, mode: 'unretire' });
  };

  const handleRetireSubmit = (mode: HostRetireMode, reason: string) => {
    if (!retireDialog) return;
    if (mode === 'retire') {
      retireMutation.mutate({ hostId: retireDialog.host.id, reason });
    } else {
      unretireMutation.mutate({ hostId: retireDialog.host.id, reason });
    }
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
    startFlashPrereqsBatch,
    cancelInstall,
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
      if (ev.kind === 'flash_prereqs') {
        if (ev.ok) {
          toast.success(`主机 ${ev.label} 刷机前置已补齐`);
          queryClient.invalidateQueries({ queryKey: hostKeys.list() });
        } else if (ev.status === 'CANCELED') {
          // #3193：与 install 批次（#2255）同口径——取消 / 运行记录丢失 ≠ 失败，不得报红
          toast.info(
            ev.error
              ? `主机 ${ev.label} 的刷机前置已中止：${ev.error}`
              : `主机 ${ev.label} 的刷机前置已取消（取消不是失败，可随时复跑）`,
          );
        } else {
          toast.error(`主机 ${ev.label} 刷机前置失败：${ev.error || ev.status}`);
        }
        return;
      }
      if (ev.ok) {
        toast.success(`主机 ${ev.label} Agent 安装完成`);
        queryClient.invalidateQueries({ queryKey: hostKeys.list() });
      } else if (ev.status === 'CANCELED') {
        // #2255：取消 ≠ 失败（与 S5 的 agent_install_canceled 同口径）——别报红
        toast.info(
          ev.error
            ? `主机 ${ev.label} 的安装已中止：${ev.error}`
            : `主机 ${ev.label} 的安装已取消（取消不是失败，可随时复跑）`,
        );
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

  const [deviceIntentUpdatingHostId, setDeviceIntentUpdatingHostId] = useState<
    string | number | null
  >(null);

  const setDeviceIntentMutation = useMutation({
    mutationFn: (vars: { hostId: string | number; reason: string }) =>
      api.hosts.setDeviceIntent(vars.hostId, vars.reason),
    onSuccess: (_data, vars) => {
      toast.success(`主机 ${vars.hostId} 已标记为「设备面已处置」`);
      setDeviceIntentUpdatingHostId(null);
      queryClient.invalidateQueries({ queryKey: hostKeys.list() });
      queryClient.invalidateQueries({ queryKey: ['host-detail', vars.hostId] });
    },
    onError: (error: unknown, vars) => {
      toast.error(
        `标记设备面已处置失败: ${toApiError(error).message || `host ${vars.hostId}`}`,
      );
      setDeviceIntentUpdatingHostId(null);
    },
  });

  const clearDeviceIntentMutation = useMutation({
    mutationFn: (hostId: string | number) => api.hosts.clearDeviceIntent(hostId),
    onSuccess: (_data, hostId) => {
      toast.success(`主机 ${hostId} 已解除「设备面已处置」标记`);
      setDeviceIntentUpdatingHostId(null);
      queryClient.invalidateQueries({ queryKey: hostKeys.list() });
      queryClient.invalidateQueries({ queryKey: ['host-detail', hostId] });
    },
    onError: (error: unknown, hostId) => {
      toast.error(
        `解除设备面已处置失败: ${toApiError(error).message || `host ${hostId}`}`,
      );
      setDeviceIntentUpdatingHostId(null);
    },
  });

  const handleSetDeviceIntent = async (host: HostTableData) => {
    const ok = await confirmDialog({
      description: `确认将主机「${host.name ?? host.id}」标记为「设备面已处置（空置/撤机）」？标记后该主机的 UsbBlind 与批量 offline 告警将被豁免，直到人工解除。`,
    });
    if (!ok) return;
    setDeviceIntentUpdatingHostId(host.id);
    setDeviceIntentMutation.mutate({
      hostId: host.id,
      reason: '人工确认空置或设备已清空（Hosts 工作台标记）',
    });
  };

  const handleClearDeviceIntent = async (host: HostTableData) => {
    const ok = await confirmDialog({
      description: `确认解除主机「${host.name ?? host.id}」的「设备面已处置」标记？解除后设备面告警将立即恢复生效。`,
    });
    if (!ok) return;
    setDeviceIntentUpdatingHostId(host.id);
    clearDeviceIntentMutation.mutate(host.id);
  };

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
    } else if (status === 'CANCELED') {
      // #2255：显式取消 ≠ 失败（与 S5 的 agent_install_canceled 同口径）
      markTerminal(hostId, 'canceled');
    } else if (status === 'FAILED') {
      markTerminal(hostId, 'failed', status);
    }
  };

  const resolveInstallTargetsFromRecords = (records: Host[]) => {
    return records
      .map((full) => {
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
    const records = await resolveHostRecordsForIds([hostId], hosts);
    const targets = resolveInstallTargetsFromRecords(records);
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
    const records = await resolveHostRecordsForIds(selectedIds, hosts);
    const retiredCount = records.filter((h) => Boolean(h.retired_at)).length;
    if (retiredCount > 0) {
      toast.error(`已跳过 ${retiredCount} 台已退役主机（不接受安装/热更新）`);
    }
    const targets = resolveInstallTargetsFromRecords(records);
    if (!targets.length) {
      toast.info('选中主机中没有可安装目标（ONLINE 请用热更新）');
      return;
    }
    const first = targets.filter((t) => !t.agentInstalled).length;
    const re = targets.filter((t) => t.agentInstalled).length;
    const scopePrefix = selectionScope === 'all_filtered' ? '（跨页已冻结集合）' : '';
    const ok = await confirmDialog({
      description: `将对 ${targets.length} 台主机安装 Agent${scopePrefix}（首次 ${first} / 重装 ${re}，并发 2）。是否继续？`,
      variant: 'destructive',
    });
    if (!ok) return;
    await startInstallBatch(targets);
    clearSelection();
  };

  const handleBulkDelete = async () => {
    if (visibleSelectedHostIds.size === 0) return;
    const scopeText =
      selectionScope === 'all_filtered'
        ? `跨页已冻结的 ${visibleSelectedHostIds.size} 台主机`
        : `选中的 ${visibleSelectedHostIds.size} 台主机`;
    const ok = await confirmDialog({
      description: `确定删除${scopeText}？此操作不可恢复。`,
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
    clearSelection();
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
  const tableData: HostTableData[] = useMemo(
    () => hosts.map(mapHostToTableData),
    [hosts],
  );

  const bulkCounts = useMemo(() => {
    if (crossPagePreview) {
      return {
        selected: crossPagePreview.total_matched,
        firstInstall: crossPagePreview.first_install_count,
        reinstall: crossPagePreview.reinstall_count,
        hotUpdate: crossPagePreview.hot_update_count,
        flashPrereqs: crossPagePreview.flash_prereqs_count,
      };
    }
    const selected = Array.from(visibleSelectedHostIds)
      .map((id) => hosts.find((h: Host) => String(h.id) === String(id)))
      .filter((h): h is Host => Boolean(h));
    let firstInstall = 0;
    let reinstall = 0;
    let hotUpdate = 0;
    let flashPrereqs = 0;
    for (const h of selected) {
      if (h.status === 'ONLINE') {
        hotUpdate += 1;
      } else if (h.agent_installed) {
        reinstall += 1;
      } else {
        firstInstall += 1;
      }
      if (h.agent_installed) {
        flashPrereqs += 1;
      }
    }
    return {
      selected: visibleSelectedHostIds.size,
      firstInstall,
      reinstall,
      hotUpdate,
      flashPrereqs,
    };
  }, [crossPagePreview, visibleSelectedHostIds, hosts]);

  const installPending = hostOps.some(
    (op) =>
      (op.kind === 'install' || op.kind === 'reinstall') &&
      (op.status === 'pending' || op.status === 'running'),
  );
  const hotUpdateOpPending = hostOps.some(
    (op) => op.kind === 'hot_update' && (op.status === 'pending' || op.status === 'running'),
  );
  const flashPrereqsPending = hostOps.some(
    (op) =>
      op.kind === 'flash_prereqs' &&
      (op.status === 'pending' || op.status === 'running'),
  );
  const hotUpdatePanelOps = hostOps.some((op) => op.kind === 'hot_update');

  const resolveFlashPrereqsTargetsFromRecords = (records: Host[]) => {
    return records
      .map((full) => {
        if (full.retired_at || !full.agent_installed) return null;
        return {
          hostId: full.id,
          label: full.name ?? full.ip ?? String(full.id),
          agentInstalled: true,
        };
      })
      .filter((t): t is NonNullable<typeof t> => t != null);
  };

  const handleFlashPrereqs = async (hostId: number | string) => {
    const records = await resolveHostRecordsForIds([hostId], hosts);
    const targets = resolveFlashPrereqsTargetsFromRecords(records);
    if (!targets.length) {
      toast.info('仅已安装 Agent 的主机可补齐刷机前置');
      return;
    }
    const ok = await confirmDialog({
      description: `确定对主机「${targets[0].label}」补齐刷机前置（dialout / udev / Qt 库）？不会热更新代码。`,
    });
    if (!ok) return;
    await startFlashPrereqsBatch(targets);
  };

  const handleBulkFlashPrereqs = async () => {
    const records = await resolveHostRecordsForIds(Array.from(visibleSelectedHostIds), hosts);
    const targets = resolveFlashPrereqsTargetsFromRecords(records);
    if (!targets.length) {
      toast.info('选中主机中没有可补齐刷机前置的目标（需已安装 Agent）');
      return;
    }
    const scopePrefix = selectionScope === 'all_filtered' ? '（跨页已冻结集合）' : '';
    const ok = await confirmDialog({
      description: `确定对 ${targets.length} 台主机补齐刷机前置${scopePrefix}（dialout / udev / Qt 库）？不会热更新代码。`,
    });
    if (!ok) return;
    await startFlashPrereqsBatch(targets);
    clearSelection();
  };

  const handleSelectedHotUpdate = async () => {
    if (visibleSelectedHostIds.size === 0 || bulkHotUpdateProgress) return;
    if (selectionScope === 'page' && visibleSelectedHostIds.size === 1) {
      const [hostId] = Array.from(visibleSelectedHostIds);
      const host = hosts.find((item: Host) => String(item.id) === String(hostId));
      if (!host || host.status !== 'ONLINE') {
        toast.info('请选择一台在线主机进行热更新');
        return;
      }
      handleHotUpdate(hostId);
      return;
    }

    const targets = Array.from(visibleSelectedHostIds).map((id) => {
      const host = hosts.find((item: Host) => String(item.id) === String(id));
      return {
        id: host ? host.id : id,
        label: host?.name ?? host?.ip ?? String(id),
      };
    });
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
          setCrossPagePreview(null);
          setSelectedHostIds(
            new Set(
              skippedOnly.skipped.map((item) => resolveSelectedHostId(item.hostId, hosts)),
            ),
          );
        }
        return;
      }

      const scopePrefix = selectionScope === 'all_filtered' ? '（跨页已冻结集合）' : '';
      const ok = await confirmDialog({
        title: '确认安全批量热更新',
        description:
          `预检完成${scopePrefix}：可热更新 ${precheck.eligible.length} 台；` +
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
        ...result.skipped.map((item) => resolveSelectedHostId(item.hostId, hosts)),
        ...result.failed.map((item) => resolveSelectedHostId(item.hostId, hosts)),
      ]);
      setCrossPagePreview(null);
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
      <RetireHostDialog
        isOpen={retireDialog != null}
        mode={retireDialog?.mode ?? 'retire'}
        hostLabel={retireDialog ? (retireDialog.host.name || String(retireDialog.host.id)) : ''}
        isSubmitting={retireMutation.isPending || unretireMutation.isPending}
        onClose={() => {
          if (!retireMutation.isPending && !unretireMutation.isPending) {
            setRetireDialog(null);
          }
        }}
        onSubmit={handleRetireSubmit}
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

  const pageHeaderActions = (
    <div className="flex items-center gap-2">
      {!opPanelOpen && hostOps.length > 0 && (
        <Button
          variant="outline"
          size="sm"
          data-testid="host-op-panel-reopen"
          onClick={() => setOpPanelOpen(true)}
        >
          {hostOps.some((o) => o.kind === 'flash_prereqs')
            ? '刷机前置进度'
            : hotUpdatePanelOps
              ? '热更新进度'
              : '安装进度'}
          {installPending || hotUpdateOpPending || flashPrereqsPending
            ? ` (${hostOps.filter((o) => o.status === 'pending' || o.status === 'running').length} 进行中)`
            : ` (${hostOps.filter((o) => o.status === 'success').length} 成功 / ${hostOps.filter((o) => o.status === 'failed').length} 失败${hostOps.some((o) => o.status === 'canceled') ? ` / ${hostOps.filter((o) => o.status === 'canceled').length} 已取消` : ''}${hostOps.some((o) => o.status === 'skipped') ? ` / ${hostOps.filter((o) => o.status === 'skipped').length} 跳过` : ''})`}
        </Button>
      )}
      {isAdmin && (
        <Button size="sm" data-testid="host-add-button" onClick={() => setIsModalOpen(true)}>
          <Plus className="h-3.5 w-3.5" />
          添加主机
        </Button>
      )}
    </div>
  );

  if (isLoading) {
    return (
      <PageContainer width="wide" className="py-2.5 lg:py-2.5">
        <PageHeader title="主机集群" subtitle="管理和监控测试执行节点" action={pageHeaderActions} />
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
      <PageContainer width="wide" className="py-2.5 lg:py-2.5">
        <PageHeader title="主机集群" subtitle="管理和监控测试执行节点" action={pageHeaderActions} />
        <ErrorState
          title={unreachable ? '无法连接后端服务' : `加载主机失败（${apiError.status}）`}
          description={unreachable ? '请检查后端服务连接与网络。' : apiError.message}
          onRetry={() => queryClient.invalidateQueries({ queryKey: hostKeys.list() })}
        />
      </PageContainer>
    );
  }

  // #2362：空态文案按**空集的性质**分三种，而不是一律归因退役——「都已退役」在
  // 全新环境（从未接入）会把排障方向引到退役记录上。`hasAnyHost === null`
  // （探针未回/失败）保持既有文案：不确定时不改口径。
  const emptyTitle = showRetired
    ? '还没有主机'
    : hasAnyHost === false
      ? '暂无主机'
      : '没有在用主机';
  const emptyDescription = showRetired
    ? '当前没有任何主机记录。'
    : hasAnyHost === false
      ? '尚未接入任何主机。等待 Agent 接入后，主机会出现在这里。'
      : '所有主机都已退役。勾选「显示已退役」可查看并解除退役。';

  // 契约 §6.1：仅当全集确实为 0 且无活动筛选时切全页空态；有筛选无结果时必须保留筛选栏
  const showFullPageEmpty =
    !hasActiveFilters &&
    tableData.length === 0 &&
    filteredTotal === 0 &&
    (facetsQ.data ? facetsQ.data.all === 0 : true);

  if (showFullPageEmpty) {
    return (
      <PageContainer width="wide" className="py-2.5 lg:py-2.5">
        <PageHeader title="主机集群" subtitle="管理和监控测试执行节点" action={pageHeaderActions} />
        {/* #2051：空态也要给「显示已退役」开关，否则全退役后无法解除退役 */}
        <div className="flex items-center justify-end gap-2 py-2">{retiredToggle}</div>
        <EmptyState
          title={emptyTitle}
          description={emptyDescription}
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
    <PageContainer width="wide" scrollable={false} className="py-2.5 lg:py-2.5">
      <PageHeader
        title="主机集群"
        subtitle="管理和监控测试执行节点 · 点击主机行查看详情，勾选后可批量运维"
        action={pageHeaderActions}
      />

      {/* Host Table */}
      <ExpandableHostTable
        hosts={tableData}
        serverMode
        serverFacets={facetsQ.data}
        serverFilteredTotal={filteredTotal}
        page={page}
        pageSize={pageSize}
        pageSizeOptions={PAGE_SIZE_OPTIONS}
        onPageChange={(nextPage) => {
          if (selectionScope === 'page') setSelectedHostIds(new Set());
          updateParams({ page: nextPage > 1 ? String(nextPage) : undefined });
        }}
        onPageSizeChange={(nextSize) => {
          if (selectionScope === 'page') setSelectedHostIds(new Set());
          updateParams({
            limit: nextSize === 50 ? undefined : String(nextSize),
            page: undefined,
          });
        }}
        sortKey={sortKey}
        sortDirection={sortDirection}
        onSortChange={(nextKey, nextDir) => {
          if (selectionScope === 'page') setSelectedHostIds(new Set());
          updateParams({
            sort_by: nextKey === 'default' ? undefined : nextKey,
            sort_dir: nextKey === 'default' ? undefined : nextDir,
            page: undefined,
          });
        }}
        drawerHostOverride={drawerHostOverride}
        isFetchingPage={isFetchingPage}
        statusFilter={statusFilter}
        onStatusFilterChange={(val) => updateFilterParams({ status: val === 'all' ? undefined : val })}
        searchQuery={searchQuery}
        onSearchQueryChange={(val) => updateFilterParams({ q: val || undefined })}
        healthFilter={healthFilter}
        onHealthFilterChange={(val) => updateFilterParams({ health: val === 'all' ? undefined : val })}
        syncFilter={syncFilter}
        onSyncFilterChange={(val) => updateFilterParams({ sync: val === 'all' ? undefined : val })}
        activeDrawerHostId={drawerHostId}
        onActiveDrawerHostIdChange={(val) => updateParams({ drawer: val || undefined })}
        showRetired={showRetired}
        onShowRetiredChange={setShowRetired}
        onClearQuickView={() =>
          updateFilterParams({ status: undefined, health: undefined, sync: undefined })
        }
        onResetAllFilters={() => {
          clearSelection();
          clearParams(['status', 'q', 'health', 'sync', 'retired', 'page', 'sort_by', 'sort_dir']);
        }}
        onNavigateToDevices={(hostId) => navigate(`/devices?host=${hostId}`)}
        onHotUpdate={isAdmin ? handleHotUpdate : undefined}
        isHotUpdating={(hostId: string | number) =>
          isHostOpBusy(hostId, 'hot_update') ||
          (bulkHotUpdateProgress != null && visibleSelectedHostIds.has(hostId))
        }
        onInstall={isAdmin ? handleInstall : undefined}
        isInstalling={(hostId: string | number) =>
          isHostOpBusy(hostId, ['install', 'reinstall'])
        }
        onFlashPrereqs={isAdmin ? handleFlashPrereqs : undefined}
        isFlashPrereqs={(hostId: string | number) =>
          isHostOpBusy(hostId, 'flash_prereqs')
        }
        onEdit={isAdmin ? handleEdit : undefined}
        onDelete={isAdmin ? handleDelete : undefined}
        onRetire={isAdmin ? handleRetire : undefined}
        onUnretire={isAdmin ? handleUnretire : undefined}
        onSetDeviceIntent={isAdmin ? handleSetDeviceIntent : undefined}
        onClearDeviceIntent={isAdmin ? handleClearDeviceIntent : undefined}
        isDeviceIntentUpdating={(hostId: string | number) =>
          (setDeviceIntentMutation.isPending || clearDeviceIntentMutation.isPending) &&
          deviceIntentUpdatingHostId === hostId
        }
        isDeleting={(hostId: string | number) => deleteMutation.isPending && deleteMutation.variables === hostId}
        onWatcherAdminStateChange={handleWatcherAdminStateChange}
        isWatcherAdminStateUpdating={(hostId: string | number) =>
          watcherAdminStateMutation.isPending && watcherAdminUpdatingHostId === hostId
        }
        canManageWatcherAdminState={canManageWatcherAdminState}
        isAdmin={isAdmin}
        selectedIds={visibleSelectedHostIds}
        onSelectionChange={handlePageSelectionChange}
        onLoadHostScriptPresence={loadHostScriptPresence}
        onRefreshHostScriptPresence={refreshHostScriptPresence}
      />

      {isAdmin && (
        <HostBulkActionBar
          counts={bulkCounts}
          isAdmin={isAdmin}
          selectionScope={selectionScope}
          filteredTotal={filteredTotal}
          crossPagePreviewLoading={crossPagePreviewLoading}
          onSelectAllFiltered={handleSelectAllFiltered}
          installPending={installPending || hotUpdateOpPending || flashPrereqsPending}
          hotUpdatePending={
            bulkHotUpdateProgress != null ||
            hotUpdateOpPending ||
            installPending ||
            flashPrereqsPending
          }
          flashPrereqsPending={flashPrereqsPending || installPending || hotUpdateOpPending}
          hotUpdateProgressLabel={bulkHotUpdateProgress
            ? `${bulkHotUpdateProgress.phase === 'checking' ? '预检' : '热更新'} ${bulkHotUpdateProgress.completed}/${bulkHotUpdateProgress.total}`
            : hotUpdateOpPending
              ? `热更新 ${hostOps.filter((o) => o.kind === 'hot_update' && o.status === 'success').length}/${hostOps.filter((o) => o.kind === 'hot_update' && o.status !== 'skipped').length}`
              : undefined}
          onInstall={handleBulkInstall}
          onHotUpdate={handleSelectedHotUpdate}
          onFlashPrereqs={handleBulkFlashPrereqs}
          onDelete={handleBulkDelete}
          onClear={clearSelection}
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
        onCancelInstall={cancelInstall}
      />
    </PageContainer>
  );
}
