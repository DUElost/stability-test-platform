import { deviceProductText } from '@/utils/deviceProduct';
import type { DeviceUsbPresence } from '@/utils/api/types';
import { useState, useMemo, useEffect, useRef, type ReactNode } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { cn } from '@/lib/utils';
import { BulkBarSpacer } from '@/components/ui/bulk-action-bar';
import { ClickableRow } from '@/components/ui/clickable-row';
import {
  FilterSearchInput,
  FilterWorkbench,
  MoreFiltersPopover,
  type ActiveFilterChip,
} from '@/components/ui/filter-bar';
import { SearchableSelect } from '@/components/ui/searchable-select';
import {
  Table,
  TableBody,
  TableCell,
  TableEmptyRow,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Progress } from '@/components/ui/progress';
import { StatusBadge } from '@/components/ui/status-badge';
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  Cpu,
  GitBranch,
  Smartphone,
  Server,
  Wifi,
  WifiOff,
  AlertTriangle,
  CheckCircle2,
  Zap,
  Tag,
} from 'lucide-react';
import { ENTITY_STATUS_COLORS } from '@/design-system/colors';
import { ALERT_BOX, resourceUsageBgClass, resourceUsageTextClass } from '@/design-system/tokens';
import { formatBytes, formatDateTimeFull } from '@/utils/format';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { DeviceDetailDrawer } from './DeviceDetailDrawer';
import type { ReturnStateEntry } from './deviceReturn';

export type DeviceStatus = 'idle' | 'testing' | 'offline' | 'error';

function temperatureTextClass(temperature: number): string {
  if (temperature > 45) return 'text-destructive';
  if (temperature > 40) return 'text-warning';
  return 'text-foreground';
}

function latencyTextClass(latency: number): string {
  if (latency > 300) return 'text-destructive';
  if (latency > 120) return 'text-warning';
  return 'text-success';
}

/** /data 分区用量摘要（字节口径，#2757 心跳上报）；未上报或 total<=0 → null，不猜。 */
function diskSummary(device: DeviceTableData): { pct: number; total: number; used: number; free: number } | null {
  const total = device.disk_total;
  const used = device.disk_used;
  if (total == null || used == null || total <= 0) return null;
  return {
    pct: Math.min(100, (used / total) * 100),
    total,
    used,
    free: Math.max(0, total - used),
  };
}

export interface DeviceTableData {
  id: number;
  serial: string;
  model: string;
  product?: string | null;
  status: DeviceStatus;
  battery_level?: number;
  temperature?: number;
  network_latency?: number | null;
  build_display_id?: string | null;
  host_id?: string | number;
  host_name?: string | null;
  current_task?: string;
  last_seen?: string;
  tags?: string[];
  /** ADR-0029：归属项目 key（F2 口径） */
  project_key?: string | null;
  /** ADR-0029 P0：归属来源四态（rule / manual / pinned / unassigned），行内 badge 用 */
  attribution_source?: 'mapped' | 'unmapped' | null;
  /** adb 连接状态（unauthorized = USB 调试未授权，读不到型号/平台） */
  adb_state?: string | null;
  /** /data 分区总容量（字节，心跳上报；未上报为 null） */
  disk_total?: number | null;
  /** /data 分区已用（字节，心跳上报；未上报为 null） */
  disk_used?: number | null;
  /** ADR-0057 D1（#2962 B）：退役事实（null = 在役） */
  retired_at?: string | null;
  /** #2962 A：陈旧（OFFLINE 且 7 天未见；后端现算派生） */
  is_stale?: boolean;
  /** ADR-0057 E4：陈旧超过 30 天的退役建议（只提示，不动作） */
  retire_suggested?: boolean;
  /** ADR-0061 D1.3 / #3723 U6：按专项归还状态（null/缺省 = 未初始化）。 */
  return_state?: Record<string, ReturnStateEntry> | null;
  /** ADR-0063 D7 / #3918：vbus 槽位 USB 在位（null = 主机无槽位映射或未上报）。 */
  usb_presence?: DeviceUsbPresence | null;
}

const USB_PRESENCE_LABEL: Record<DeviceUsbPresence['state'], { text: string; className: string; hint: string }> = {
  powered_off: { text: 'USB 关机', className: 'bg-info/15 text-info', hint: '槽位口为关机态（0e8d:2046），可走 USB 切电刷机' },
  adb_mode: { text: 'USB 开机', className: 'bg-success/15 text-success', hint: '槽位口为开机 ADB 态（0e8d:201c）' },
  other: { text: 'USB 其他', className: 'bg-warning/20 text-warning', hint: '槽位口有枚举，但不是已知的关机或开机态' },
  absent: { text: 'USB 空口', className: 'bg-destructive/15 text-destructive', hint: '槽位口下没有 USB 设备：检查连线或 Hub 供电' },
};

function usbPresenceTitle(presence: DeviceUsbPresence): string {
  const label = USB_PRESENCE_LABEL[presence.state];
  const parts = [label.hint, `槽位 ${presence.slot}`, `VID:PID ${presence.vid_pid ?? '无'}`];
  if (presence.reported_at) parts.push(`上报于 ${presence.reported_at}`);
  if (!presence.host_online) parts.push('主机离线：数据可能已过期');
  return parts.join(' · ');
}

interface ExpandableDeviceTableProps {
  devices: DeviceTableData[];
  /** ADR-0061 D5.7 / #3723 U6：人工放行入口只对管理员显示。 */
  isAdmin?: boolean;
  /** 单台设备行内编辑标签回调（传入时在详情卡显示编辑入口）。 */
  onEditDeviceTags?: (device: DeviceTableData) => void;
  /**
   * 服务端报告的设备总数（`GET /devices` 的 `total`）。用于「全部设备」卡显示真实
   * 总数，并在 `devices` 少于它时提示列表不完整（#3131）。
   *
   * 不传时退化为 `devices.length`；但 `limit` 只是**单次响应护栏**，设备数越过它时
   * 已加载条数会小于真实总数，所以真实页面（DevicesPage）必须传。
   */
  totalCount?: number;
  selectedIds?: Set<number>;
  onSelectionChange?: (ids: Set<number>) => void;
  onFilteredDevicesChange?: (devices: DeviceTableData[]) => void;
  /** 可选受控筛选状态（由 DevicesPage 同步到 URL）。 */
  statusFilter?: string;
  onStatusFilterChange?: (value: string) => void;
  modelFilter?: string;
  onModelFilterChange?: (value: string) => void;
  productFilter?: string;
  onProductFilterChange?: (value: string) => void;
  versionFilter?: string;
  onVersionFilterChange?: (value: string) => void;
  hostFilter?: string;
  onHostFilterChange?: (value: string) => void;
  searchQuery?: string;
  onSearchQueryChange?: (value: string) => void;
  taggedOnly?: boolean;
  onTaggedOnlyChange?: (value: boolean) => void;
  currentPage?: number;
  onPageChange?: (page: number) => void;
  includeStale?: boolean;
  onIncludeStaleChange?: (value: boolean) => void;
  includeRetired?: boolean;
  onIncludeRetiredChange?: (value: boolean) => void;
  projectChipLabel?: string;
  onClearProjectFilter?: () => void;
  extraToolbarControls?: ReactNode;
  extraHeaderRight?: ReactNode;
  onClearQuickView?: () => void;
  onResetAllFilters?: () => void;
  onNavigateToHost?: (hostName: string) => void;
  /** 可选受控右侧详情抽屉设备 ID（由 DevicesPage 同步至 URL ?drawer=） */
  drawerDeviceId?: number | null;
  onDrawerDeviceIdChange?: (deviceId: number | null) => void;
}

