import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus, Smartphone } from 'lucide-react';
import { useToast } from '@/hooks/useToast';
import { useAuthSession } from '@/hooks/useAuthSession';
import { useFleetDeviceUpdates } from '@/hooks/useFleetDeviceUpdates';
import { useUrlFilterParams } from '@/hooks/useUrlFilterState';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import {
  ExpandableDeviceTable,
  type DeviceStatus,
  type DeviceTableData,
} from '@/components/device/ExpandableDeviceTable';
import DeviceBulkActionBar from '@/components/device/DeviceBulkActionBar';
import { AddDeviceModal } from './components/AddDeviceModal';
import { BatchEditDeviceTagsDialog, type DeviceTagOperation } from './components/BatchEditDeviceTagsDialog';
import { RetireDevicesDialog, type DeviceRetireMode } from './components/RetireDevicesDialog';
import { AssignProjectDialog } from './components/AssignProjectDialog';
import { ProjectFilterSelect, UNASSIGNED_FILTER_VALUE } from '@/components/project/ProjectFilterSelect';
import { api, assignDevicesToProject, toApiError } from '@/utils/api';
import type {
  Device,
  DeviceFilterQueryParams,
  DeviceListItem,
  DeviceListSortBy,
  DeviceListSortDir,
  DeviceSelectionPreview,
} from '@/utils/api/types';
import { deviceKeys } from '@/utils/api/queryKeys';
import { Button } from '@/components/ui/button';
import { PageContainer, PageHeader } from '@/components/layout';
import { ErrorState } from '@/components/ui/error-state';
import { EmptyState } from '@/components/ui/empty-state';
import { TEXT } from '@/design-system';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import { cn } from '@/lib/utils';
import { escapeSpreadsheetClipboardCell, neutralizeSpreadsheetCellText } from '@/utils/spreadsheet';

const PAGE_SIZE_OPTIONS = [50, 100, 200] as const;

const deviceStatusMap: Record<string, DeviceStatus> = {
  ONLINE: 'idle',
  BUSY: 'testing',
  OFFLINE: 'offline',
  ERROR: 'error',
};

/** 契约 §3.2 规则 3：将 URL 中的前端状态值归一为后端 API 查询值。 */
function mapUrlStatusToApiStatus(statusFilter: string): string | undefined {
  switch (statusFilter) {
    case 'idle':
      return 'ONLINE';
    case 'testing':
      return 'BUSY';
    case 'offline':
      return 'OFFLINE';
    case 'error':
      return 'ERROR';
    case 'attention':
      return 'need_attention';
    default:
      return undefined;
  }
}

function toDeviceTableData(device: DeviceListItem | Device): DeviceTableData {
  const hostKey =
    device.host_id != null && String(device.host_id).trim() !== ''
      ? String(device.host_id)
      : null;
  const hostName = 'host_name' in device ? (device.host_name ?? null) : null;
  return {
    id: device.id,
    serial: device.serial,
    model: device.model || '未知设备',
    product: device.product ?? null,
    status: deviceStatusMap[device.status] || 'offline',
    adb_state: device.adb_state,
    battery_level: device.battery_level ?? undefined,
    temperature: device.temperature ?? device.battery_temp ?? undefined,
    network_latency: device.network_latency ?? null,
    build_display_id: device.build_display_id ?? null,
    disk_total: device.disk_total ?? null,
    disk_used: device.disk_used ?? null,
    host_id: hostKey ?? undefined,
    host_name: hostName,
    last_seen: device.last_seen ?? undefined,
    tags: Array.isArray(device.tags) ? device.tags : [],
    project_key: device.project_key ?? null,
    attribution_source: device.attribution_source ?? null,
    retired_at: device.retired_at ?? null,
    is_stale: device.is_stale ?? false,
    retire_suggested: device.retire_suggested ?? false,
    return_state: device.return_state ?? null,
    usb_presence: device.usb_presence ?? null,
  };
}

/** 契约 §3.4 规则 3：用户主动触发跨页复制/导出时按页拉满当前筛选全集。 */
async function fetchAllMatchingDevicesForReadOnly(
  filters: DeviceFilterQueryParams,
  frozenIds?: number[] | null,
): Promise<DeviceTableData[]> {
  const pageLimit = 200;
  const first = await api.devices.listPage({
    ...filters,
    skip: 0,
    limit: pageLimit,
    projection: 'list',
  });
  const allItems: DeviceListItem[] = [...first.items];
  for (let skip = first.items.length; skip < first.total && first.items.length > 0; skip += pageLimit) {
    const next = await api.devices.listPage({
      ...filters,
      skip,
      limit: pageLimit,
      projection: 'list',
    });
    if (next.items.length === 0) break;
    allItems.push(...next.items);
  }
  const rows = allItems.map(toDeviceTableData);
  if (frozenIds && frozenIds.length > 0) {
    const allowed = new Set(frozenIds);
    return rows.filter((row) => allowed.has(row.id));
  }
  return rows;
}