type SortKey = 'serial' | 'status' | 'battery_level' | 'temperature' | 'host_name' | 'last_seen';
type SortDir = 'asc' | 'desc';

const STATUS_SORT_RANK: Record<DeviceStatus, number> = {
  error: 0,
  offline: 1,
  testing: 2,
  idle: 3,
};

function matchesStatusFilter(status: DeviceStatus, filter: string): boolean {
  if (filter === 'all') return true;
  if (filter === 'attention') return status === 'offline' || status === 'error';
  return status === filter;
}

function hostFilterKey(device: DeviceTableData): string {
  return device.host_id != null ? String(device.host_id) : 'unassigned';
}

export function ExpandableDeviceTable({
  devices,
  totalCount,
  selectedIds,
  onSelectionChange,
  onFilteredDevicesChange,
  isAdmin = false,
  onEditDeviceTags,
  statusFilter: controlledStatusFilter,
  onStatusFilterChange,
  modelFilter: controlledModelFilter,
  onModelFilterChange,
  productFilter: controlledProductFilter,
  onProductFilterChange,
  versionFilter: controlledVersionFilter,
  onVersionFilterChange,
  hostFilter: controlledHostFilter,
  onHostFilterChange,
  searchQuery: controlledSearchQuery,
  onSearchQueryChange,
  taggedOnly: controlledTaggedOnly,
  onTaggedOnlyChange,
  currentPage: controlledCurrentPage,
  onPageChange,
  includeStale,
  onIncludeStaleChange,
  includeRetired,
  onIncludeRetiredChange,
  projectChipLabel,
  onClearProjectFilter,
  extraToolbarControls,
  extraHeaderRight,
  onClearQuickView,
  onResetAllFilters,
  onNavigateToHost,
  drawerDeviceId: controlledDrawerDeviceId,
  onDrawerDeviceIdChange,
}: ExpandableDeviceTableProps) {
  const [localDrawerDeviceId, setLocalDrawerDeviceId] = useState<number | null>(null);
  const [sortConfig, setSortConfig] = useState<{ key: SortKey; dir: SortDir } | null>(null);
  const [localStatusFilter, setLocalStatusFilter] = useState<string>('all');
  const [localModelFilter, setLocalModelFilter] = useState('all');
  const [localProductFilter, setLocalProductFilter] = useState('all');
  const [localVersionFilter, setLocalVersionFilter] = useState('all');
  const [localHostFilter, setLocalHostFilter] = useState('all');
  const [localSearchQuery, setLocalSearchQuery] = useState('');
  const [localTaggedOnly, setLocalTaggedOnly] = useState(false);
  const [localCurrentPage, setLocalCurrentPage] = useState(1);

  const drawerDeviceId =
    controlledDrawerDeviceId !== undefined ? controlledDrawerDeviceId : localDrawerDeviceId;
  const setDrawerDeviceId = onDrawerDeviceIdChange ?? setLocalDrawerDeviceId;

  const statusFilter = controlledStatusFilter ?? localStatusFilter;
  const setStatusFilter = onStatusFilterChange ?? setLocalStatusFilter;
  const modelFilter = controlledModelFilter ?? localModelFilter;
  const setModelFilter = onModelFilterChange ?? setLocalModelFilter;
  const productFilter = controlledProductFilter ?? localProductFilter;
  const setProductFilter = onProductFilterChange ?? setLocalProductFilter;
  const versionFilter = controlledVersionFilter ?? localVersionFilter;
  const setVersionFilter = onVersionFilterChange ?? setLocalVersionFilter;
  const hostFilter = controlledHostFilter ?? localHostFilter;
  const setHostFilter = onHostFilterChange ?? setLocalHostFilter;
  const searchQuery = controlledSearchQuery ?? localSearchQuery;
  const setSearchQuery = onSearchQueryChange ?? setLocalSearchQuery;
  const taggedOnly = controlledTaggedOnly ?? localTaggedOnly;
  const setTaggedOnly = onTaggedOnlyChange ?? setLocalTaggedOnly;
  const currentPage = controlledCurrentPage ?? localCurrentPage;
  const setCurrentPage = onPageChange ?? setLocalCurrentPage;
  const selectPageRef = useRef<HTMLInputElement>(null);
  const tableContainerRef = useRef<HTMLDivElement>(null);
  const pageSize = 50;
  const selectable = !!onSelectionChange;

  // 网络/存储列：全空时不渲染（B7——生产 515 台无 latency，白占 ~15% 宽）；
  // 标签列恒显示（#3962 #6），避免打标签后表头列集突变。
  const hasNetworkData = devices.some((d) => d.network_latency != null);
  const hasDiskData = devices.some((d) => diskSummary(d) != null);

  // 防抖搜索，减少不必要的过滤计算
  const debouncedSearch = useDebouncedValue(searchQuery, 300);

  const devicesInStatusAndTagScope = useMemo(
    () =>
      devices.filter((device) => {
        if (!matchesStatusFilter(device.status, statusFilter)) return false;
        if (taggedOnly && (!device.tags || device.tags.length === 0)) return false;
        return true;
      }),
    [devices, statusFilter, taggedOnly],
  );

  const hostOptions = useMemo(() => {
    const byKey = new Map<string, string>();
    for (const device of devicesInStatusAndTagScope) {
      const key = hostFilterKey(device);
      if (byKey.has(key)) continue;
      byKey.set(key, device.host_name?.trim() || (key === 'unassigned' ? '未分配' : key));
    }
    return Array.from(byKey.entries())
      .map(([value, label]) => ({ value, label }))
      .sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'));
  }, [devicesInStatusAndTagScope]);

  const effectiveHostFilter =
    hostFilter !== 'all' && !hostOptions.some((opt) => opt.value === hostFilter)
      ? 'all'
      : hostFilter;

  const devicesInOptionScope = useMemo(
    () =>
      devicesInStatusAndTagScope.filter(
        (device) => effectiveHostFilter === 'all' || hostFilterKey(device) === effectiveHostFilter,
      ),
    [devicesInStatusAndTagScope, effectiveHostFilter],
  );

  const modelOptions = useMemo(
    () =>
      Array.from(new Set(devicesInOptionScope.map((device) => device.model).filter(Boolean))).sort((a, b) =>
        a.localeCompare(b, 'zh-CN'),
      ),
    [devicesInOptionScope],
  );

  const productOptions = useMemo(
    () =>
      Array.from(
        new Set(devicesInOptionScope.map((device) => device.product).filter((value): value is string => Boolean(value))),
      ).sort(),
    [devicesInOptionScope],
  );

  const versionOptions = useMemo(
    () =>
      Array.from(
        new Set(
          devicesInOptionScope
            .map((device) => device.build_display_id)
            .filter((value): value is string => Boolean(value)),
        ),
      ).sort((a, b) => a.localeCompare(b, 'zh-CN')),
    [devicesInOptionScope],
  );

  const effectiveModelFilter =
    modelFilter !== 'all' && !modelOptions.includes(modelFilter) ? 'all' : modelFilter;
  const effectiveProductFilter =
    productFilter !== 'all' && !productOptions.includes(productFilter) ? 'all' : productFilter;
  const effectiveVersionFilter =
    versionFilter !== 'all' && !versionOptions.includes(versionFilter) ? 'all' : versionFilter;

  // 将失效子选项清理放入 effect，禁止在 render 期调用 setSearchParams（#4117 U2-1a）
  useEffect(() => {
    if (hostFilter !== effectiveHostFilter) setHostFilter('all');
  }, [hostFilter, effectiveHostFilter, setHostFilter]);

  useEffect(() => {
    if (modelFilter !== effectiveModelFilter) setModelFilter('all');
  }, [modelFilter, effectiveModelFilter, setModelFilter]);

  useEffect(() => {
    if (productFilter !== effectiveProductFilter) setProductFilter('all');
  }, [productFilter, effectiveProductFilter, setProductFilter]);

  useEffect(() => {
    if (versionFilter !== effectiveVersionFilter) setVersionFilter('all');
  }, [versionFilter, effectiveVersionFilter, setVersionFilter]);

  const toggleSort = (key: SortKey) => {
    setSortConfig((prev) => {
      if (!prev || prev.key !== key) return { key, dir: key === 'last_seen' ? 'desc' : 'asc' };
      if (prev.dir === 'asc') return { key, dir: 'desc' };
      return null;
    });
  };

  const filteredDevices = useMemo(() => {
    return devices.filter(device => {
      if (!matchesStatusFilter(device.status, statusFilter)) return false;
      if (effectiveHostFilter !== 'all' && hostFilterKey(device) !== effectiveHostFilter) return false;
      if (effectiveModelFilter !== 'all' && device.model !== effectiveModelFilter) return false;
      if (effectiveProductFilter !== 'all' && device.product !== effectiveProductFilter) return false;
      if (effectiveVersionFilter !== 'all' && device.build_display_id !== effectiveVersionFilter) return false;
      if (taggedOnly && (!device.tags || device.tags.length === 0)) return false;
      if (debouncedSearch) {
        const query = debouncedSearch.toLowerCase();
        return (
          device.model?.toLowerCase().includes(query) ||
          deviceProductText(device.model, device.product).toLowerCase().includes(query) ||
          device.serial.toLowerCase().includes(query) ||
          device.build_display_id?.toLowerCase().includes(query) ||
          device.host_name?.toLowerCase().includes(query) ||
          device.tags?.some((tag) => tag.toLowerCase().includes(query))
        );
      }
      return true;
    });
  }, [
    devices,
    statusFilter,
    effectiveHostFilter,
    effectiveModelFilter,
    effectiveProductFilter,
    effectiveVersionFilter,
    taggedOnly,
    debouncedSearch,
  ]);

  const sortedDevices = useMemo(() => {
    if (!sortConfig) return filteredDevices;
    const { key, dir } = sortConfig;
    const factor = dir === 'asc' ? 1 : -1;
    return [...filteredDevices].sort((a, b) => {
      switch (key) {
        case 'serial':
          return a.serial.localeCompare(b.serial) * factor;
        case 'status':
          return (STATUS_SORT_RANK[a.status] - STATUS_SORT_RANK[b.status]) * factor;
        case 'battery_level': {
          const av = a.battery_level ?? -1;
          const bv = b.battery_level ?? -1;
          return (av - bv) * factor;
        }
        case 'temperature': {
          const av = a.temperature ?? -999;
          const bv = b.temperature ?? -999;
          return (av - bv) * factor;
        }
        case 'host_name':
          return (a.host_name ?? '').localeCompare(b.host_name ?? '', 'zh-CN') * factor;
        case 'last_seen': {
          const at = a.last_seen ? Date.parse(a.last_seen) || 0 : 0;
          const bt = b.last_seen ? Date.parse(b.last_seen) || 0 : 0;
          return (at - bt) * factor;
        }
      }
    });
  }, [filteredDevices, sortConfig]);

  const activeDrawerIndex = useMemo(
    () => (drawerDeviceId == null ? -1 : sortedDevices.findIndex((d) => d.id === drawerDeviceId)),
    [drawerDeviceId, sortedDevices],
  );
  const activeDrawerDevice = useMemo(() => {
    if (drawerDeviceId == null) return null;
    if (activeDrawerIndex >= 0) return sortedDevices[activeDrawerIndex];
    return devices.find((d) => d.id === drawerDeviceId) ?? null;
  }, [activeDrawerIndex, devices, drawerDeviceId, sortedDevices]);

  // Reset to page 1 when filter or search changes (in effect, never during render)
  const pageResetKey = JSON.stringify([
    statusFilter,
    effectiveHostFilter,
    effectiveModelFilter,
    effectiveProductFilter,
    effectiveVersionFilter,
    taggedOnly,
    debouncedSearch,
  ]);
  const prevPageResetKeyRef = useRef(pageResetKey);
  useEffect(() => {
    if (prevPageResetKeyRef.current !== pageResetKey) {
      prevPageResetKeyRef.current = pageResetKey;
      if (currentPage !== 1) setCurrentPage(1);
    }
  }, [pageResetKey, currentPage, setCurrentPage]);

  const totalPages = Math.max(1, Math.ceil(sortedDevices.length / pageSize));
  const safeCurrentPage = Math.min(currentPage, totalPages);
  useEffect(() => {
    if (safeCurrentPage !== currentPage) {
      setCurrentPage(safeCurrentPage);
    }
  }, [safeCurrentPage, currentPage, setCurrentPage]);
  const paginatedDevices = useMemo(
    () => sortedDevices.slice((safeCurrentPage - 1) * pageSize, safeCurrentPage * pageSize),
    [sortedDevices, safeCurrentPage, pageSize],
  );

  // D5 Phase 1 虚拟化：在固定高度滚动容器内按需渲染可见行，降低 DOM 节点数
  const rowVirtualizer = useVirtualizer({
    count: paginatedDevices.length,
    getScrollElement: () =>
      tableContainerRef.current?.querySelector<HTMLDivElement>('[data-slot="table-scroll-container"]') ??
      null,
    estimateSize: () => 56,
    overscan: 10,
    initialRect: { width: 1200, height: 640 },
    observeElementRect: (instance, cb) => {
      const el = instance.scrollElement;
      if (!el) return;
      const measure = () => {
        const rect = el.getBoundingClientRect();
        if (rect.height > 0) {
          cb({ width: rect.width, height: rect.height });
        } else {
          cb({ width: 1200, height: 640 });
        }
      };
      measure();
      if (typeof ResizeObserver === 'undefined') return;
      const ro = new ResizeObserver(measure);
      ro.observe(el);
      return () => ro.disconnect();
    },
  });
  const virtualRows = rowVirtualizer.getVirtualItems();
  const virtualPaddingTop = virtualRows.length > 0 ? virtualRows[0].start : 0;
  const virtualPaddingBottom =
    virtualRows.length > 0
      ? rowVirtualizer.getTotalSize() - virtualRows[virtualRows.length - 1].end
      : 0;
  const visibleColumnCount =
    (selectable ? 1 : 0) + 8 + (hasDiskData ? 1 : 0) + (hasNetworkData ? 1 : 0);

  const pageDeviceIds = useMemo(() => paginatedDevices.map((device) => device.id), [paginatedDevices]);
  const allPageSelected = pageDeviceIds.length > 0 && pageDeviceIds.every((id) => selectedIds?.has(id));
  const somePageSelected = pageDeviceIds.some((id) => selectedIds?.has(id));

  useEffect(() => {
    onFilteredDevicesChange?.(filteredDevices);
  }, [filteredDevices, onFilteredDevicesChange]);

  useEffect(() => {
    if (!selectPageRef.current) return;
    selectPageRef.current.indeterminate = somePageSelected && !allPageSelected;
  }, [allPageSelected, somePageSelected]);

  const togglePageSelection = () => {
    if (!onSelectionChange) return;
    const next = new Set(selectedIds ?? []);
    if (allPageSelected) {
      pageDeviceIds.forEach((id) => next.delete(id));
    } else {
      pageDeviceIds.forEach((id) => next.add(id));
    }
    onSelectionChange(next);
  };

  const toggleDeviceSelection = (id: number, event: React.MouseEvent) => {
    event.stopPropagation();
    if (!onSelectionChange) return;
    const next = new Set(selectedIds ?? []);
    if (next.has(id)) next.delete(id); else next.add(id);
    onSelectionChange(next);
  };

  const truncated = totalCount != null && devices.length < totalCount;
  const stats = useMemo(() => ({
    // 真实总数优先：拿已加载条数当总数会在越过单次响应护栏时静默少报（#3131）
    total: totalCount ?? devices.length,
    idle: devices.filter(d => d.status === 'idle').length,
    testing: devices.filter(d => d.status === 'testing').length,
    offline: devices.filter(d => d.status === 'offline').length,
    error: devices.filter(d => d.status === 'error').length,
  }), [devices, totalCount]);

  const handlePageChange = (updater: (prev: number) => number) => {
    setCurrentPage(updater(safeCurrentPage));
    const scrollEl = tableContainerRef.current?.querySelector('[data-slot="table-scroll-container"]');
    if (scrollEl) scrollEl.scrollTop = 0;
    tableContainerRef.current?.scrollIntoView?.({ block: 'start' });
  };

  return (
    <div className="space-y-2">
      {/* #3131：列表不完整时必须说出来——下方状态统计与搜索/筛选都只作用于已加载
          部分，静默截断会让「搜不到」被读成「不存在」 */}
      {truncated && (
        <div
          role="alert"
          data-testid="device-table-truncated"
          className={cn('flex items-start gap-2 rounded-lg px-3 py-2 text-xs', ALERT_BOX.warning)}
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            设备总数 {totalCount} 台，本次仅加载 {devices.length} 台：下方状态统计与
            搜索/筛选只作用于已加载部分，未加载的设备不会出现在结果里。
          </span>
        </div>
      )}

      {/* 统一单行工作台：顶行合并状态统计与快捷视角（省去 92px 稀疏五卡），主筛选行严格单行 */}
      {(() => {
        // 只为「更多筛选」内部专属且未在顶层直接展示的条件渲染 Chip（陈旧 / 退役）
        const hiddenChips: ActiveFilterChip[] = [];
        if (includeStale && onIncludeStaleChange) {
          hiddenChips.push({
            key: 'stale',
            label: '范围',
            value: '含陈旧设备',
            onRemove: () => onIncludeStaleChange(false),
          });
        }
        if (includeRetired && onIncludeRetiredChange) {
          hiddenChips.push({
            key: 'retired',
            label: '范围',
            value: '含已退役',
            onRemove: () => onIncludeRetiredChange(false),
          });
        }

        // 只统计存在于「更多筛选」内的条件（陈旧 / 退役）
        const moreActiveCount =
          (includeStale && onIncludeStaleChange ? 1 : 0) +
          (includeRetired && onIncludeRetiredChange ? 1 : 0);
        const hasMoreFilters = Boolean(onIncludeStaleChange || onIncludeRetiredChange);

        const hasAnyActiveFilter =
          statusFilter !== 'all' ||
          effectiveHostFilter !== 'all' ||
          effectiveModelFilter !== 'all' ||
          effectiveProductFilter !== 'all' ||
          effectiveVersionFilter !== 'all' ||
          taggedOnly ||
          Boolean(searchQuery.trim()) ||
          Boolean(projectChipLabel) ||
          Boolean(includeStale) ||
          Boolean(includeRetired);

        const handleResetAll = () => {
          if (onResetAllFilters) {
            onResetAllFilters();
            return;
          }
          setStatusFilter('all');
          setHostFilter('all');
          setModelFilter('all');
          setProductFilter('all');
          setVersionFilter('all');
          setTaggedOnly(false);
          setSearchQuery('');
          onClearProjectFilter?.();
          onIncludeStaleChange?.(false);
          onIncludeRetiredChange?.(false);
        };

        const taggedCount = devices.filter((d) => d.tags && d.tags.length > 0).length;
        const attentionCount = stats.offline + stats.error;

        const unifiedStatusViews = (
          <div
            data-testid="device-status-strip"
            className="flex flex-wrap items-center gap-1"
          >
            <button
              type="button"
              aria-pressed={statusFilter === 'all' && !taggedOnly}
              onClick={() => {
                if (onClearQuickView) {
                  onClearQuickView();
                  return;
                }
                setStatusFilter('all');
                setTaggedOnly(false);
              }}
              className={cn(
                'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors',
                statusFilter === 'all' && !taggedOnly
                  ? 'border-foreground/35 bg-muted/60 font-medium text-foreground'
                  : 'border-transparent text-muted-foreground hover:bg-muted/40 hover:text-foreground',
              )}
            >
              <Smartphone className="h-3.5 w-3.5 shrink-0" />
              <span>全部设备</span>
              <span className="font-mono font-semibold tabular-nums text-foreground">{stats.total}</span>
            </button>
            <button
              type="button"
              aria-pressed={statusFilter === 'idle'}
              onClick={() => setStatusFilter(statusFilter === 'idle' ? 'all' : 'idle')}
              className={cn(
                'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors',
                statusFilter === 'idle'
                  ? 'border-success bg-success/10 font-medium text-foreground'
                  : 'border-transparent text-muted-foreground hover:bg-success/5 hover:text-foreground',
              )}
            >
              <CheckCircle2 className={cn('h-3.5 w-3.5 shrink-0', ENTITY_STATUS_COLORS.device.idle)} />
              <span>空闲</span>
              <span className={cn('font-mono font-semibold tabular-nums', ENTITY_STATUS_COLORS.device.idle)}>
                {stats.idle}
              </span>
            </button>
            <button
              type="button"
              aria-pressed={statusFilter === 'testing'}
              onClick={() => setStatusFilter(statusFilter === 'testing' ? 'all' : 'testing')}
              className={cn(
                'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors',
                statusFilter === 'testing'
                  ? 'border-primary bg-primary/10 font-medium text-foreground'
                  : 'border-transparent text-muted-foreground hover:bg-primary/5 hover:text-foreground',
              )}
            >
              <Zap
                className={cn(
                  'h-3.5 w-3.5 shrink-0',
                  stats.testing > 0 ? ENTITY_STATUS_COLORS.device.testing : 'text-muted-foreground',
                )}
              />
              <span>测试中</span>
              <span
                className={cn(
                  'font-mono font-semibold tabular-nums',
                  stats.testing > 0 ? ENTITY_STATUS_COLORS.device.testing : 'text-muted-foreground',
                )}
              >
                {stats.testing}
              </span>
            </button>
            <button
              type="button"
              aria-pressed={statusFilter === 'offline'}
              onClick={() => setStatusFilter(statusFilter === 'offline' ? 'all' : 'offline')}
              className={cn(
                'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors',
                statusFilter === 'offline'
                  ? 'border-foreground/35 bg-muted/60 font-medium text-foreground'
                  : 'border-transparent text-muted-foreground hover:bg-muted/40 hover:text-foreground',
              )}
            >
              <WifiOff className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span>离线</span>
              <span className="font-mono font-semibold tabular-nums text-muted-foreground">{stats.offline}</span>
            </button>
            <button
              type="button"
              aria-pressed={statusFilter === 'error'}
              onClick={() => setStatusFilter(statusFilter === 'error' ? 'all' : 'error')}
              className={cn(
                'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors',
                statusFilter === 'error'
                  ? 'border-destructive bg-destructive/10 font-medium text-foreground'
                  : 'border-transparent text-muted-foreground hover:bg-destructive/5 hover:text-foreground',
              )}
            >
              <AlertTriangle
                className={cn(
                  'h-3.5 w-3.5 shrink-0',
                  stats.error > 0 ? ENTITY_STATUS_COLORS.device.error : 'text-muted-foreground',
                )}
              />
              <span>错误</span>
              <span
                className={cn(
                  'font-mono font-semibold tabular-nums',
                  stats.error > 0 ? ENTITY_STATUS_COLORS.device.error : 'text-muted-foreground',
                )}
              >
                {stats.error}
              </span>
            </button>

            <span className="mx-1 hidden h-4 w-px bg-border sm:inline-block" aria-hidden="true" />

            <button
              type="button"
              aria-pressed={statusFilter === 'attention'}
              title="筛选离线或异常设备"
              onClick={() => setStatusFilter(statusFilter === 'attention' ? 'all' : 'attention')}
              className={cn(
                'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors',
                statusFilter === 'attention'
                  ? 'border-warning/40 bg-warning/10 font-medium text-warning'
                  : 'border-transparent text-muted-foreground hover:bg-muted/40 hover:text-foreground',
              )}
            >
              <span>需关注</span>
              <span className="font-mono font-semibold tabular-nums">{attentionCount}</span>
            </button>
            <button
              type="button"
              aria-pressed={taggedOnly}
              onClick={() => setTaggedOnly(!taggedOnly)}
              className={cn(
                'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors',
                taggedOnly
                  ? 'border-primary/35 bg-primary/10 font-medium text-primary'
                  : 'border-transparent text-muted-foreground hover:bg-muted/40 hover:text-foreground',
              )}
            >
              <Tag className="h-3 w-3 shrink-0" />
              <span>仅看有标签</span>
              <span className="font-mono font-semibold tabular-nums">{taggedCount}</span>
            </button>
          </div>
        );

        return (
          <FilterWorkbench
            headerLeft={unifiedStatusViews}
            headerRight={extraHeaderRight}
            hiddenChips={hiddenChips}
            hasAnyActiveFilter={hasAnyActiveFilter}
            onResetAll={handleResetAll}
            matchSummary={`匹配 ${filteredDevices.length} / ${devices.length} 台设备`}
            chipsTestId="devices-filter-chips"
          >
            <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center xl:flex-nowrap">
              <FilterSearchInput
                id="device-search"
                value={searchQuery}
                onChange={setSearchQuery}
                placeholder="搜索序列号 / 型号 / 主机..."
                ariaLabel="搜索设备"
                className="flex-1 min-w-[180px]"
              />
              {extraToolbarControls}
              <SearchableSelect
                value={effectiveHostFilter}
                onChange={setHostFilter}
                options={hostOptions}
                allLabel="全部主机"
                searchPlaceholder="输入主机名搜索..."
                icon={<Server />}
                ariaLabel="按所属主机筛选"
                className="w-full sm:w-[120px] xl:w-32"
                testId="device-host-filter"
              />
              <SearchableSelect
                value={effectiveModelFilter}
                onChange={setModelFilter}
                options={modelOptions.map((m) => ({ value: m, label: m }))}
                allLabel="全部型号"
                searchPlaceholder="输入型号搜索..."
                icon={<Smartphone />}
                ariaLabel="按型号筛选"
                className="w-full sm:w-[120px] xl:w-32"
                testId="device-model-filter"
              />
              <SearchableSelect
                value={effectiveProductFilter}
                onChange={setProductFilter}
                options={productOptions.map((p) => ({ value: p, label: p }))}
                allLabel="全部 product"
                searchPlaceholder="输入 product 搜索..."
                icon={<Cpu />}
                ariaLabel="按 product 筛选"
                className="w-full sm:w-[132px] xl:w-36"
                testId="device-product-filter"
              />
              <SearchableSelect
                value={effectiveVersionFilter}
                onChange={setVersionFilter}
                options={versionOptions.map((v) => ({ value: v, label: v }))}
                allLabel="全部版本"
                searchPlaceholder="输入版本号搜索..."
                icon={<GitBranch />}
                ariaLabel="按版本筛选"
                className="w-full sm:w-[120px] xl:w-32"
                testId="device-version-filter"
              />
              {hasMoreFilters && (
                <MoreFiltersPopover activeCount={moreActiveCount} testId="devices-more-filters">
                  {onIncludeStaleChange && (
                    <label className="flex cursor-pointer items-center justify-between rounded-lg border border-border px-3 py-2 text-xs hover:bg-muted/30">
                      <span>显示陈旧设备</span>
                      <input
                        type="checkbox"
                        checked={Boolean(includeStale)}
                        onChange={(e) => onIncludeStaleChange(e.target.checked)}
                        data-testid="device-toggle-stale"
                        className="rounded"
                      />
                    </label>
                  )}
                  {onIncludeRetiredChange && (
                    <label className="flex cursor-pointer items-center justify-between rounded-lg border border-border px-3 py-2 text-xs hover:bg-muted/30">
                      <span>显示已退役设备</span>
                      <input
                        type="checkbox"
                        checked={Boolean(includeRetired)}
                        onChange={(e) => onIncludeRetiredChange(e.target.checked)}
                        data-testid="device-toggle-retired"
                        className="rounded"
                      />
                    </label>
                  )}
                </MoreFiltersPopover>
              )}
            </div>
          </FilterWorkbench>
        );
      })()}

      {/* Table（固定视口滚容器 + sticky top-0 表头 + 虚拟化数据行 + ClickableRow） */}
      <div ref={tableContainerRef} className="rounded-xl border border-border bg-card">
        <Table containerClassName="max-h-[calc(100vh-225px)] min-h-[280px]">
          <TableHeader>
            <TableRow className="sticky top-0 z-20 bg-muted/95 hover:bg-muted/95 [&>th]:h-9">
              {selectable && (
                <TableHead className="sticky left-0 z-30 w-9 min-w-[36px] bg-muted px-2.5 py-2">
                  <input
                    ref={selectPageRef}
                    type="checkbox"
                    checked={allPageSelected}
                    onChange={togglePageSelection}
                    aria-label={`选择当前页设备（本页 ${pageDeviceIds.length} 台）`}
                    title={`全选当前页 ${pageDeviceIds.length} 台；其他页保持原选择`}
                    className="h-4 w-4 rounded border-border accent-primary"
                  />
                </TableHead>
              )}
              <TableHead
                aria-sort={
                  sortConfig?.key === 'serial'
                    ? sortConfig.dir === 'asc'
                      ? 'ascending'
                      : 'descending'
                    : 'none'
                }
                className={cn(
                  'sticky z-30 min-w-[160px] max-w-[240px] bg-muted font-medium shadow-[2px_0_4px_-2px_rgba(0,0,0,0.08)]',
                  selectable ? 'left-9' : 'left-0',
                )}
              >
                <button
                  type="button"
                  onClick={() => toggleSort('serial')}
                  className="inline-flex items-center gap-1 hover:text-foreground"
                >
                  <span>设备</span>
                  {sortConfig?.key === 'serial' ? (
                    sortConfig.dir === 'asc' ? (
                      <ArrowUp className="h-3 w-3 text-primary" />
                    ) : (
                      <ArrowDown className="h-3 w-3 text-primary" />
                    )
                  ) : (
                    <ArrowUpDown className="h-3 w-3 opacity-40" />
                  )}
                </button>
              </TableHead>
              <TableHead className="min-w-[64px] font-medium">版本</TableHead>
              <TableHead
                aria-sort={
                  sortConfig?.key === 'status'
                    ? sortConfig.dir === 'asc'
                      ? 'ascending'
                      : 'descending'
                    : 'none'
                }
                className="min-w-[112px] font-medium"
              >
                <button
                  type="button"
                  onClick={() => toggleSort('status')}
                  className="inline-flex items-center gap-1 hover:text-foreground"
                >
                  <span>状态</span>
                  {sortConfig?.key === 'status' ? (
                    sortConfig.dir === 'asc' ? (
                      <ArrowUp className="h-3 w-3 text-primary" />
                    ) : (
                      <ArrowDown className="h-3 w-3 text-primary" />
                    )
                  ) : (
                    <ArrowUpDown className="h-3 w-3 opacity-40" />
                  )}
                </button>
              </TableHead>
              <TableHead
                aria-sort={
                  sortConfig?.key === 'battery_level'
                    ? sortConfig.dir === 'asc'
                      ? 'ascending'
                      : 'descending'
                    : 'none'
                }
                className="min-w-[76px] font-medium"
              >
                <button
                  type="button"
                  onClick={() => toggleSort('battery_level')}
                  className="inline-flex items-center gap-1 hover:text-foreground"
                >
                  <span>电量</span>
                  {sortConfig?.key === 'battery_level' ? (
                    sortConfig.dir === 'asc' ? (
                      <ArrowUp className="h-3 w-3 text-primary" />
                    ) : (
                      <ArrowDown className="h-3 w-3 text-primary" />
                    )
                  ) : (
                    <ArrowUpDown className="h-3 w-3 opacity-40" />
                  )}
                </button>
              </TableHead>
              <TableHead
                aria-sort={
                  sortConfig?.key === 'temperature'
                    ? sortConfig.dir === 'asc'
                      ? 'ascending'
                      : 'descending'
                    : 'none'
                }
                className="min-w-[60px] font-medium"
              >
                <button
                  type="button"
                  onClick={() => toggleSort('temperature')}
                  className="inline-flex items-center gap-1 hover:text-foreground"
                >
                  <span>温度</span>
                  {sortConfig?.key === 'temperature' ? (
                    sortConfig.dir === 'asc' ? (
                      <ArrowUp className="h-3 w-3 text-primary" />
                    ) : (
                      <ArrowDown className="h-3 w-3 text-primary" />
                    )
                  ) : (
                    <ArrowUpDown className="h-3 w-3 opacity-40" />
                  )}
                </button>
              </TableHead>
              {hasDiskData && <TableHead className="min-w-[88px] font-medium">存储</TableHead>}
              {hasNetworkData && <TableHead className="min-w-[60px] font-medium">网络</TableHead>}
              <TableHead className="min-w-[84px] font-medium">标签</TableHead>
              <TableHead
                aria-sort={
                  sortConfig?.key === 'host_name'
                    ? sortConfig.dir === 'asc'
                      ? 'ascending'
                      : 'descending'
                    : 'none'
                }
                className="min-w-[88px] font-medium"
              >
                <button
                  type="button"
                  onClick={() => toggleSort('host_name')}
                  className="inline-flex items-center gap-1 hover:text-foreground"
                >
                  <span>所属主机</span>
                  {sortConfig?.key === 'host_name' ? (
                    sortConfig.dir === 'asc' ? (
                      <ArrowUp className="h-3 w-3 text-primary" />
                    ) : (
                      <ArrowDown className="h-3 w-3 text-primary" />
                    )
                  ) : (
                    <ArrowUpDown className="h-3 w-3 opacity-40" />
                  )}
                </button>
              </TableHead>
              <TableHead
                aria-sort={
                  sortConfig?.key === 'last_seen'
                    ? sortConfig.dir === 'asc'
                      ? 'ascending'
                      : 'descending'
                    : 'none'
                }
                className="min-w-[96px] font-medium text-right"
              >
                <button
                  type="button"
                  onClick={() => toggleSort('last_seen')}
                  className="ml-auto inline-flex items-center gap-1 hover:text-foreground"
                >
                  <span>最后活跃</span>
                  {sortConfig?.key === 'last_seen' ? (
                    sortConfig.dir === 'asc' ? (
                      <ArrowUp className="h-3 w-3 text-primary" />
                    ) : (
                      <ArrowDown className="h-3 w-3 text-primary" />
                    )
                  ) : (
                    <ArrowUpDown className="h-3 w-3 opacity-40" />
                  )}
                </button>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {virtualPaddingTop > 0 && (
              <TableRow aria-hidden="true" className="border-0 hover:bg-transparent">
                <TableCell
                  colSpan={visibleColumnCount}
                  className="border-0 p-0"
                  style={{ height: virtualPaddingTop }}
                />
              </TableRow>
            )}
            {virtualRows.map((virtualRow) => {
              const device = paginatedDevices[virtualRow.index];
              if (!device) return null;
              const isActive = drawerDeviceId === device.id;
              const isSelected = Boolean(selectedIds?.has(device.id));
              const disk = diskSummary(device);
              const stickyCellBg = isSelected
                ? 'bg-primary/5 group-hover:bg-primary/10'
                : isActive
                  ? 'bg-accent/60'
                  : 'bg-card group-hover:bg-muted/50';

              return (
                <ClickableRow
                  key={device.id}
                  role="button"
                  aria-label={`查看设备 ${device.serial} 详情`}
                  className={cn(
                    'group transition-colors',
                    isActive && 'bg-accent/60 hover:bg-accent/70',
                    isSelected && 'bg-primary/5 hover:bg-primary/10',
                  )}
                  data-state={isSelected ? 'selected' : undefined}
                  onClick={() => setDrawerDeviceId(device.id)}
                >
                  {selectable && (
                    <TableCell
                      className={cn(
                        'sticky left-0 z-10 w-9 min-w-[36px] px-2.5 py-1.5 transition-colors',
                        stickyCellBg,
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onClick={(event) => toggleDeviceSelection(device.id, event)}
                        onChange={() => {}}
                        aria-label={`选择设备 ${device.serial}`}
                        className="h-4 w-4 rounded border-border accent-primary"
                      />
                    </TableCell>
                  )}
                  <TableCell
                    className={cn(
                      'sticky z-10 min-w-[160px] max-w-[240px] px-3 py-1.5 shadow-[2px_0_4px_-2px_rgba(0,0,0,0.08)] transition-colors',
                      stickyCellBg,
                      selectable ? 'left-9' : 'left-0',
                    )}
                  >
                    <div className="truncate font-mono text-sm text-foreground" title={device.serial}>
                      {device.serial}
                    </div>
                    <div className="mt-0.5 flex items-center gap-1.5">
                      <span className="truncate text-xs text-muted-foreground" title={device.model ?? ''}>
                        {device.model}
                      </span>
                      {device.adb_state === 'unauthorized' ? (
                        <span
                          className="shrink-0 rounded-full bg-destructive/10 px-1.5 py-0.5 text-[11px] text-destructive-text"
                          title="USB 调试未授权：请在设备上允许授权，否则无法采集型号/平台"
                        >
                          未授权
                        </span>
                      ) : null}
                      {device.project_key ? (
                        <span
                          className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground"
                          title={`归属项目 ${device.project_key}`}
                        >
                          {device.project_key}
                        </span>
                      ) : null}
                      {device.attribution_source === 'mapped' ? null : (
                        <span
                          className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground"
                          title="型号未映射：在 /projects 添加型号成员"
                        >
                          未映射
                        </span>
                      )}
                    </div>
                    <div className="max-w-xs truncate font-mono text-xs text-muted-foreground" title={deviceProductText(device.model, device.product)}>
                      Product: {deviceProductText(device.model, device.product)}
                    </div>
                  </TableCell>
                    <TableCell className="px-3 py-1.5 font-mono text-xs text-muted-foreground whitespace-nowrap">
                      {device.build_display_id || '-'}
                    </TableCell>
                    <TableCell className="px-3 py-1.5">
                      <div className="flex flex-wrap items-center gap-1">
                        <StatusBadge kind="device-ui" status={device.status} size="sm" />
                        {/* ADR-0057 D4 第 4 面（#2962 B）：退役是人工确认的终态，
                            与 status 正交（心跳不改写 status），必须独立可见。 */}
                        {device.retired_at && (
                          <span
                            data-testid={`device-retired-badge-${device.id}`}
                            className="shrink-0 whitespace-nowrap rounded-full bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
                            title={`已退役${device.retired_at ? `（${device.retired_at.slice(0, 10)}）` : ''}`}
                          >
                            已退役
                          </span>
                        )}
                        {/* ADR-0063 D7 / #3918：vbus 槽位 USB 在位（与 adb 状态并列；只有带槽位映射的主机才有） */}
                        {device.usb_presence && (
                          <span
                            data-testid={`device-usb-presence-${device.id}`}
                            className={cn(
                              'shrink-0 whitespace-nowrap rounded-full px-1.5 py-0.5 text-[11px]',
                              USB_PRESENCE_LABEL[device.usb_presence.state]?.className ?? 'bg-muted text-muted-foreground',
                              !device.usb_presence.host_online && 'opacity-60',
                            )}
                            title={usbPresenceTitle(device.usb_presence)}
                          >
                            {USB_PRESENCE_LABEL[device.usb_presence.state]?.text ?? 'USB ?'}
                          </span>
                        )}
                        {/* #2962 A：陈旧度派生徽标；退役建议（E4）只提示不动作 */}
                        {!device.retired_at && device.is_stale && (
                          <span
                            data-testid={`device-stale-badge-${device.id}`}
                            className="shrink-0 whitespace-nowrap rounded-full bg-warning/20 px-1.5 py-0.5 text-[11px] text-warning"
                            title="陈旧：OFFLINE 且超过 7 天未上报，已从默认列表/容量中隐藏"
                          >
                            陈旧
                          </span>
                        )}
                        {!device.retired_at && device.retire_suggested && (
                          <span
                            data-testid={`device-retire-suggested-${device.id}`}
                            className="shrink-0 whitespace-nowrap rounded-full bg-destructive/15 px-1.5 py-0.5 text-[11px] text-destructive"
                            title="超过 30 天未上报：建议人工确认后退役（只提示，不动作）"
                          >
                            建议退役
                          </span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="px-3 py-1.5">
                      {device.battery_level != null ? (
                        <div className="flex items-center gap-1.5" title="最近一次上报电量">
                          <Progress
                            value={device.battery_level}
                            className="h-2 w-10"
                            indicatorClassName={resourceUsageBgClass(100 - device.battery_level)}
                          />
                          <span className={cn(
                            'font-mono text-xs',
                            resourceUsageTextClass(100 - device.battery_level),
                          )}>
                            {device.battery_level.toFixed(0)}%
                          </span>
                        </div>
                      ) : (
                        <span className="text-muted-foreground/40">—</span>
                      )}
                    </TableCell>
                    <TableCell className="px-3 py-1.5">
                      {device.temperature != null ? (
                        <span
                          className={cn('font-mono text-xs', temperatureTextClass(device.temperature))}
                          title="最近一次上报温度"
                        >
                          {device.temperature.toFixed(1)}°C
                        </span>
                      ) : (
                        <span className="text-muted-foreground/40">—</span>
                      )}
                    </TableCell>
                    {hasDiskData && (
                      <TableCell className="px-3 py-1.5">
                        {disk ? (
                          <div
                            className="flex items-center gap-1.5"
                            title={`已用 ${formatBytes(disk.used)} / 共 ${formatBytes(disk.total)}（最近一次 df /data 上报）`}
                          >
                            <Progress
                              value={disk.pct}
                              className="h-2 w-10"
                              indicatorClassName={resourceUsageBgClass(disk.pct)}
                            />
                            <span className={cn('font-mono text-xs whitespace-nowrap', resourceUsageTextClass(disk.pct))}>
                              剩 {formatBytes(disk.free)}
                            </span>
                          </div>
                        ) : (
                          <span className="text-muted-foreground/40">—</span>
                        )}
                      </TableCell>
                    )}
                    {hasNetworkData && (
                      <TableCell className="px-3 py-1.5">
                        {device.network_latency != null ? (
                          <div
                            className={cn('flex items-center gap-1 font-mono text-xs', latencyTextClass(device.network_latency))}
                            title="最近一次上报网络延迟"
                          >
                            <Wifi className="h-3.5 w-3.5" />
                            {device.network_latency.toFixed(0)}ms
                          </div>
                        ) : (
                          <div className="flex items-center gap-1 text-xs text-muted-foreground/40">
                            <WifiOff className="h-3.5 w-3.5" />—
                          </div>
                        )}
                      </TableCell>
                    )}
                    <TableCell className="px-3 py-1.5">
                      {device.tags?.length ? (
                        <div className="flex max-w-[160px] items-center gap-1" title={device.tags.join(', ')}>
                          {device.tags.slice(0, 2).map((tag) => (
                            <span
                              key={tag}
                              title={tag}
                              className="max-w-[140px] truncate rounded-full bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
                            >
                              {tag}
                            </span>
                          ))}
                          {device.tags.length > 2 && (
                            <span className="shrink-0 text-[11px] text-muted-foreground">
                              +{device.tags.length - 2}
                            </span>
                          )}
                        </div>
                      ) : (
                        <span className="text-muted-foreground/40">—</span>
                      )}
                    </TableCell>
                    <TableCell className="max-w-[120px] truncate px-2.5 py-1.5 text-sm text-muted-foreground" title={device.host_name || undefined}>
                      {device.host_name && onNavigateToHost ? (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            onNavigateToHost(device.host_name!);
                          }}
                          className="block max-w-full truncate text-left text-primary hover:underline"
                          title={`跳转到主机页查看 ${device.host_name}`}
                        >
                          {device.host_name}
                        </button>
                      ) : (
                        device.host_name || '-'
                      )}
                    </TableCell>
                    <TableCell className="px-3 py-1.5 text-right text-xs text-muted-foreground whitespace-nowrap">
                      {device.last_seen
                        ? formatDateTimeFull(device.last_seen)
                        : '-'}
                    </TableCell>
                </ClickableRow>
              );
            })}
            {virtualPaddingBottom > 0 && (
              <TableRow aria-hidden="true" className="border-0 hover:bg-transparent">
                <TableCell
                  colSpan={visibleColumnCount}
                  className="border-0 p-0"
                  style={{ height: virtualPaddingBottom }}
                />
              </TableRow>
            )}
            {filteredDevices.length === 0 && (
              <TableEmptyRow colSpan={20}>未找到匹配条件的设备</TableEmptyRow>
            )}
          </TableBody>
        </Table>

        {/* Pagination */}
        {sortedDevices.length > pageSize && (
          <div className="p-3 border-t border-border flex items-center justify-between">
            <div className="text-xs text-muted-foreground">
              显示第 {(safeCurrentPage - 1) * pageSize + 1} - {Math.min(safeCurrentPage * pageSize, sortedDevices.length)} 条，
              共 {sortedDevices.length} 条设备
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => handlePageChange((p) => Math.max(1, p - 1))}
                disabled={safeCurrentPage === 1}
                aria-label="上一页"
                className="p-1.5 rounded-md border border-border hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <ChevronLeft className="w-4 h-4 text-muted-foreground" />
              </button>
              <span className="text-xs text-muted-foreground">
                {safeCurrentPage} / {totalPages}
              </span>
              <button
                onClick={() => handlePageChange((p) => Math.min(totalPages, p + 1))}
                disabled={safeCurrentPage === totalPages}
                aria-label="下一页"
                className="p-1.5 rounded-md border border-border hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <ChevronRight className="w-4 h-4 text-muted-foreground" />
              </button>
            </div>
          </div>
        )}

      </div>

      {/* 全选后底部悬浮批量条会压住分页行：用真实占位把最后一行顶出覆盖带（#2614，
          与主机页同一规格；不这样「下一页」的坐标点击会被条体吞掉或误触「取消选择」） */}
      {selectable && (selectedIds?.size ?? 0) > 0 && (
        <BulkBarSpacer testId="device-table-selection-spacer" />
      )}

      {/* 右侧标准设备详情抽屉（D2 / #465 / #4117 U2-1b） */}
      <DeviceDetailDrawer
        device={activeDrawerDevice}
        onClose={() => setDrawerDeviceId(null)}
        isAdmin={isAdmin}
        onEditDeviceTags={onEditDeviceTags}
        onNavigateToHost={onNavigateToHost}
        currentIndex={activeDrawerIndex >= 0 ? activeDrawerIndex : undefined}
        totalCount={sortedDevices.length}
        onPrev={
          activeDrawerIndex > 0
            ? () => setDrawerDeviceId(sortedDevices[activeDrawerIndex - 1].id)
            : undefined
        }
        onNext={
          activeDrawerIndex >= 0 && activeDrawerIndex < sortedDevices.length - 1
            ? () => setDrawerDeviceId(sortedDevices[activeDrawerIndex + 1].id)
            : undefined
        }
      />
    </div>
  );
}