export default function DevicesPage() {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedDeviceIds, setSelectedDeviceIds] = useState<Set<number>>(new Set());
  const [crossPagePreview, setCrossPagePreview] = useState<DeviceSelectionPreview | null>(null);
  const [crossPageLoading, setCrossPageLoading] = useState(false);
  const [readOnlyExportPending, setReadOnlyExportPending] = useState(false);
  const [isTagDialogOpen, setIsTagDialogOpen] = useState(false);
  const [singleTagDevice, setSingleTagDevice] = useState<DeviceTableData | null>(null);
  const [isAssignDialogOpen, setIsAssignDialogOpen] = useState(false);
  const [sortState, setSortState] = useState<{ by?: DeviceListSortBy; dir?: DeviceListSortDir }>({});
  const navigate = useNavigate();
  const { getString, getBool, getPositiveInt, updateParams, clearParams } = useUrlFilterParams();

  // ADR-0029 + URL 持久化：筛选条件同步到 URL SearchParams，刷新/返回不丢失
  const projectKey = getString('project') || undefined;
  const setProjectKey = (next: string | undefined) =>
    updateParams({ project: next }, { resetPage: true });
  const includeStale = getBool('stale');
  const setIncludeStale = (next: boolean) =>
    updateParams({ stale: next }, { resetPage: true });
  const includeRetired = getBool('retired');
  const setIncludeRetired = (next: boolean) =>
    updateParams({ retired: next }, { resetPage: true });
  const statusFilter = getString('status', 'all');
  const hostFilter = getString('host', 'all');
  const modelFilter = getString('model', 'all');
  const productFilter = getString('product', 'all');
  const versionFilter = getString('version', 'all');
  const searchQuery = getString('q', '');
  const debouncedSearch = useDebouncedValue(searchQuery, 300);
  const taggedOnly = getBool('tagged');
  const currentPage = getPositiveInt('page', 1);
  const rawPageSize = getPositiveInt('pageSize', 50);
  const pageSize: number = (PAGE_SIZE_OPTIONS as readonly number[]).includes(rawPageSize) ? rawPageSize : 50;
  const rawDrawerParam = getString('drawer');
  const drawerDeviceId = rawDrawerParam ? getPositiveInt('drawer', 0) || null : null;
  const [retireDialog, setRetireDialog] = useState<{ open: boolean; mode: DeviceRetireMode }>(
    { open: false, mode: 'retire' },
  );
  const queryClient = useQueryClient();
  const toast = useToast();
  const sessionQ = useAuthSession();
  const isAdmin = sessionQ.data?.role === 'admin';
  const unassignedOnly = projectKey === UNASSIGNED_FILTER_VALUE;
  const effectiveProjectKey = unassignedOnly ? undefined : projectKey;

  // #2369：仅设备页订阅 fleet:devices，material DEVICE_UPDATE 合流失效列表。
  useFleetDeviceUpdates(true);

  const apiStatus = mapUrlStatusToApiStatus(statusFilter);

  // 全量筛选参数（用于单页列表、跨页预览与导出）
  const activeFilterParams = useMemo<DeviceFilterQueryParams>(
    () => ({
      ...(debouncedSearch.trim() ? { q: debouncedSearch.trim() } : {}),
      ...(apiStatus ? { status: apiStatus } : {}),
      ...(hostFilter !== 'all' ? { host_id: hostFilter } : {}),
      ...(modelFilter !== 'all' ? { model: modelFilter } : {}),
      ...(productFilter !== 'all' ? { product: productFilter } : {}),
      ...(versionFilter !== 'all' ? { version: versionFilter } : {}),
      ...(taggedOnly ? { has_tags: true } : {}),
      ...(effectiveProjectKey ? { project_key: effectiveProjectKey } : {}),
      ...(unassignedOnly ? { unassigned: true } : {}),
      ...(includeStale ? { include_stale: true } : {}),
      ...(includeRetired ? { include_retired: true } : {}),
    }),
    [
      debouncedSearch,
      apiStatus,
      hostFilter,
      modelFilter,
      productFilter,
      versionFilter,
      taggedOnly,
      effectiveProjectKey,
      unassignedOnly,
      includeStale,
      includeRetired,
    ],
  );

  // 契约 §3.3 规则 6 / §6.1 规则 2：Facets 键只含三级作用域维度，不含页码、排序、q 与同级明细下拉
  const facetQueryParams = useMemo<DeviceFilterQueryParams>(
    () => ({
      ...(effectiveProjectKey ? { project_key: effectiveProjectKey } : {}),
      ...(unassignedOnly ? { unassigned: true } : {}),
      ...(includeStale ? { include_stale: true } : {}),
      ...(includeRetired ? { include_retired: true } : {}),
      ...(apiStatus ? { status: apiStatus } : {}),
      ...(taggedOnly ? { has_tags: true } : {}),
      ...(hostFilter !== 'all' ? { host_id: hostFilter } : {}),
    }),
    [
      effectiveProjectKey,
      unassignedOnly,
      includeStale,
      includeRetired,
      apiStatus,
      taggedOnly,
      hostFilter,
    ],
  );

  const skip = (currentPage - 1) * pageSize;

  // #496 B3-1：默认请求单页（limit=50，projection=list），不再每 10 秒全量拉满 fleet
  const pageQueryParams = useMemo(
    () => ({
      ...activeFilterParams,
      skip,
      limit: pageSize,
      ...(sortState.by ? { sort_by: sortState.by } : {}),
      ...(sortState.dir ? { sort_dir: sortState.dir } : {}),
      projection: 'list' as const,
    }),
    [activeFilterParams, skip, pageSize, sortState.by, sortState.dir],
  );

  const pageQ = useQuery({
    queryKey: deviceKeys.page(pageQueryParams as Record<string, unknown>),
    queryFn: () => api.devices.listPage(pageQueryParams),
    placeholderData: keepPreviousData,
    refetchInterval: 10000,
  });

  // 独立只读三级 Facets（翻页、排序、输入搜索词均不触发重复请求）
  const facetsQ = useQuery({
    queryKey: deviceKeys.facets(facetQueryParams as Record<string, unknown>),
    queryFn: () => api.devices.facets(facetQueryParams),
    placeholderData: keepPreviousData,
    refetchInterval: 30000,
  });

  const formattedDevices: DeviceTableData[] = useMemo(
    () => (pageQ.data?.items ?? []).map(toDeviceTableData),
    [pageQ.data?.items],
  );

  // 契约 §6.1 规则 1：?drawer= 深链目标不在当前页时，经 GET /devices/{id} 独立加载
  const drawerInCurrentPage = useMemo(
    () => drawerDeviceId != null && formattedDevices.some((d) => d.id === drawerDeviceId),
    [drawerDeviceId, formattedDevices],
  );
  const drawerDetailQ = useQuery({
    queryKey: deviceKeys.detail(drawerDeviceId ?? 0),
    queryFn: () => api.devices.get(drawerDeviceId!),
    enabled: drawerDeviceId != null && !drawerInCurrentPage,
  });
  const drawerDeviceOverride = useMemo<DeviceTableData | null>(() => {
    if (drawerInCurrentPage || !drawerDetailQ.data) return null;
    return toDeviceTableData(drawerDetailQ.data);
  }, [drawerInCurrentPage, drawerDetailQ.data]);

  // 契约 §3.3 规则 7：只有匹配当前筛选且成功返回（非 placeholder/加载/失败）的 Facets 才允许归一失效选项
  const facetsData = facetsQ.data;
  const facetsSettled = facetsQ.isSuccess && !facetsQ.isFetching && !facetsQ.isPlaceholderData && Boolean(facetsData);
  useEffect(() => {
    if (!facetsSettled || !facetsData) return;
    const patch: Record<string, string | undefined> = {};
    if (hostFilter !== 'all' && !facetsData.options.hosts.some((opt) => opt.value === hostFilter)) {
      patch.host = undefined;
    }
    if (modelFilter !== 'all' && !facetsData.options.models.includes(modelFilter)) {
      patch.model = undefined;
    }
    if (productFilter !== 'all' && !facetsData.options.products.includes(productFilter)) {
      patch.product = undefined;
    }
    if (versionFilter !== 'all' && !facetsData.options.versions.includes(versionFilter)) {
      patch.version = undefined;
    }
    if (Object.keys(patch).length > 0) {
      updateParams(patch, { resetPage: true });
    }
  }, [
    facetsSettled,
    facetsData,
    hostFilter,
    modelFilter,
    productFilter,
    versionFilter,
    updateParams,
  ]);

  // 筛选条件或页码变化时清空当前页勾选与跨页冻结集合，防止操作误作用于旧筛选结果
  const filterScopeKey = JSON.stringify([activeFilterParams, currentPage, pageSize, sortState]);
  const [prevFilterScopeKey, setPrevFilterScopeKey] = useState(filterScopeKey);
  if (prevFilterScopeKey !== filterScopeKey) {
    setPrevFilterScopeKey(filterScopeKey);
    if (selectedDeviceIds.size > 0) setSelectedDeviceIds(new Set());
    if (crossPagePreview !== null) setCrossPagePreview(null);
  }

  // 当页设备刷新后，剔除已不在当前页的普通勾选 ID
  const pageIdSignature = formattedDevices.map((d) => d.id).join(',');
  const [prevPageIdSignature, setPrevPageIdSignature] = useState(pageIdSignature);
  if (prevPageIdSignature !== pageIdSignature) {
    setPrevPageIdSignature(pageIdSignature);
    const availableIds = new Set(formattedDevices.map((d) => d.id));
    setSelectedDeviceIds((previous) => {
      if (previous.size === 0) return previous;
      const next = new Set(Array.from(previous).filter((id) => availableIds.has(id)));
      return next.size === previous.size ? previous : next;
    });
  }

  const createMutation = useMutation({
    mutationFn: (data: { serial: string; model?: string; host_id?: string; tags?: string[] }) =>
      api.devices.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: deviceKeys.allLists() });
      setIsModalOpen(false);
      toast.success('设备添加成功');
    },
    onError: (error: unknown) => {
      toast.error(`添加设备失败: ${toApiError(error).message}`);
    },
  });

  const selectedPageDevices = useMemo(
    () => formattedDevices.filter((device) => selectedDeviceIds.has(device.id)),
    [formattedDevices, selectedDeviceIds],
  );

  // 两条显式选择路径：跨页冻结集合优先于当前页普通勾选
  const isCrossPageMode = crossPagePreview !== null;
  const effectiveSelectedCount = isCrossPageMode
    ? crossPagePreview.total_matched
    : selectedPageDevices.length;
  const effectiveSelectedIds = useMemo(
    () => (isCrossPageMode ? crossPagePreview.device_ids : selectedPageDevices.map((d) => d.id)),
    [crossPagePreview, isCrossPageMode, selectedPageDevices],
  );
  const filteredTotal = pageQ.data?.total ?? 0;

  const bulkAssignPreview = useMemo(() => {
    if (isCrossPageMode) {
      return {
        affectedModels: crossPagePreview.affected_models,
        devicesWithoutModelSerials: crossPagePreview.devices_without_model_serials,
      };
    }
    const rawItems = (pageQ.data?.items ?? []).filter((d) => selectedDeviceIds.has(d.id));
    const devicesWithoutModelSerials = rawItems
      .filter((device) => !device.model?.trim())
      .map((device) => device.serial);
    const affectedModels = [
      ...new Set(
        rawItems
          .map((device) => device.model?.trim())
          .filter((model): model is string => Boolean(model)),
      ),
    ].sort();
    return { affectedModels, devicesWithoutModelSerials };
  }, [crossPagePreview, isCrossPageMode, pageQ.data?.items, selectedDeviceIds]);

  const statusSummary = useMemo(() => {
    if (isCrossPageMode) {
      const sc = crossPagePreview.status_counts;
      return [
        sc.ONLINE ? `空闲 ${sc.ONLINE}` : null,
        sc.BUSY ? `测试中 ${sc.BUSY}` : null,
        sc.OFFLINE ? `离线 ${sc.OFFLINE}` : null,
        sc.ERROR ? `错误 ${sc.ERROR}` : null,
      ].filter(Boolean).join(' · ');
    }
    const counts = {
      idle: selectedPageDevices.filter((device) => device.status === 'idle').length,
      testing: selectedPageDevices.filter((device) => device.status === 'testing').length,
      offline: selectedPageDevices.filter((device) => device.status === 'offline').length,
      error: selectedPageDevices.filter((device) => device.status === 'error').length,
    };
    return [
      counts.idle ? `空闲 ${counts.idle}` : null,
      counts.testing ? `测试中 ${counts.testing}` : null,
      counts.offline ? `离线 ${counts.offline}` : null,
      counts.error ? `错误 ${counts.error}` : null,
    ].filter(Boolean).join(' · ');
  }, [crossPagePreview, isCrossPageMode, selectedPageDevices]);

  const handleClearSelection = () => {
    setSelectedDeviceIds(new Set());
    setCrossPagePreview(null);
  };

  // 契约 §3.4 规则 1：跨页全选调用独立 selection-preview 冻结匹配 ID 与影响摘要
  const handleSelectAllFiltered = async () => {
    setCrossPageLoading(true);
    try {
      const preview = await api.devices.selectionPreview({
        action: 'assign_project',
        ...activeFilterParams,
      });
      if (preview.total_matched === 0) {
        toast.error('当前筛选条件下没有匹配的设备');
        return;
      }
      setCrossPagePreview(preview);
      if (preview.exceeds_limit) {
        toast.error(
          `当前筛选共命中 ${preview.total_matched} 台，超出单次写操作上限（${preview.max_allowed ?? 500} 台）；仅支持复制串号与导出 CSV，请收窄筛选后再执行写操作`,
        );
      } else {
        toast.success(`已冻结全部筛选结果 ${preview.total_matched} 台设备`);
      }
    } catch (err: unknown) {
      toast.error(`跨页预览失败: ${toApiError(err).message}`);
    } finally {
      setCrossPageLoading(false);
    }
  };

  const tagUpdateMutation = useMutation({
    mutationFn: async ({
      targetIds,
      pageById,
      operation,
      tags,
    }: {
      targetIds: number[];
      pageById: Map<number, DeviceTableData>;
      operation: DeviceTagOperation;
      tags: string[];
    }) => {
      let cursor = 0;
      let succeeded = 0;
      const failed: string[] = [];
      const workers = Array.from({ length: Math.min(5, targetIds.length) }, async () => {
        while (cursor < targetIds.length) {
          const targetId = targetIds[cursor++];
          const cached = pageById.get(targetId);
          try {
            let currentTags = cached?.tags ?? [];
            if (!cached && operation !== 'replace') {
              const fetched = await api.devices.get(targetId);
              currentTags = Array.isArray(fetched.tags) ? fetched.tags : [];
            }
            const nextTags = operation === 'add'
              ? Array.from(new Set([...currentTags, ...tags]))
              : operation === 'remove'
                ? currentTags.filter((tag) => !tags.includes(tag))
                : tags;
            await api.devices.updateTags(targetId, nextTags);
            succeeded += 1;
          } catch {
            failed.push(cached?.serial ?? `#${targetId}`);
          }
        }
      });
      await Promise.all(workers);
      return { succeeded, failed };
    },
    onSuccess: ({ succeeded, failed }, variables) => {
      queryClient.invalidateQueries({ queryKey: deviceKeys.allLists() });
      const wasSingleEdit =
        singleTagDevice !== null &&
        variables.targetIds.length === 1 &&
        variables.targetIds[0] === singleTagDevice.id;
      setIsTagDialogOpen(false);
      setSingleTagDevice(null);
      if (failed.length === 0) {
        toast.success(`已更新 ${succeeded} 台设备的标签`);
        if (!wasSingleEdit) {
          handleClearSelection();
        }
      } else {
        toast.error(`标签更新完成：成功 ${succeeded} 台，失败 ${failed.length} 台`);
      }
    },
    onError: (error: unknown) => {
      toast.error(`批量更新标签失败: ${toApiError(error).message}`);
    },
  });

  const handleCopySerials = async () => {
    setReadOnlyExportPending(true);
    try {
      const rows = isCrossPageMode
        ? await fetchAllMatchingDevicesForReadOnly(
            activeFilterParams,
            crossPagePreview.exceeds_limit ? null : crossPagePreview.device_ids,
          )
        : selectedPageDevices;
      const text = rows
        .map((device) => escapeSpreadsheetClipboardCell(device.serial))
        .join('\n');
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        textarea.remove();
      }
      toast.success(`已复制 ${rows.length} 个设备序列号`);
    } catch {
      toast.error('复制失败，请检查浏览器剪贴板权限或网络连接');
    } finally {
      setReadOnlyExportPending(false);
    }
  };

  const handleExportSelected = async () => {
    setReadOnlyExportPending(true);
    try {
      const targets = isCrossPageMode
        ? await fetchAllMatchingDevicesForReadOnly(
            activeFilterParams,
            crossPagePreview.exceeds_limit ? null : crossPagePreview.device_ids,
          )
        : selectedPageDevices;
      // #3237：先对原始 string 做公式中和，再做双引号 framing；number 原样保留数值语义。
      const csvCell = (value: unknown) => {
        const raw = typeof value === 'string' ? neutralizeSpreadsheetCellText(value) : (value ?? '');
        return `"${String(raw).replace(/"/g, '""')}"`;
      };
      const rows = targets.map((device) => [
        device.id,
        device.serial,
        device.model,
        device.product ?? '',
        device.status,
        device.host_name ?? '',
        device.build_display_id ?? '',
        (device.tags ?? []).join('|'),
        device.last_seen ?? '',
      ]);
      const csv = [
        ['ID', 'Serial', 'Model', 'Product', 'Status', 'Host', 'Build', 'Tags', 'Last Seen'],
        ...rows,
      ].map((row) => row.map(csvCell).join(',')).join('\r\n');
      const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `devices-${new Date().toISOString().slice(0, 10)}.csv`;
      anchor.click();
      URL.revokeObjectURL(url);
      toast.success(`已导出 ${targets.length} 台设备`);
    } catch (err: unknown) {
      toast.error(`导出失败: ${toApiError(err).message}`);
    } finally {
      setReadOnlyExportPending(false);
    }
  };

  const assignProjectMutation = useMutation({
    mutationFn: ({ targetProjectKey }: { targetProjectKey: string }) =>
      assignDevicesToProject(targetProjectKey, effectiveSelectedIds),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: deviceKeys.allLists() });
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      queryClient.invalidateQueries({ queryKey: ['project-devices'] });
      setIsAssignDialogOpen(false);
      const modelCount = bulkAssignPreview.affectedModels.length;
      const assignedCount = effectiveSelectedIds.length;
      handleClearSelection();
      toast.success(
        modelCount > 0
          ? `已归入 ${modelCount} 个型号（${assignedCount} 台已选）`
          : `已归入 ${assignedCount} 台设备`,
      );
    },
    onError: (error: unknown) => {
      toast.error(`归入项目失败: ${toApiError(error).message}`);
    },
  });

  const swipeTrailMutation = useMutation({
    mutationFn: (enabled: boolean) =>
      api.devices.bulkSwipeTrail(effectiveSelectedIds, enabled),
    onSuccess: (result) => {
      const verb = result.enabled ? '开启' : '关闭';
      const summary = `滑动留痕已${verb}：成功 ${result.ok} · 失败 ${result.failed} · 跳过 ${result.skipped}`;
      if (result.failed > 0 || result.ok === 0) {
        toast.error(summary);
      } else {
        toast.success(summary);
      }
    },
    onError: (error: unknown) => {
      toast.error(`滑动留痕下发失败: ${toApiError(error).message}`);
    },
  });

  const skipOobeMutation = useMutation({
    mutationFn: () => api.devices.bulkSkipOobe(effectiveSelectedIds),
    onSuccess: (result) => {
      const summary = `快捷跳过OOBE（v${result.script_version}）：成功 ${result.ok} · 失败 ${result.failed} · 跳过 ${result.skipped}`;
      if (result.failed > 0 || result.ok === 0) {
        toast.error(summary);
      } else {
        toast.success(summary);
      }
    },
    onError: (error: unknown) => {
      toast.error(`快捷跳过OOBE下发失败: ${toApiError(error).message}`);
    },
  });

  type RetireOutcome =
    | { kind: 'retire'; batch: Awaited<ReturnType<typeof api.devices.retireBatch>> }
    | { kind: 'unretire'; ok: number; failed: string[] };

  const retireMutation = useMutation({
    mutationFn: async ({ mode, reason }: { mode: DeviceRetireMode; reason: string }): Promise<RetireOutcome> => {
      const ids = effectiveSelectedIds;
      if (mode === 'retire') {
        return { kind: 'retire', batch: await api.devices.retireBatch(ids, reason) };
      }
      let ok = 0;
      const failed: string[] = [];
      for (const id of ids) {
        try {
          await api.devices.unretire(id, reason);
          ok += 1;
        } catch {
          failed.push(String(id));
        }
      }
      return { kind: 'unretire', ok, failed };
    },
    onSuccess: (outcome) => {
      queryClient.invalidateQueries({ queryKey: deviceKeys.allLists() });
      setRetireDialog({ open: false, mode: 'retire' });
      handleClearSelection();
      if (outcome.kind === 'retire') {
        const { retired, already_retired, conflict, not_found, failed } = outcome.batch;
        const summary = `退役完成：改态 ${retired} · 幂等跳过 ${already_retired} · 前置冲突 ${conflict} · 未找到 ${not_found} · 失败 ${failed}`;
        if (conflict || not_found || failed) toast.error(summary);
        else toast.success(summary);
      } else if (outcome.failed.length) {
        toast.error(`解除退役：成功 ${outcome.ok} 台，失败 ${outcome.failed.length} 台`);
      } else {
        toast.success(`已解除退役 ${outcome.ok} 台`);
      }
    },
    onError: (error: unknown) => {
      toast.error(`退役操作失败: ${toApiError(error).message}`);
    },
  });

  const selectedRetiredCount = useMemo(() => {
    if (isCrossPageMode) return crossPagePreview.retired_count;
    return selectedPageDevices.filter((device) => Boolean(device.retired_at)).length;
  }, [crossPagePreview, isCrossPageMode, selectedPageDevices]);

  // #3483：幂等跳过的是本次不会改态的台——retire 跳过已退役的，unretire 跳过在役的。
  const idempotentSkipCount = retireDialog.mode === 'retire'
    ? selectedRetiredCount
    : (isCrossPageMode ? crossPagePreview.active_count : selectedPageDevices.length - selectedRetiredCount);

  const hasActivePageFilter =
    Boolean(projectKey) ||
    includeStale ||
    includeRetired ||
    statusFilter !== 'all' ||
    hostFilter !== 'all' ||
    modelFilter !== 'all' ||
    productFilter !== 'all' ||
    versionFilter !== 'all' ||
    Boolean(searchQuery.trim()) ||
    taggedOnly;

  const addDeviceAction = (
    <Button
      size="sm"
      data-testid="device-add-button"
      disabled={!isAdmin}
      title={!isAdmin ? '需要管理员权限' : undefined}
      onClick={() => setIsModalOpen(true)}
      className="gap-1.5"
    >
      <Plus className="h-3.5 w-3.5" />
      添加设备
    </Button>
  );

  if (pageQ.isLoading) {
    return (
      <PageContainer width="wide" className="py-4">
        <PageHeader title="物理设备" subtitle="管理和监控测试设备" action={addDeviceAction} />
        <PageSkeleton>
          <PageSkeleton.Stats count={5} />
          <PageSkeleton.Block size="lg" />
        </PageSkeleton>
      </PageContainer>
    );
  }

  if (pageQ.error) {
    const isProject404 = toApiError(pageQ.error).status === 404 && !unassignedOnly;
    return (
      <PageContainer width="wide" className="py-4">
        <PageHeader title="物理设备" subtitle="管理和监控测试设备" action={addDeviceAction} />
        <ErrorState
          title={isProject404 ? '项目不存在' : '加载设备失败'}
          description={isProject404
            ? `项目 "${effectiveProjectKey}" 不存在，请清除筛选或核对 key`
            : '请检查后端服务连接'}
          onRetry={isProject404 ? undefined : () => queryClient.invalidateQueries({ queryKey: deviceKeys.allLists() })}
          action={isProject404 ? (
            <Button variant="outline" onClick={() => setProjectKey(undefined)}>
              清除项目筛选
            </Button>
          ) : undefined}
        />
      </PageContainer>
    );
  }

  const baseAllCount = facetsQ.data?.status_counts.all ?? filteredTotal;
  if (formattedDevices.length === 0 && baseAllCount === 0 && !hasActivePageFilter) {
    return (
      <PageContainer width="wide" className="py-4">
        <PageHeader title="物理设备" subtitle="管理和监控测试设备" action={addDeviceAction} />
        <EmptyState
          title="还没有设备"
          description="添加您的第一台测试设备"
          icon={<Smartphone className="w-16 h-16" />}
          action={isAdmin ? (
            <Button onClick={() => setIsModalOpen(true)}>
              <Plus className="w-4 h-4 mr-2" />
              添加设备
            </Button>
          ) : undefined}
        />
        {(!includeStale || !includeRetired) && (
          <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
            <span className={cn('text-xs', TEXT.subtitle)}>默认隐藏了陈旧/已退役设备：</span>
            {!includeStale && (
              <Button variant="outline" size="sm" data-testid="device-empty-show-stale"
                      onClick={() => setIncludeStale(true)}>
                显示陈旧设备
              </Button>
            )}
            {!includeRetired && (
              <Button variant="outline" size="sm" data-testid="device-empty-show-retired"
                      onClick={() => setIncludeRetired(true)}>
                显示已退役设备
              </Button>
            )}
          </div>
        )}
        <AddDeviceModal
          isOpen={isModalOpen}
          onClose={() => setIsModalOpen(false)}
          onSubmit={createMutation.mutate}
          isSubmitting={createMutation.isPending}
        />
      </PageContainer>
    );
  }

  const pageDeviceMap = new Map(formattedDevices.map((d) => [d.id, d]));

  return (
    <PageContainer width="wide" className="py-2.5 lg:py-2.5">
      <PageHeader
        title="物理设备"
        subtitle="管理和监控测试设备 · 点击设备行查看详情，勾选后可批量处理"
        action={addDeviceAction}
      />

      {/* Device Table（服务端分页 + 独立三级 Facets） */}
      <div>
        <ExpandableDeviceTable
          serverMode
          serverFacets={facetsQ.data}
          serverFilteredTotal={filteredTotal}
          devices={formattedDevices}
          totalCount={facetsQ.data?.status_counts.all ?? filteredTotal}
          pageSize={pageSize}
          pageSizeOptions={PAGE_SIZE_OPTIONS}
          onPageSizeChange={(nextSize) =>
            updateParams({ pageSize: nextSize === 50 ? undefined : nextSize }, { resetPage: true })
          }
          serverSortBy={sortState.by}
          serverSortDir={sortState.dir}
          onServerSortChange={(by, dir) => {
            setSortState({ by, dir });
            if (currentPage !== 1) updateParams({ page: undefined });
          }}
          drawerDeviceOverride={drawerDeviceOverride}
          selectedIds={selectedDeviceIds}
          onSelectionChange={(nextIds) => {
            if (crossPagePreview !== null) setCrossPagePreview(null);
            setSelectedDeviceIds(nextIds);
          }}
          isAdmin={isAdmin}
          onEditDeviceTags={isAdmin ? (device) => setSingleTagDevice(device) : undefined}
          statusFilter={statusFilter}
          onStatusFilterChange={(val) =>
            updateParams({ status: val === 'all' ? undefined : val }, { resetPage: true })
          }
          hostFilter={hostFilter}
          onHostFilterChange={(val) =>
            updateParams({ host: val === 'all' ? undefined : val }, { resetPage: true })
          }
          modelFilter={modelFilter}
          onModelFilterChange={(val) =>
            updateParams({ model: val === 'all' ? undefined : val }, { resetPage: true })
          }
          productFilter={productFilter}
          onProductFilterChange={(val) =>
            updateParams({ product: val === 'all' ? undefined : val }, { resetPage: true })
          }
          versionFilter={versionFilter}
          onVersionFilterChange={(val) =>
            updateParams({ version: val === 'all' ? undefined : val }, { resetPage: true })
          }
          searchQuery={searchQuery}
          onSearchQueryChange={(val) =>
            updateParams({ q: val || undefined }, { resetPage: true })
          }
          taggedOnly={taggedOnly}
          onTaggedOnlyChange={(val) => updateParams({ tagged: val }, { resetPage: true })}
          onClearQuickView={() => updateParams({ status: undefined, tagged: undefined }, { resetPage: true })}
          currentPage={currentPage}
          onPageChange={(page) => updateParams({ page: page <= 1 ? undefined : page })}
          includeStale={includeStale}
          onIncludeStaleChange={setIncludeStale}
          includeRetired={includeRetired}
          onIncludeRetiredChange={setIncludeRetired}
          projectChipLabel={
            projectKey === UNASSIGNED_FILTER_VALUE ? '未归属项目' : projectKey
          }
          onClearProjectFilter={() => setProjectKey(undefined)}
          extraToolbarControls={
            <ProjectFilterSelect
              value={projectKey}
              onChange={setProjectKey}
              className="w-full sm:w-[122px] shrink-0"
              testId="device-project-filter"
              showUnassigned
            />
          }
          onResetAllFilters={() =>
            clearParams([
              'project',
              'stale',
              'retired',
              'status',
              'host',
              'model',
              'product',
              'version',
              'q',
              'tagged',
              'page',
            ])
          }
          onNavigateToHost={(hostName) =>
            navigate(`/hosts?q=${encodeURIComponent(hostName)}`)
          }
          drawerDeviceId={drawerDeviceId}
          onDrawerDeviceIdChange={(nextId) =>
            updateParams({ drawer: nextId ?? undefined })
          }
        />
      </div>

      <DeviceBulkActionBar
        selectedCount={effectiveSelectedCount}
        filteredCount={filteredTotal}
        selectedFilteredCount={effectiveSelectedCount}
        selectionScope={isCrossPageMode ? 'all_filtered' : 'page'}
        crossPagePreviewLoading={crossPageLoading}
        writeLimitExceeded={Boolean(crossPagePreview?.exceeds_limit)}
        writeMaxAllowed={crossPagePreview?.max_allowed ?? null}
        statusSummary={statusSummary}
        canEditTags={isAdmin}
        tagUpdatePending={tagUpdateMutation.isPending}
        canAssignProject={isAdmin}
        canSwipeTrail={isAdmin}
        swipeTrailPending={swipeTrailMutation.isPending}
        canSkipOobe={isAdmin}
        skipOobePending={skipOobeMutation.isPending}
        canRetire={isAdmin}
        retirePending={retireMutation.isPending}
        exportPending={readOnlyExportPending}
        onSelectAllFiltered={() => void handleSelectAllFiltered()}
        onEditTags={() => setIsTagDialogOpen(true)}
        onAssignProject={() => setIsAssignDialogOpen(true)}
        onSwipeTrailOn={() => swipeTrailMutation.mutate(true)}
        onSwipeTrailOff={() => swipeTrailMutation.mutate(false)}
        onSkipOobe={() => skipOobeMutation.mutate()}
        onRetire={() => setRetireDialog({ open: true, mode: 'retire' })}
        onUnretire={() => setRetireDialog({ open: true, mode: 'unretire' })}
        onCopySerials={() => void handleCopySerials()}
        onExport={() => void handleExportSelected()}
        onClear={handleClearSelection}
      />

      <AddDeviceModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSubmit={(data) => createMutation.mutate(data)}
        isSubmitting={createMutation.isPending}
      />

      <BatchEditDeviceTagsDialog
        isOpen={isTagDialogOpen || singleTagDevice !== null}
        selectedCount={singleTagDevice ? 1 : effectiveSelectedIds.length}
        isSubmitting={tagUpdateMutation.isPending}
        onClose={() => {
          setIsTagDialogOpen(false);
          setSingleTagDevice(null);
        }}
        onSubmit={(operation, tags) => {
          const targetIds = singleTagDevice ? [singleTagDevice.id] : effectiveSelectedIds;
          const map = new Map(pageDeviceMap);
          if (singleTagDevice) map.set(singleTagDevice.id, singleTagDevice);
          tagUpdateMutation.mutate({ targetIds, pageById: map, operation, tags });
        }}
      />

      <RetireDevicesDialog
        isOpen={retireDialog.open}
        mode={retireDialog.mode}
        selectedCount={effectiveSelectedIds.length}
        idempotentSkipCount={idempotentSkipCount}
        isSubmitting={retireMutation.isPending}
        onClose={() => setRetireDialog((previous) => ({ ...previous, open: false }))}
        onSubmit={(mode, reason) => retireMutation.mutate({ mode, reason })}
      />

      <AssignProjectDialog
        isOpen={isAssignDialogOpen}
        selectedCount={effectiveSelectedIds.length}
        affectedModels={bulkAssignPreview.affectedModels}
        devicesWithoutModelSerials={bulkAssignPreview.devicesWithoutModelSerials}
        isSubmitting={assignProjectMutation.isPending}
        onClose={() => setIsAssignDialogOpen(false)}
        onSubmit={(targetProjectKey) =>
          assignProjectMutation.mutate({ targetProjectKey })
        }
      />

    </PageContainer>
  );
}
