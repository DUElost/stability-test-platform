import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { cn } from '@/lib/utils';
import { BulkBarSpacer } from '@/components/ui/bulk-action-bar';
import { ClickableRow } from '@/components/ui/clickable-row';
import { Progress } from '@/components/ui/progress';
import {
  Table,
  TableBody,
  TableCell,
  TableEmptyRow,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { TooltipProvider } from '@/components/ui/tooltip';
import { StatusBadge } from '@/components/ui/status-badge';
import {
  FilterSearchInput,
  FilterWorkbench,
  MoreFiltersPopover,
  type ActiveFilterChip,
} from '@/components/ui/filter-bar';
import { SearchableSelect } from '@/components/ui/searchable-select';
import { resourceUsageBgClass, resourceUsageTextClass } from '@/design-system/tokens';
import {
  Activity,
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleSlash,
  MoreHorizontal,
  Pencil,
  RotateCcw,
  Server,
  ShieldAlert,
  Trash2,
} from 'lucide-react';
import { formatLocalTime, parseIsoToDate } from '@/utils/format';
import type { HostFacets, HostScriptPresence } from '@/utils/api/types';
import { HostDetailDrawer, type HostPresenceLoad } from '@/components/host/HostDetailDrawer';

export interface HostResources {
  cpu_load: number;
  cpu_cores?: number;
  ram_usage: number;
  ram_total_gb?: number;
  disk_usage: number | null;
  disk_total_gb?: number;
  temperature?: number;
  uptime_seconds?: number;
}

export interface MountStatus {
  path: string;
  mounted: boolean;
  available_gb?: number;
  total_gb?: number;
}

export type AgentCodeSyncStatus = 'unknown' | 'matched' | 'drift' | 'pending';

export type HostHealthFilter = 'all' | 'HEALTHY' | 'DEGRADED' | 'UNSCHEDULABLE' | 'unhealthy';
export type HostSyncFilter =
  | 'all'
  | 'upgrade_pending'
  | 'drift'
  | 'matched'
  | 'mount_error'
  | 'usb_fault';

export type HostSortKey = 'default' | 'name' | 'devices' | 'cpu' | 'ram' | 'disk' | 'heartbeat';
export type HostSortDirection = 'asc' | 'desc';
type SortDirection = HostSortDirection;

export interface HostTableData {
  id: string | number;
  name: string;
  ip: string;
  status: 'ONLINE' | 'OFFLINE' | 'DEGRADED';
  watcher_admin_active?: boolean;
  last_heartbeat?: string;
  /** ADR-0038 D1/D4：退役生命周期（retired_at 非空即退役；与 status 正交） */
  retired_at?: string | null;
  retired_by?: string | null;
  retire_reason?: string | null;
  /** ADR-0038 v0.3 D9：设备面意图（空置 / 人工清空）——非空即豁免中 */
  emptied_at?: string | null;
  emptied_by?: string | null;
  emptied_reason?: string | null;
  /** 与 status 正交：曾安装成功 / 有过心跳 */
  agent_installed?: boolean;
  agent_protocol_version?: string | null;
  agent_code_revision?: string | null;
  /** ADR-0040 v1.1：drift/matched 的唯一判据源（远端上报的 code digest） */
  agent_artifact_digest?: string | null;
  expected_code_revision?: string | null;
  agent_code_deployed?: string | null;
  agent_code_deployed_at?: string | null;
  agent_code_sync_status?: AgentCodeSyncStatus;
  resources?: HostResources;
  mount_status?: MountStatus[];
  mount_anomaly?: boolean;
  mount_count?: number;
  mount_failed_count?: number;
  device_count?: number;
  /** 台账在册（未退役）设备数；0 表示无在册设备（纯空置主机），null/undefined 表示未下发 */
  ledger_device_count?: number | null;
  /** 台账在册设备最近活跃时间（用于按 72h 窗口区分长期空置与近期掉光） */
  ledger_latest_seen_at?: string | null;
  /**
   * lsusb 枚举到的疑似 Android 设备数（物理 USB 侧，与 device_count 的 adb 口径
   * 并排对照）。null / undefined = 未采集到，渲染为「—」而非 0。
   */
  usb_device_count?: number | null;
  /** sysfs 暴露 ADB 接口（ff:42）的设备数（L2/L4 分诊） */
  adb_interface_count?: number | null;
  /** adb devices 按 state 分桶计数（L3 分诊） */
  adb_state_counts?: {
    device?: number;
    offline?: number;
    unauthorized?: number;
    other?: number;
  } | null;
  /** 内核 USB 日志通道可读性 */
  usb_kernel_log?: 'ok' | 'unavailable' | 'unknown' | null;
  /** S1：零权限 sysfs 级联 Hub 拓扑与半枚举 Hub 计数 */
  usb_hub_topology?: {
    cascade_hubs: number;
    unbound_hubs: number;
    unbound_idle_bus_hubs: number;
  } | null;
  /** S1：最近 1 小时滑动窗内的内核 USB 错误数值计数 */
  usb_kernel_errors_1h?: {
    link_errors: number;
    cable_suspect: number;
    disconnects: number;
    resets: number;
    enum_errors: number;
    hub_port_errors: number;
  } | null;
  /** 零权限 sysfs USB 主控与端口硬件异常体检（不依赖内核日志权限，重启不洗白） */
  usb_controller_health?: {
    unbound_xhci_controllers: number;
    disabled_root_ports: number;
    disabled_hub_ports: number;
    over_current_ports: number;
    stuck_default_ports: number;
  } | null;
  /** VBUS 槽位映射（hub:port → serial），供抽屉标注断电端口对应设备 */
  usb_power_slot_serials?: Record<string, string> | null;
  /** #2983：控制面 SSH 拓扑对账探针摘要 */
  health_probe?: {
    checked_at?: string;
    verdict?: string;
    signals?: string[];
    topology?: string;
    recent_verdicts?: string[];
    strike_open?: boolean;
    strike_need?: number;
    uhubctl_status?: string;
    powered_off_ports?: string[];
  } | null;
  /** #2972 / S4：Agent 侧 USB/ADB 自愈与持久化熔断状态 */
  self_heal?: {
    state?: string;
    fuse_open?: boolean;
    fuse_reason?: string | null;
    xhci?: {
      state?: string;
      fuse_open?: boolean;
      fuse_reason?: string | null;
      boot_attempts?: number;
      window_attempts?: number;
      episode_failures?: number;
      last_action_at?: string | null;
      last_marker?: string | null;
    };
    adb_reconnect?: {
      state?: string;
      fuse_open?: boolean;
      fuse_reason?: string | null;
      boot_attempts?: number;
      window_attempts?: number;
      episode_failures?: number;
      last_action_at?: string | null;
      last_marker?: string | null;
    };
  } | null;
  /** Tooltip: adb/lease exclusions from device list (frontend-derived). */
  claim_hint?: string | null;
  active_tasks?: number;
  health_status?: 'HEALTHY' | 'DEGRADED' | 'UNSCHEDULABLE';
  health_reasons?: string[];
}

interface ExpandableHostTableProps {
  hosts: HostTableData[];
  onHotUpdate?: (hostId: string | number) => void;
  isHotUpdating?: (hostId: string | number) => boolean;
  onInstall?: (hostId: string | number) => void;
  isInstalling?: (hostId: string | number) => boolean;
  onFlashPrereqs?: (hostId: string | number) => void;
  isFlashPrereqs?: (hostId: string | number) => boolean;
  onEdit?: (host: HostTableData) => void;
  onDelete?: (host: HostTableData) => void;
  /** ADR-0038 D2：退役 / 解除退役（admin；原因由调用方收集）。 */
  onRetire?: (host: HostTableData) => void;
  onUnretire?: (host: HostTableData) => void;
  isDeleting?: (hostId: string | number) => boolean;
  isAdmin?: boolean;
  onWatcherAdminStateChange?: (hostId: string | number, nextActive: boolean) => void;
  isWatcherAdminStateUpdating?: (hostId: string | number) => boolean;
  canManageWatcherAdminState?: boolean;
  selectedIds?: Set<string | number>;
  onSelectionChange?: (ids: Set<string | number>) => void;
  /** 打开主机详情抽屉时按需拉该机脚本在位矩阵（缺省则抽屉内不出现「脚本在位」区块）。 */
  onLoadHostScriptPresence?: (hostId: string | number) => Promise<HostScriptPresence>;
  /** 单机按需重核；成功后组件会重新拉一次该机矩阵。 */
  onRefreshHostScriptPresence?: (hostId: string | number) => Promise<unknown>;
  /** 可选受控筛选状态（由页面同步到 URL）。 */
  statusFilter?: 'all' | HostTableData['status'];
  onStatusFilterChange?: (value: 'all' | HostTableData['status']) => void;
  searchQuery?: string;
  onSearchQueryChange?: (value: string) => void;
  healthFilter?: HostHealthFilter;
  onHealthFilterChange?: (value: HostHealthFilter) => void;
  syncFilter?: HostSyncFilter;
  onSyncFilterChange?: (value: HostSyncFilter) => void;
  /** 受控详情抽屉主机 ID（支持 URL `?drawer=` 深链持久化） */
  activeDrawerHostId?: string | null;
  onActiveDrawerHostIdChange?: (hostId: string | null) => void;
  /** ADR-0038 v0.3 D9：快捷标记/清除「设备面已处置（emptied）」 */
  onSetDeviceIntent?: (host: HostTableData) => void;
  onClearDeviceIntent?: (host: HostTableData) => void;
  isDeviceIntentUpdating?: (hostId: string | number) => boolean;
  showRetired?: boolean;
  onShowRetiredChange?: (value: boolean) => void;
  onResetAllFilters?: () => void;
  onClearQuickView?: () => void;
  onNavigateToDevices?: (hostId: string | number) => void;
  onFilteredHostsChange?: (hosts: HostTableData[]) => void;
  extraHeaderRight?: ReactNode;
  /** B3-2：服务端分页模式（关闭前端全集过滤/排序，消费独立 10 项 Facets 与分页条） */
  serverMode?: boolean;
  serverFacets?: HostFacets;
  serverFilteredTotal?: number;
  page?: number;
  pageSize?: number;
  pageSizeOptions?: readonly number[];
  onPageChange?: (page: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
  sortKey?: HostSortKey;
  sortDirection?: HostSortDirection;
  onSortChange?: (key: HostSortKey, dir: HostSortDirection) => void;
  /** 跨页 `?drawer=<id>` 深链独立加载出的主机详情 */
  drawerHostOverride?: HostTableData | null;
  isFetchingPage?: boolean;
}

function getResourceColor(percentage: number): string {
  return resourceUsageTextClass(percentage);
}

function getProgressColor(percentage: number): string {
  return resourceUsageBgClass(percentage);
}

function formatUsagePercent(value: number | null | undefined, digits = 0): string {
  if (value == null || !Number.isFinite(value)) return '未知';
  return `${value.toFixed(digits)}%`;
}

const REASON_LABELS: Record<string, string> = {
  cpu_high: 'CPU 过高',
  ram_high: '内存过高',
  disk_high: '磁盘过高',
  disk_unknown: '磁盘未知',
  mount_failed: '挂载失败',
  adb_low_healthy_devices: '无健康设备',
  usb_tree_empty: 'USB 总线空树（主控/供电异常）',
  adb_interfaces_missing: 'USB 有设备但无 ADB 接口',
  adb_multiple_servers: 'ADB 多 server 冲突',
  usb_host_controller_dead: 'USB 主控失联（xHCI 死亡）',
  usb_link_degraded: 'USB 链路劣化',
  single_instance_guard_degraded: '单实例守卫降级（双实例风险）',
};

const USB_KERNEL_ERROR_LABELS: Record<string, string> = {
  link_errors: '-71/-110 传输错',
  hub_port_errors: 'Hub 端口错',
  disconnects: 'USB 断连',
  resets: '总线复位',
  enum_errors: '-32/-22 枚举错',
  cable_suspect: '线缆异常',
};

const PROBE_TOPOLOGY_LABELS: Record<string, string> = {
  ok: '正常',
  hub_present_phones_zero: '级联 Hub 在树但无终端',
  root_hub_only: '仅 root hub（空柜）',
  unknown: '未知',
};

const PROBE_VERDICT_LABELS: Record<string, string> = {
  aligned: '一致',
  agent_mute: 'Agent 漏报',
  probe_quiet: '探针未复现',
  topology_mismatch: '拓扑不一致',
  probe_error: '探针采集失败',
};

interface InlineFaultChip {
  key: string;
  label: string;
  tone: 'danger' | 'warning' | 'info' | 'muted';
  title?: string;
}

const EMPTY_TREE_RECENT_LEDGER_MS = 72 * 60 * 60 * 1000;

function hasRecentLedgerActivity(host: HostTableData): boolean {
  if (host.ledger_device_count === 0) return false;
  if (host.ledger_device_count == null || !host.ledger_latest_seen_at) return true;
  const seen = parseIsoToDate(host.ledger_latest_seen_at);
  if (!seen) return true;
  return Date.now() - seen.getTime() <= EMPTY_TREE_RECENT_LEDGER_MS;
}

/** 计算主机 USB/Hub/对账故障优先级（3=严重需人工 2=劣化/未豁免空树 1=待办/VBUS 断电/闲置半枚举 0=正常）。 */
function hostUsbFaultPriority(host: HostTableData): number {
  if (host.retired_at) return 0;
  const reasons = new Set(host.health_reasons ?? []);
  const topo = host.usb_hub_topology;
  const activeUnboundHubs = topo
    ? Math.max(0, topo.unbound_hubs - topo.unbound_idle_bus_hubs)
    : 0;
  const ctrl = host.usb_controller_health;
  const unboundXhci = ctrl?.unbound_xhci_controllers ?? 0;
  const disabledRootPorts = ctrl?.disabled_root_ports ?? 0;
  const disabledHubPorts = ctrl?.disabled_hub_ports ?? 0;
  const overCurrentPorts = ctrl?.over_current_ports ?? 0;
  const stuckDefaultPorts = ctrl?.stuck_default_ports ?? 0;
  const poweredOffCount = host.health_probe?.powered_off_ports?.length ?? 0;
  if (
    reasons.has('usb_host_controller_dead') ||
    host.health_probe?.strike_open === true ||
    host.self_heal?.fuse_open === true ||
    activeUnboundHubs > 0 ||
    unboundXhci > 0 ||
    disabledRootPorts > 0 ||
    overCurrentPorts > 0
  ) {
    return 3;
  }
  const hasUnemptiedLedgerEmptyTree =
    reasons.has('usb_tree_empty') &&
    !host.emptied_at &&
    hasRecentLedgerActivity(host);
  if (
    reasons.has('usb_link_degraded') ||
    hasUnemptiedLedgerEmptyTree ||
    reasons.has('adb_multiple_servers') ||
    disabledHubPorts > 0
  ) {
    return 2;
  }
  if (
    reasons.has('adb_interfaces_missing') ||
    (topo != null && topo.unbound_hubs > 0) ||
    stuckDefaultPorts > 0 ||
    poweredOffCount > 0
  ) {
    return 1;
  }
  return 0;
}

/** 主行外显的紧凑故障芯片（常态静音，仅故障主机外显 ≤2 个芯片）。 */
function buildInlineFaultChips(host: HostTableData): InlineFaultChip[] {
  const chips: InlineFaultChip[] = [];
  const ctrl = host.usb_controller_health;
  if (ctrl) {
    if (ctrl.unbound_xhci_controllers > 0) {
      chips.push({
        key: 'unbound_xhci',
        label: 'xHCI 驱动失绑',
        tone: 'danger',
        title: `PCI xHCI 主控未绑定驱动（${ctrl.unbound_xhci_controllers} 个），需排查 probe -110 或重启主机`,
      });
    }
    if (ctrl.disabled_root_ports > 0) {
      chips.push({
        key: 'disabled_root_ports',
        label: `主控端口禁用(${ctrl.disabled_root_ports})`,
        tone: 'danger',
        title: `Root Hub 有 ${ctrl.disabled_root_ports} 个端口处于 disable=1（常见于 xHCI 死亡后的永久失明态）`,
      });
    }
    if (ctrl.over_current_ports > 0) {
      chips.push({
        key: 'over_current_ports',
        label: `USB 过流(${ctrl.over_current_ports})`,
        tone: 'danger',
        title: `检测到 ${ctrl.over_current_ports} 个 USB 端口存在过流计数（over_current_count > 0）`,
      });
    }
    if (ctrl.disabled_hub_ports > 0) {
      chips.push({
        key: 'disabled_hub_ports',
        label: `Hub 端口禁用(${ctrl.disabled_hub_ports})`,
        tone: 'warning',
        title: `外接级联 Hub 有 ${ctrl.disabled_hub_ports} 个端口处于内核 sysfs disable=1（OS 层端口禁用，与 uhubctl VBUS 断电独立）`,
      });
    }
    if (ctrl.stuck_default_ports > 0) {
      chips.push({
        key: 'stuck_default_ports',
        label: `端口卡死(${ctrl.stuck_default_ports})`,
        tone: 'warning',
        title: `外接级联 Hub 有 ${ctrl.stuck_default_ports} 个端口停在 state=default 且无设备节点（疑似坏线/无法枚举）`,
      });
    }
  }
  const poweredOffPorts = host.health_probe?.powered_off_ports ?? [];
  if (poweredOffPorts.length > 0) {
    chips.push({
      key: 'vbus_powered_off',
      label: `VBUS 断电(${poweredOffPorts.length})`,
      tone: 'info',
      title: `uhubctl 检测到 ${poweredOffPorts.length} 个 Hub 端口处于 0000 off 供电关闭态（${poweredOffPorts.join(', ')}），非端口硬件损坏，可通过 uhubctl -a on 恢复`,
    });
  }
  const reasons = host.health_reasons ?? [];
  for (const reason of reasons) {
    const label = REASON_LABELS[reason] || reason;
    if (reason === 'usb_host_controller_dead') {
      chips.push({ key: reason, label: 'USB 主控失联', tone: 'danger', title: label });
    } else if (reason === 'usb_link_degraded') {
      chips.push({ key: reason, label: 'USB 链路劣化', tone: 'warning', title: label });
    } else if (reason === 'usb_tree_empty') {
      const zeroLedger = !host.emptied_at && host.ledger_device_count === 0;
      const staleLedger = !host.emptied_at && !zeroLedger && !hasRecentLedgerActivity(host);
      chips.push({
        key: reason,
        label: host.emptied_at
          ? 'USB 空树(已处置)'
          : zeroLedger
            ? '未接设备'
            : staleLedger
              ? '长期空置'
              : 'USB 空树',
        tone: host.emptied_at || zeroLedger || staleLedger ? 'muted' : 'danger',
        title: zeroLedger
          ? 'USB 总线空树且台账无在册设备（空置/未接线，不计入 USB/Hub 异常）'
          : staleLedger
            ? `台账在册 ${host.ledger_device_count} 台设备均已离线超 72 小时且主控硬件正常（自动豁免 USB/Hub 异常）`
            : label,
      });
    } else if (reason === 'adb_interfaces_missing') {
      chips.push({ key: reason, label: '无 ADB 接口', tone: 'warning', title: label });
    } else if (reason === 'adb_multiple_servers') {
      chips.push({ key: reason, label: 'ADB 多 server', tone: 'warning', title: label });
    } else if (
      reason === 'mount_failed' ||
      reason === 'disk_high' ||
      reason === 'cpu_high' ||
      reason === 'ram_high' ||
      reason === 'adb_low_healthy_devices' ||
      reason === 'single_instance_guard_degraded'
    ) {
      chips.push({
        key: reason,
        label,
        tone: reason === 'mount_failed' || reason === 'disk_high' ? 'danger' : 'warning',
        title: label,
      });
    }
  }
  const topo = host.usb_hub_topology;
  if (topo && topo.unbound_hubs > 0) {
    const idleOnly = topo.unbound_idle_bus_hubs >= topo.unbound_hubs;
    chips.push({
      key: 'unbound_hubs',
      label: idleOnly
        ? `SS Hub 半枚举(${topo.unbound_hubs})`
        : `在用 Hub 半枚举(${topo.unbound_hubs})`,
      tone: idleOnly ? 'warning' : 'danger',
      title: idleOnly
        ? '未绑定驱动的 Hub 落在无在用设备总线（SuperSpeed 退化分支）'
        : '存在未绑定驱动的 Hub 落在承载在用设备的总线上，需现场核查',
    });
  }
  if (host.health_probe?.strike_open) {
    chips.push({
      key: 'probe_strike',
      label: '探针对账异常',
      tone: 'danger',
      title: `控制面 SSH 探针连续异常：${PROBE_VERDICT_LABELS[host.health_probe.verdict ?? ''] ?? host.health_probe.verdict ?? '未知'}`,
    });
  }
  if (host.self_heal?.fuse_open) {
    const isHw = host.self_heal.state === 'hardware_suspect';
    chips.push({
      key: 'self_heal_fuse',
      label: isHw ? '自愈熔断(疑似硬件)' : '自愈熔断(需人工)',
      tone: 'danger',
      title: `USB/ADB 自动恢复已熔断（${host.self_heal.fuse_reason ?? host.self_heal.state ?? 'unknown'}）`,
    });
  }
  return chips;
}

const AGENT_SYNC_LABELS: Record<AgentCodeSyncStatus, string> = {
  matched: '已对齐',
  pending: '待上报',
  drift: '内容漂移',
  unknown: '未知',
};

function formatAgentVersionLabel(host: HostTableData): string {
  const protocol = host.agent_protocol_version;
  const revision = host.agent_code_revision ?? host.agent_code_deployed;
  if (protocol && revision) return `${protocol} @${revision}`;
  if (protocol) return protocol;
  if (revision) return `@${revision}`;
  return '—';
}

function agentSyncBadgeClass(status: AgentCodeSyncStatus | undefined): string {
  switch (status) {
    case 'matched':
      return 'bg-success/10 text-success';
    case 'pending':
      return 'bg-info/10 text-info';
    case 'drift':
      return 'bg-destructive/10 text-destructive-text';
    default:
      return 'bg-muted/50 text-muted-foreground';
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function formatHeartbeatLabel(value?: string): string {
  if (!value) return '—';
  const date = parseIsoToDate(value);
  if (!date) return formatLocalTime(value);
  const elapsedSeconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (elapsedSeconds < 60) return '刚刚';
  if (elapsedSeconds < 3600) return `${Math.floor(elapsedSeconds / 60)} 分钟前`;
  if (elapsedSeconds < 86400) return `${Math.floor(elapsedSeconds / 3600)} 小时前`;
  return `${Math.floor(elapsedSeconds / 86400)} 天前`;
}

function SortIcon({ active, direction }: { active: boolean; direction: SortDirection }) {
  if (!active) return <ArrowUpDown className="h-3 w-3 text-muted-foreground/60" aria-hidden="true" />;
  return direction === 'asc' ? (
    <ArrowUp className="h-3 w-3 text-primary" aria-hidden="true" />
  ) : (
    <ArrowDown className="h-3 w-3 text-primary" aria-hidden="true" />
  );
}

export function ExpandableHostTable({
  hosts,
  onHotUpdate,
  isHotUpdating,
  onInstall,
  isInstalling,
  onFlashPrereqs,
  isFlashPrereqs,
  onEdit,
  onDelete,
  onRetire,
  onUnretire,
  isDeleting,
  isAdmin,
  onWatcherAdminStateChange,
  isWatcherAdminStateUpdating,
  canManageWatcherAdminState = false,
  selectedIds,
  onSelectionChange,
  onLoadHostScriptPresence,
  onRefreshHostScriptPresence,
  statusFilter: controlledStatusFilter,
  onStatusFilterChange,
  searchQuery: controlledSearchQuery,
  onSearchQueryChange,
  healthFilter: controlledHealthFilter,
  onHealthFilterChange,
  syncFilter: controlledSyncFilter,
  onSyncFilterChange,
  activeDrawerHostId: controlledDrawerHostId,
  onActiveDrawerHostIdChange,
  onSetDeviceIntent,
  onClearDeviceIntent,
  isDeviceIntentUpdating,
  showRetired,
  onShowRetiredChange,
  onResetAllFilters,
  onClearQuickView,
  onNavigateToDevices,
  onFilteredHostsChange,
  extraHeaderRight,
  serverMode = false,
  serverFacets,
  serverFilteredTotal,
  page = 1,
  pageSize = 50,
  pageSizeOptions = [50, 100, 200],
  onPageChange,
  onPageSizeChange,
  sortKey: controlledSortKey,
  sortDirection: controlledSortDirection,
  onSortChange,
  drawerHostOverride,
  isFetchingPage = false,
}: ExpandableHostTableProps) {
  const [localStatusFilter, setLocalStatusFilter] = useState<'all' | HostTableData['status']>('all');
  const [localSearchQuery, setLocalSearchQuery] = useState('');
  const [localHealthFilter, setLocalHealthFilter] = useState<HostHealthFilter>('all');
  const [localSyncFilter, setLocalSyncFilter] = useState<HostSyncFilter>('all');
  const [localDrawerHostId, setLocalDrawerHostId] = useState<string | null>(null);
  const [localSortKey, setLocalSortKey] = useState<HostSortKey>('default');
  const [localSortDirection, setLocalSortDirection] = useState<SortDirection>('desc');

  const statusFilter = controlledStatusFilter ?? localStatusFilter;
  const setStatusFilter = onStatusFilterChange ?? setLocalStatusFilter;
  const searchQuery = controlledSearchQuery ?? localSearchQuery;
  const setSearchQuery = onSearchQueryChange ?? setLocalSearchQuery;
  const healthFilter = controlledHealthFilter ?? localHealthFilter;
  const setHealthFilter = onHealthFilterChange ?? setLocalHealthFilter;
  const syncFilter = controlledSyncFilter ?? localSyncFilter;
  const setSyncFilter = onSyncFilterChange ?? setLocalSyncFilter;
  const sortKey = controlledSortKey ?? localSortKey;
  const sortDirection = controlledSortDirection ?? localSortDirection;
  const activeDrawerHostId =
    controlledDrawerHostId !== undefined ? controlledDrawerHostId : localDrawerHostId;
  const setActiveDrawerHostId = useCallback(
    (next: string | null) => {
      if (onActiveDrawerHostIdChange) {
        onActiveDrawerHostIdChange(next);
      } else {
        setLocalDrawerHostId(next);
      }
    },
    [onActiveDrawerHostIdChange],
  );

  const selectable = !!onSelectionChange;
  const selectAllRef = useRef<HTMLInputElement>(null);
  const tableContainerRef = useRef<HTMLDivElement>(null);

  // 逐台脚本在位按需拉取（仅在右侧抽屉打开对应主机时请求）
  const [presenceByHost, setPresenceByHost] = useState<Record<string, HostPresenceLoad>>({});
  const [presenceRefreshing, setPresenceRefreshing] = useState<Set<string | number>>(new Set());
  const presenceInFlight = useRef<Set<string>>(new Set());

  const loadHostPresence = useCallback(
    async (hostId: string | number, force = false) => {
      if (!onLoadHostScriptPresence) return;
      const key = String(hostId);
      if (!force && presenceInFlight.current.has(key)) return;
      presenceInFlight.current.add(key);
      setPresenceByHost((prev) => ({
        ...prev,
        [key]: { ...prev[key], status: 'loading', error: undefined },
      }));
      try {
        const data = await onLoadHostScriptPresence(hostId);
        setPresenceByHost((prev) => ({ ...prev, [key]: { status: 'ready', data } }));
      } catch (error) {
        setPresenceByHost((prev) => ({
          ...prev,
          [key]: { ...prev[key], status: 'error', error: errorMessage(error) },
        }));
      } finally {
        presenceInFlight.current.delete(key);
      }
    },
    [onLoadHostScriptPresence],
  );

  const refreshHostPresence = useCallback(
    async (hostId: string | number) => {
      if (!onRefreshHostScriptPresence) return;
      setPresenceRefreshing((prev) => new Set(prev).add(hostId));
      try {
        await onRefreshHostScriptPresence(hostId);
        await loadHostPresence(hostId, true);
      } catch (error) {
        setPresenceByHost((prev) => ({
          ...prev,
          [String(hostId)]: {
            ...prev[String(hostId)],
            status: 'error',
            error: errorMessage(error),
          },
        }));
      } finally {
        setPresenceRefreshing((prev) => {
          const next = new Set(prev);
          next.delete(hostId);
          return next;
        });
      }
    },
    [loadHostPresence, onRefreshHostScriptPresence],
  );

  const hasMountIssue = (host: HostTableData) =>
    Boolean(host.mount_anomaly) || Boolean(host.mount_status?.some((m) => !m.mounted));

  const filteredHosts = useMemo(() => {
    if (serverMode) {
      return hosts;
    }
    const q = searchQuery.trim().toLowerCase();
    const tokens = q ? q.split(/\s+/).filter(Boolean) : [];
    const matched = hosts.filter((host) => {
      if (statusFilter !== 'all' && host.status !== statusFilter) return false;
      if (healthFilter !== 'all') {
        const effectiveHealth = host.health_status ?? 'HEALTHY';
        if (healthFilter === 'unhealthy') {
          if (effectiveHealth !== 'DEGRADED' && effectiveHealth !== 'UNSCHEDULABLE') return false;
        } else if (effectiveHealth !== healthFilter) {
          return false;
        }
      }
      if (
        syncFilter === 'upgrade_pending' &&
        !(host.status === 'ONLINE' && Boolean(host.agent_installed) && host.agent_code_sync_status === 'drift')
      ) {
        return false;
      }
      if (syncFilter === 'drift' && host.agent_code_sync_status !== 'drift') return false;
      if (syncFilter === 'matched' && host.agent_code_sync_status !== 'matched') return false;
      if (syncFilter === 'mount_error' && !hasMountIssue(host)) return false;
      if (syncFilter === 'usb_fault' && hostUsbFaultPriority(host) === 0) return false;
      if (tokens.length > 0) {
        const reasonText = (host.health_reasons ?? [])
          .map((r) => `${r} ${REASON_LABELS[r] || ''}`)
          .join(' ');
        const haystack = [
          String(host.id),
          host.name,
          host.ip,
          host.agent_code_revision,
          host.agent_code_deployed,
          host.agent_protocol_version,
          host.status,
          reasonText,
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase();
        if (!tokens.every((t) => haystack.includes(t))) return false;
      }
      return true;
    });

    if (sortKey === 'default') {
      // 默认按严重 USB/Hub/探针异常自动置顶，同档保持原始相对顺序（ES2019 稳定排序）
      return [...matched].sort((a, b) => hostUsbFaultPriority(b) - hostUsbFaultPriority(a));
    }

    const dir = sortDirection === 'asc' ? 1 : -1;
    return [...matched].sort((a, b) => {
      let cmp = 0;
      if (sortKey === 'name') {
        cmp = (a.name || a.ip).localeCompare(b.name || b.ip, 'zh-CN');
      } else if (sortKey === 'devices') {
        cmp = (a.device_count ?? 0) - (b.device_count ?? 0);
      } else if (sortKey === 'cpu') {
        cmp = (a.resources?.cpu_load ?? -1) - (b.resources?.cpu_load ?? -1);
      } else if (sortKey === 'ram') {
        cmp = (a.resources?.ram_usage ?? -1) - (b.resources?.ram_usage ?? -1);
      } else if (sortKey === 'disk') {
        cmp = (a.resources?.disk_usage ?? -1) - (b.resources?.disk_usage ?? -1);
      } else if (sortKey === 'heartbeat') {
        const ta = a.last_heartbeat ? (parseIsoToDate(a.last_heartbeat)?.getTime() ?? 0) : 0;
        const tb = b.last_heartbeat ? (parseIsoToDate(b.last_heartbeat)?.getTime() ?? 0) : 0;
        cmp = ta - tb;
      }
      return cmp * dir;
    });
  }, [serverMode, hosts, statusFilter, healthFilter, syncFilter, searchQuery, sortKey, sortDirection]);

  const handleSort = (nextKey: Exclude<HostSortKey, 'default'>) => {
    let resolvedKey: HostSortKey;
    let resolvedDir: SortDirection;
    if (sortKey !== nextKey) {
      resolvedKey = nextKey;
      resolvedDir = nextKey === 'name' ? 'asc' : 'desc';
    } else if (sortDirection === 'desc') {
      resolvedKey = nextKey;
      resolvedDir = 'asc';
    } else {
      resolvedKey = 'default';
      resolvedDir = 'desc';
    }
    if (onSortChange) {
      onSortChange(resolvedKey, resolvedDir);
    } else {
      setLocalSortKey(resolvedKey);
      setLocalSortDirection(resolvedDir);
    }
  };

  // 右侧详情抽屉当前主机 & 上下切换索引（优先采用按需加载的完整详情，未就绪时用当前页行瞬时占位）
  const activeDrawerHost = useMemo(() => {
    if (!activeDrawerHostId) return null;
    if (drawerHostOverride && String(drawerHostOverride.id) === activeDrawerHostId) {
      return drawerHostOverride;
    }
    const matched =
      filteredHosts.find((h) => String(h.id) === activeDrawerHostId) ??
      hosts.find((h) => String(h.id) === activeDrawerHostId);
    if (matched) return matched;
    return null;
  }, [activeDrawerHostId, filteredHosts, hosts, drawerHostOverride]);

  const activeDrawerIndex = useMemo(() => {
    if (!activeDrawerHost) return -1;
    return filteredHosts.findIndex((h) => String(h.id) === String(activeDrawerHost.id));
  }, [activeDrawerHost, filteredHosts]);

  // 当抽屉打开到某台主机且未加载过脚本在位时，按需触发一次加载
  useEffect(() => {
    if (!activeDrawerHost || !onLoadHostScriptPresence) return;
    const key = String(activeDrawerHost.id);
    if (presenceByHost[key] === undefined) {
      void loadHostPresence(activeDrawerHost.id);
    }
  }, [activeDrawerHost, loadHostPresence, onLoadHostScriptPresence, presenceByHost]);

  const openDrawer = (hostId: string | number) => {
    setActiveDrawerHostId(String(hostId));
  };

  // 虚拟化滚动（D5 Phase 1）
  const rowVirtualizer = useVirtualizer({
    count: filteredHosts.length,
    getScrollElement: () =>
      tableContainerRef.current?.querySelector<HTMLDivElement>('[data-slot="table-scroll-container"]') ??
      null,
    estimateSize: () => 56,
    overscan: 8,
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
  const visibleColumnCount = (selectable ? 1 : 0) + 10;

  const filteredIds = useMemo(() => filteredHosts.map((host) => host.id), [filteredHosts]);

  useEffect(() => {
    onFilteredHostsChange?.(filteredHosts);
  }, [filteredHosts, onFilteredHostsChange]);

  const selectedFilteredCount = filteredIds.filter((id) => selectedIds?.has(id)).length;
  const allFilteredSelected = filteredIds.length > 0 && selectedFilteredCount === filteredIds.length;
  const someFilteredSelected = selectedFilteredCount > 0 && !allFilteredSelected;

  useEffect(() => {
    if (!selectAllRef.current) return;
    selectAllRef.current.indeterminate = someFilteredSelected;
  }, [someFilteredSelected]);

  const toggleSelect = (id: string | number, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!onSelectionChange || !selectedIds) return;
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onSelectionChange(next);
  };

  const toggleAll = () => {
    if (!onSelectionChange || !selectedIds) return;
    if (allFilteredSelected) {
      const next = new Set(selectedIds);
      filteredIds.forEach((id) => next.delete(id));
      onSelectionChange(next);
    } else {
      const next = new Set(selectedIds);
      filteredIds.forEach((id) => next.add(id));
      onSelectionChange(next);
    }
  };

  const stats = useMemo(() => {
    if (serverMode && serverFacets) {
      return {
        total: serverFacets.all,
        online: serverFacets.online,
        offline: serverFacets.offline,
        degraded: serverFacets.degraded,
        agentAligned: serverFacets.aligned_agent_count,
        agentDrift: serverFacets.upgrade_pending,
        agentTrackable: serverFacets.trackable_agent_count,
        mountErrorCount: serverFacets.mount_anomaly,
        usbFaultCount: serverFacets.usb_anomaly,
        unhealthyCount: serverFacets.health_anomaly,
      };
    }
    const onlineHosts = hosts.filter((h) => h.status === 'ONLINE' && h.agent_installed);
    const aligned = onlineHosts.filter((h) => h.agent_code_sync_status === 'matched').length;
    return {
      total: hosts.length,
      online: hosts.filter((h) => h.status === 'ONLINE').length,
      offline: hosts.filter((h) => h.status === 'OFFLINE').length,
      degraded: hosts.filter((h) => h.status === 'DEGRADED').length,
      agentAligned: aligned,
      // #2366：把「落后」单独报出来——只报「已对齐 N/M」时，全队落后一版会读成「口径坏了」。
      agentDrift: onlineHosts.filter((h) => h.agent_code_sync_status === 'drift').length,
      agentTrackable: onlineHosts.length,
      mountErrorCount: hosts.filter((h) => h.mount_status?.some((m) => !m.mounted)).length,
      usbFaultCount: hosts.filter((h) => hostUsbFaultPriority(h) > 0).length,
      unhealthyCount: hosts.filter(
        (h) => h.health_status === 'DEGRADED' || h.health_status === 'UNSCHEDULABLE',
      ).length,
    };
  }, [serverMode, serverFacets, hosts]);

  // 只为「更多筛选」内部专属且未在顶层直接展示的条件渲染 Chip
  const hiddenChips = useMemo<ActiveFilterChip[]>(() => {
    const chips: ActiveFilterChip[] = [];
    if (syncFilter === 'matched') {
      chips.push({
        key: 'sync',
        label: 'Agent 同步',
        value: '已对齐',
        onRemove: () => setSyncFilter('all'),
      });
    } else if (syncFilter === 'drift') {
      chips.push({
        key: 'sync',
        label: 'Agent 同步',
        value: '内容漂移 (全部)',
        onRemove: () => setSyncFilter('all'),
      });
    }
    if (showRetired && onShowRetiredChange) {
      chips.push({
        key: 'retired',
        label: '范围',
        value: '含已退役',
        onRemove: () => onShowRetiredChange(false),
      });
    }
    return chips;
  }, [syncFilter, showRetired, onShowRetiredChange, setSyncFilter]);

  const handleResetAll = () => {
    if (onResetAllFilters) {
      onResetAllFilters();
      return;
    }
    setStatusFilter('all');
    setHealthFilter('all');
    setSyncFilter('all');
    setSearchQuery('');
    onShowRetiredChange?.(false);
  };

  const moreActiveCount =
    (syncFilter === 'matched' || syncFilter === 'drift' ? 1 : 0) + (showRetired ? 1 : 0);
  const totalMatched = serverMode ? (serverFilteredTotal ?? hosts.length) : filteredHosts.length;
  const totalPages = serverMode ? Math.max(1, Math.ceil(totalMatched / Math.max(1, pageSize))) : 1;
  const currentPage = serverMode ? Math.min(Math.max(1, page), totalPages) : 1;
  const rangeStart = totalMatched === 0 ? 0 : (currentPage - 1) * pageSize + 1;
  const rangeEnd = totalMatched === 0 ? 0 : Math.min(totalMatched, (currentPage - 1) * pageSize + filteredHosts.length);
  const hasAnyActiveFilter =
    statusFilter !== 'all' ||
    healthFilter !== 'all' ||
    syncFilter !== 'all' ||
    Boolean(searchQuery.trim()) ||
    Boolean(showRetired);

  const isAllQuickView =
    statusFilter === 'all' && healthFilter === 'all' && syncFilter === 'all';
  const isUnhealthyActive =
    healthFilter === 'unhealthy' ||
    healthFilter === 'DEGRADED' ||
    healthFilter === 'UNSCHEDULABLE';

  const unifiedStatusViews = (
    <div
      data-testid="host-status-strip"
      className="flex flex-wrap items-center gap-1"
    >
      <button
        type="button"
        aria-pressed={isAllQuickView}
        aria-label="筛选全部主机"
        onClick={() => {
          if (onClearQuickView) {
            onClearQuickView();
            return;
          }
          setStatusFilter('all');
          setHealthFilter('all');
          setSyncFilter('all');
        }}
        className={cn(
          'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors',
          isAllQuickView
            ? 'border-foreground/35 bg-muted/60 font-medium text-foreground'
            : 'border-transparent text-muted-foreground hover:bg-muted/40 hover:text-foreground',
        )}
      >
        <Server className="h-3.5 w-3.5 shrink-0" />
        <span>全部主机</span>
        <span className="font-mono font-semibold tabular-nums text-foreground">{stats.total}</span>
      </button>

      <button
        type="button"
        aria-pressed={statusFilter === 'ONLINE'}
        aria-label="筛选在线主机"
        onClick={() => setStatusFilter(statusFilter === 'ONLINE' ? 'all' : 'ONLINE')}
        className={cn(
          'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors',
          statusFilter === 'ONLINE'
            ? 'border-success bg-success/10 font-medium text-foreground'
            : 'border-transparent text-muted-foreground hover:bg-success/5 hover:text-foreground',
        )}
      >
        <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-success" />
        <span>在线</span>
        <span className="font-mono font-semibold tabular-nums text-success">{stats.online}</span>
      </button>

      <button
        type="button"
        aria-pressed={statusFilter === 'DEGRADED'}
        aria-label="筛选告警主机"
        onClick={() => setStatusFilter(statusFilter === 'DEGRADED' ? 'all' : 'DEGRADED')}
        className={cn(
          'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors',
          statusFilter === 'DEGRADED'
            ? 'border-warning bg-warning/10 font-medium text-foreground'
            : 'border-transparent text-muted-foreground hover:bg-warning/5 hover:text-foreground',
        )}
      >
        <AlertTriangle
          className={cn(
            'h-3.5 w-3.5 shrink-0',
            stats.degraded > 0 ? 'text-warning' : 'text-muted-foreground',
          )}
        />
        <span>告警</span>
        <span
          className={cn(
            'font-mono font-semibold tabular-nums',
            stats.degraded > 0 ? 'text-warning' : 'text-muted-foreground',
          )}
        >
          {stats.degraded}
        </span>
      </button>

      <button
        type="button"
        aria-pressed={statusFilter === 'OFFLINE'}
        aria-label="筛选离线主机"
        onClick={() => setStatusFilter(statusFilter === 'OFFLINE' ? 'all' : 'OFFLINE')}
        className={cn(
          'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors',
          statusFilter === 'OFFLINE'
            ? 'border-foreground/35 bg-muted/60 font-medium text-foreground'
            : 'border-transparent text-muted-foreground hover:bg-muted/40 hover:text-foreground',
        )}
      >
        <Activity className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span>离线</span>
        <span className="font-mono font-semibold tabular-nums text-muted-foreground">
          {stats.offline}
        </span>
      </button>

      <span className="mx-1 hidden h-4 w-px bg-border sm:inline-block" aria-hidden="true" />

      <button
        type="button"
        aria-pressed={syncFilter === 'usb_fault'}
        onClick={() => setSyncFilter(syncFilter === 'usb_fault' ? 'all' : 'usb_fault')}
        className={cn(
          'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors',
          syncFilter === 'usb_fault'
            ? 'border-destructive/40 bg-destructive/10 font-medium text-destructive-text'
            : 'border-transparent text-muted-foreground hover:bg-muted/40 hover:text-foreground',
        )}
      >
        <span>USB / Hub 异常</span>
        <span
          className={cn(
            'font-mono font-semibold tabular-nums',
            stats.usbFaultCount > 0 ? 'text-destructive-text' : 'text-muted-foreground',
          )}
        >
          {stats.usbFaultCount}
        </span>
      </button>

      <button
        type="button"
        aria-pressed={syncFilter === 'upgrade_pending'}
        onClick={() => setSyncFilter(syncFilter === 'upgrade_pending' ? 'all' : 'upgrade_pending')}
        className={cn(
          'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors',
          syncFilter === 'upgrade_pending'
            ? 'border-warning/40 bg-warning/10 font-medium text-warning'
            : 'border-transparent text-muted-foreground hover:bg-muted/40 hover:text-foreground',
        )}
      >
        <span>待热更新</span>
        <span
          className={cn(
            'font-mono font-semibold tabular-nums',
            stats.agentDrift > 0 ? 'text-warning' : 'text-muted-foreground',
          )}
        >
          {stats.agentDrift}
        </span>
      </button>

      <button
        type="button"
        aria-pressed={isUnhealthyActive}
        title="筛选降级或禁调主机"
        onClick={() => setHealthFilter(isUnhealthyActive ? 'all' : 'unhealthy')}
        className={cn(
          'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors',
          isUnhealthyActive
            ? 'border-warning/40 bg-warning/10 font-medium text-warning'
            : 'border-transparent text-muted-foreground hover:bg-muted/40 hover:text-foreground',
        )}
      >
        <span>健康异常</span>
        <span
          className={cn(
            'font-mono font-semibold tabular-nums',
            stats.unhealthyCount > 0 ? 'text-warning' : 'text-muted-foreground',
          )}
        >
          {stats.unhealthyCount}
        </span>
      </button>

      <button
        type="button"
        aria-pressed={syncFilter === 'mount_error'}
        onClick={() => setSyncFilter(syncFilter === 'mount_error' ? 'all' : 'mount_error')}
        className={cn(
          'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors',
          syncFilter === 'mount_error'
            ? 'border-destructive/40 bg-destructive/10 font-medium text-destructive-text'
            : 'border-transparent text-muted-foreground hover:bg-muted/40 hover:text-foreground',
        )}
      >
        <span>挂载异常</span>
        <span
          className={cn(
            'font-mono font-semibold tabular-nums',
            stats.mountErrorCount > 0 ? 'text-destructive-text' : 'text-muted-foreground',
          )}
        >
          {stats.mountErrorCount}
        </span>
      </button>
    </div>
  );

  const workbenchHeaderRight = (
    <div className="flex flex-wrap items-center gap-2">
      {stats.agentTrackable > 0 ? (
        <span
          data-testid="host-agent-alignment-summary"
          className="text-xs text-muted-foreground whitespace-nowrap"
        >
          Agent 已对齐 {stats.agentAligned}/{stats.agentTrackable}
          {stats.agentDrift > 0 ? ` · ${stats.agentDrift} 台待热更新` : ''}
        </span>
      ) : null}
      {extraHeaderRight}
    </div>
  );

  return (
    <TooltipProvider>
      <div className="flex flex-1 min-h-0 flex-col gap-2">
        {/* 统一单行工作台：顶行合并状态统计与快捷视角，主筛选行严格单行 */}
        <div className="shrink-0">
          <FilterWorkbench
            headerLeft={unifiedStatusViews}
          headerRight={workbenchHeaderRight}
          hiddenChips={hiddenChips}
          hasAnyActiveFilter={hasAnyActiveFilter}
          onResetAll={handleResetAll}
          matchSummary={
            serverMode
              ? `匹配 ${totalMatched} / ${stats.total} 台主机`
              : `显示 ${filteredHosts.length} / ${hosts.length} 台主机`
          }
          chipsTestId="hosts-filter-chips"
        >
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center lg:flex-nowrap">
            <FilterSearchInput
              id="host-search"
              value={searchQuery}
              onChange={setSearchQuery}
              placeholder="搜索主机名 / IP / Agent 版本..."
              ariaLabel="搜索主机"
              testId="host-search-input"
              className="flex-1 min-w-[220px] sm:max-w-[360px]"
            />
            <SearchableSelect
              value={healthFilter}
              onChange={(v) => setHealthFilter(v as HostHealthFilter)}
              options={[
                { value: 'HEALTHY', label: '正常' },
                { value: 'unhealthy', label: '健康异常 (降级/禁调)' },
                { value: 'DEGRADED', label: '降级' },
                { value: 'UNSCHEDULABLE', label: '禁调' },
              ]}
              allLabel="全部健康度"
              searchable={false}
              icon={<ShieldAlert />}
              ariaLabel="按健康状态筛选"
              className="w-full sm:w-44"
              testId="host-health-filter"
            />
            <MoreFiltersPopover activeCount={moreActiveCount} testId="hosts-more-filters">
              <div className="space-y-1.5">
                <div className="text-[11px] font-medium text-muted-foreground">Agent 代码同步</div>
                <SearchableSelect
                  value={syncFilter}
                  onChange={(v) => setSyncFilter(v as HostSyncFilter)}
                  options={[
                    { value: 'upgrade_pending', label: '待热更新 (在线+漂移)' },
                    { value: 'drift', label: '内容漂移 (全部 drift)' },
                    { value: 'matched', label: '已对齐 (matched)' },
                    { value: 'mount_error', label: '存储挂载异常' },
                    { value: 'usb_fault', label: 'USB / Hub 异常' },
                  ]}
                  allLabel="全部同步状态"
                  searchable={false}
                  ariaLabel="按代码同步状态筛选"
                  className="w-full"
                  testId="host-sync-filter"
                />
              </div>
              {onShowRetiredChange ? (
                <label className="flex cursor-pointer items-center justify-between rounded-lg border border-border px-3 py-2 text-xs hover:bg-muted/30">
                  <span>显示已退役主机</span>
                  <input
                    type="checkbox"
                    checked={Boolean(showRetired)}
                    onChange={(e) => onShowRetiredChange(e.target.checked)}
                    data-testid="hosts-show-retired"
                    className="rounded"
                  />
                </label>
              ) : null}
            </MoreFiltersPopover>
          </div>
          </FilterWorkbench>
        </div>

        {/* 虚拟化高密主表：视口弹性锁高 + sticky top-0 表头 + 卡片底栏固定分页，1280×800 核心列免横滚 */}
        <div
          ref={tableContainerRef}
          className="flex flex-1 min-h-0 flex-col overflow-hidden rounded-xl border border-border bg-card"
        >
          <Table
            containerClassName="flex-1 min-h-0 overflow-auto"
            className="w-full min-w-[860px]"
          >
            <TableHeader className="sticky top-0 z-10 bg-muted/95 backdrop-blur-xs">
              <TableRow className="bg-muted/95 hover:bg-muted/95">
                {selectable ? (
                  <TableHead className="w-8 px-2 py-2">
                    <input
                      ref={selectAllRef}
                      type="checkbox"
                      checked={allFilteredSelected}
                      onChange={toggleAll}
                      aria-label={serverMode ? '选择本页主机' : '选择全部主机'}
                      className="h-4 w-4 rounded border-border accent-primary"
                    />
                  </TableHead>
                ) : null}
                <TableHead className="min-w-[104px] px-2 py-2 font-medium">
                  <button
                    type="button"
                    onClick={() => handleSort('name')}
                    className="inline-flex items-center gap-1 hover:text-foreground"
                  >
                    主机
                    <SortIcon active={sortKey === 'name'} direction={sortDirection} />
                  </button>
                </TableHead>
                <TableHead className="min-w-[116px] px-2 py-2 font-medium">状态</TableHead>
                <TableHead className="min-w-[144px] px-2 py-2 text-center font-medium whitespace-nowrap">
                  <button
                    type="button"
                    onClick={() => handleSort('devices')}
                    className="inline-flex items-center gap-1 hover:text-foreground"
                  >
                    设备 / 任务
                    <SortIcon active={sortKey === 'devices'} direction={sortDirection} />
                  </button>
                </TableHead>
                <TableHead className="w-[122px] px-1.5 py-2 text-center font-medium whitespace-nowrap 2xl:hidden">
                  资源
                </TableHead>
                <TableHead className="hidden min-w-[104px] px-2.5 py-2 font-medium 2xl:table-cell">
                  <button
                    type="button"
                    onClick={() => handleSort('cpu')}
                    className="inline-flex items-center gap-1 hover:text-foreground"
                  >
                    CPU
                    <SortIcon active={sortKey === 'cpu'} direction={sortDirection} />
                  </button>
                </TableHead>
                <TableHead className="hidden min-w-[104px] px-2.5 py-2 font-medium 2xl:table-cell">
                  <button
                    type="button"
                    onClick={() => handleSort('ram')}
                    className="inline-flex items-center gap-1 hover:text-foreground"
                  >
                    内存
                    <SortIcon active={sortKey === 'ram'} direction={sortDirection} />
                  </button>
                </TableHead>
                <TableHead className="hidden min-w-[104px] px-2.5 py-2 font-medium 2xl:table-cell">
                  <button
                    type="button"
                    onClick={() => handleSort('disk')}
                    className="inline-flex items-center gap-1 hover:text-foreground"
                  >
                    磁盘
                    <SortIcon active={sortKey === 'disk'} direction={sortDirection} />
                  </button>
                </TableHead>
                <TableHead className="w-[114px] px-2 py-2 font-medium whitespace-nowrap">Agent</TableHead>
                <TableHead className="hidden min-w-[92px] px-2.5 py-2 text-right font-medium 2xl:table-cell">
                  <button
                    type="button"
                    onClick={() => handleSort('heartbeat')}
                    className="inline-flex items-center gap-1 hover:text-foreground"
                  >
                    心跳
                    <SortIcon active={sortKey === 'heartbeat'} direction={sortDirection} />
                  </button>
                </TableHead>
                <TableHead className="w-[138px] px-2 py-2 text-right font-medium whitespace-nowrap">
                  操作
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {virtualPaddingTop > 0 ? (
                <tr aria-hidden="true">
                  <td colSpan={visibleColumnCount} style={{ height: `${virtualPaddingTop}px`, padding: 0 }} />
                </tr>
              ) : null}

              {virtualRows.map((virtualRow) => {
                const host = filteredHosts[virtualRow.index];
                if (!host) return null;
                const isDrawerActive = activeDrawerHost != null && String(activeDrawerHost.id) === String(host.id);
                const faultChips = buildInlineFaultChips(host);
                const visibleFaults = faultChips.slice(0, 2);
                const restFaults = faultChips.slice(2);
                const usb = host.usb_device_count;
                const usbKnown = typeof usb === 'number' && Number.isFinite(usb);
                const adbCount = host.device_count || 0;
                const usbMismatch = usbKnown && usb > adbCount;

                return (
                  <ClickableRow
                    key={host.id}
                    role="button"
                    aria-label={`查看主机 ${host.name || host.ip || host.id} 详情`}
                    data-state={selectedIds?.has(host.id) ? 'selected' : undefined}
                    onClick={() => openDrawer(host.id)}
                    className={cn(
                      'transition-colors hover:bg-muted/50',
                      isDrawerActive && 'bg-primary/5 ring-1 ring-inset ring-primary/25',
                      selectedIds?.has(host.id) && 'bg-primary/5 hover:bg-primary/10',
                    )}
                  >
                    {selectable ? (
                      <TableCell className="w-8 px-2 py-1.5">
                        <input
                          type="checkbox"
                          checked={selectedIds?.has(host.id) ?? false}
                          onClick={(e) => toggleSelect(host.id, e)}
                          onChange={() => {}}
                          aria-label={`选择主机 ${host.name ?? host.id}`}
                          className="h-4 w-4 rounded border-border accent-primary"
                        />
                      </TableCell>
                    ) : null}

                    <TableCell className="max-w-[164px] px-2 py-1.5">
                      <div className="truncate font-medium text-foreground" title={host.name ?? ''}>
                        {host.name}
                      </div>
                      {host.name !== host.ip ? (
                        <div className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground" title={host.ip}>
                          {host.ip}
                        </div>
                      ) : null}
                    </TableCell>

                    <TableCell className="px-2 py-1.5 whitespace-nowrap">
                      <div className="space-y-1">
                        <div
                          className="flex flex-nowrap items-center gap-1 whitespace-nowrap"
                          data-testid={`host-status-line-${host.id}`}
                        >
                          <StatusBadge kind="host" status={host.status} size="sm" />
                          {host.retired_at ? (
                            <span
                              className={cn(
                                'inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-1.5 py-0.5 text-[11px] font-medium cursor-help',
                                host.status === 'ONLINE'
                                  ? 'bg-warning/10 text-warning'
                                  : 'bg-muted text-muted-foreground',
                              )}
                              title={`已退役${host.retired_by ? `（${host.retired_by}）` : ''}${
                                host.retire_reason ? `：${host.retire_reason}` : ''
                              }`}
                              data-testid={`host-retired-badge-${host.id}`}
                            >
                              {host.status === 'ONLINE' ? '已退役但仍在心跳' : '已退役'}
                            </span>
                          ) : null}
                          {host.emptied_at ? (
                            <span
                              className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full bg-muted px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground cursor-help"
                              title={`设备面已由人工处置${host.emptied_by ? `（${host.emptied_by}）` : ''}${
                                host.emptied_reason ? `：${host.emptied_reason}` : ''
                              }——设备面告警（UsbBlind / 批量 offline）豁免中（ADR-0038 D9）`}
                              data-testid={`host-emptied-badge-${host.id}`}
                            >
                              设备面已处置
                            </span>
                          ) : null}
                          {host.health_status && host.health_status !== 'HEALTHY' ? (
                            <span
                              className={cn(
                                'inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-1.5 py-0.5 text-[11px] font-medium cursor-help',
                                host.health_status === 'UNSCHEDULABLE'
                                  ? 'bg-destructive/10 text-destructive-text'
                                  : 'bg-warning/10 text-warning',
                              )}
                              title={host.health_reasons?.map((r) => REASON_LABELS[r] || r).join(', ') || ''}
                            >
                              {host.health_status === 'UNSCHEDULABLE' ? '禁调' : '降级'}
                            </span>
                          ) : null}
                          {visibleFaults.length > 0 ? (
                            <span
                              className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap"
                              data-testid={`host-inline-faults-${host.id}`}
                            >
                              {visibleFaults.map((chip) => (
                                <span
                                  key={chip.key}
                                  title={chip.title}
                                  className={cn(
                                    'inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-1.5 py-0.5 text-[11px] font-medium leading-tight',
                                    chip.tone === 'danger'
                                      ? 'bg-destructive/10 text-destructive-text'
                                      : chip.tone === 'warning'
                                        ? 'bg-warning/10 text-warning'
                                        : chip.tone === 'info'
                                          ? 'bg-info/10 text-info'
                                          : 'bg-muted text-muted-foreground',
                                  )}
                                >
                                  {chip.label}
                                </span>
                              ))}
                              {restFaults.length > 0 ? (
                                <span
                                  className="inline-flex shrink-0 items-center whitespace-nowrap rounded-full bg-muted px-1.5 py-0.5 text-[11px] leading-tight text-muted-foreground cursor-help"
                                  title={restFaults.map((c) => c.title || c.label).join('；')}
                                >
                                  +{restFaults.length}
                                </span>
                              ) : null}
                            </span>
                          ) : null}
                        </div>

                        <div
                          className={cn(
                            'flex items-center gap-1 whitespace-nowrap text-[11px]',
                            host.watcher_admin_active !== false ? 'text-success' : 'text-destructive-text',
                          )}
                        >
                          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-current" />
                          Watch {host.watcher_admin_active !== false ? '已激活' : '未激活'}
                        </div>
                      </div>
                    </TableCell>

                    <TableCell className="px-2 py-1.5 text-center whitespace-nowrap">
                      <div className="inline-flex items-center gap-1">
                        {onNavigateToDevices ? (
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              onNavigateToDevices(host.id);
                            }}
                            className={cn(
                              'inline-flex items-center justify-center rounded-full px-1.5 py-0.5 text-[11px] font-medium transition-colors hover:underline',
                              adbCount > 0
                                ? 'bg-primary/10 text-primary hover:bg-primary/20'
                                : 'bg-muted/50 text-muted-foreground hover:bg-muted',
                            )}
                            title="点击跳转到设备页查看该主机下的设备"
                          >
                            在线 {adbCount}
                          </button>
                        ) : (
                          <span
                            className={cn(
                              'inline-flex items-center justify-center rounded-full px-1.5 py-0.5 text-[11px] font-medium',
                              adbCount > 0 ? 'bg-primary/10 text-primary' : 'bg-muted/50 text-muted-foreground',
                            )}
                            title={host.claim_hint ?? '在线设备数（来源：adb devices）'}
                          >
                            在线 {adbCount}
                          </span>
                        )}
                        <span
                          className={cn(
                            'inline-flex items-center justify-center rounded-full px-1.5 py-0.5 text-[11px] font-medium',
                            !usbKnown
                              ? 'bg-muted/50 text-muted-foreground'
                              : usbMismatch
                                ? 'bg-warning/10 text-warning'
                                : 'bg-muted/50 text-muted-foreground',
                          )}
                          title={
                            !usbKnown
                              ? 'USB 设备数未采集到（lsusb 不可用或采集失败）'
                              : usbMismatch
                                ? `USB 枚举 ${usb} 台 > ADB 在线 ${adbCount} 台：设备在 USB 上但 ADB 未枚举，可能存在授权/驱动/多 ADB server 问题（来源：lsusb）`
                                : 'USB 枚举到的疑似 Android 设备数（来源：lsusb），与 ADB 在线数对照'
                          }
                        >
                          USB {usbKnown ? usb : '—'}
                        </span>
                        <span
                          className={cn(
                            'inline-flex items-center justify-center rounded-full px-1.5 py-0.5 text-[11px] font-medium',
                            (host.active_tasks || 0) > 0
                              ? 'bg-info/10 text-info'
                              : 'bg-muted/50 text-muted-foreground',
                          )}
                        >
                          任务 {host.active_tasks || 0}
                        </span>
                      </div>
                    </TableCell>

                    <TableCell className="w-[122px] px-1.5 py-1.5 2xl:hidden">
                      {host.resources && host.status === 'ONLINE' ? (
                        <div className="mx-auto grid w-[112px] grid-cols-3 gap-0.5">
                          {[
                            ['CPU', host.resources.cpu_load],
                            ['内存', host.resources.ram_usage],
                            ['磁盘', host.resources.disk_usage],
                          ].map(([label, value]) => (
                            <div key={String(label)} className="text-center">
                              <div className="text-[10px] text-muted-foreground">{label}</div>
                              <div
                                data-testid={label === '磁盘' ? 'host-disk-usage' : undefined}
                                className={cn(
                                  'font-mono text-[11px]',
                                  typeof value === 'number'
                                    ? getResourceColor(value)
                                    : 'text-muted-foreground',
                                )}
                              >
                                {formatUsagePercent(typeof value === 'number' ? value : null)}
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="text-center text-muted-foreground/40">-</div>
                      )}
                    </TableCell>

                    <TableCell className="hidden px-2.5 py-1.5 2xl:table-cell">
                      {host.resources && host.status === 'ONLINE' ? (
                        <div className="flex items-center gap-2">
                          <Progress
                            value={host.resources.cpu_load}
                            className="h-2 w-14"
                            indicatorClassName={getProgressColor(host.resources.cpu_load)}
                          />
                          <span className={cn('font-mono text-xs', getResourceColor(host.resources.cpu_load))}>
                            {host.resources.cpu_load.toFixed(0)}%
                          </span>
                        </div>
                      ) : (
                        <span className="text-muted-foreground/40">-</span>
                      )}
                    </TableCell>

                    <TableCell className="hidden px-2.5 py-1.5 2xl:table-cell">
                      {host.resources && host.status === 'ONLINE' ? (
                        <div className="flex items-center gap-2">
                          <Progress
                            value={host.resources.ram_usage}
                            className="h-2 w-14"
                            indicatorClassName={getProgressColor(host.resources.ram_usage)}
                          />
                          <span className={cn('font-mono text-xs', getResourceColor(host.resources.ram_usage))}>
                            {host.resources.ram_usage.toFixed(0)}%
                          </span>
                        </div>
                      ) : (
                        <span className="text-muted-foreground/40">-</span>
                      )}
                    </TableCell>

                    <TableCell className="hidden px-2.5 py-1.5 2xl:table-cell">
                      {host.resources && host.status === 'ONLINE' ? (
                        host.resources.disk_usage == null ? (
                          <span data-testid="host-disk-usage" className="font-mono text-xs text-muted-foreground">
                            未知
                          </span>
                        ) : (
                          <div className="flex items-center gap-2">
                            <Progress
                              value={host.resources.disk_usage}
                              className="h-2 w-14"
                              indicatorClassName={getProgressColor(host.resources.disk_usage)}
                            />
                            <span
                              data-testid="host-disk-usage"
                              className={cn('font-mono text-xs', getResourceColor(host.resources.disk_usage))}
                            >
                              {host.resources.disk_usage.toFixed(0)}%
                            </span>
                          </div>
                        )
                      ) : (
                        <span className="text-muted-foreground/40">-</span>
                      )}
                    </TableCell>

                    <TableCell className="w-[114px] px-2 py-1.5">
                      {host.agent_installed ? (
                        <div className="flex w-[104px] flex-col gap-0.5">
                          <span
                            className="truncate font-mono text-[11px] text-foreground"
                            title={formatAgentVersionLabel(host)}
                          >
                            {formatAgentVersionLabel(host)}
                          </span>
                          <span
                            className={cn(
                              'inline-flex w-fit items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                              agentSyncBadgeClass(host.agent_code_sync_status),
                            )}
                            title={
                              host.agent_artifact_digest
                                ? `当前摘要 ${host.agent_artifact_digest.replace(/^sha256:/, '').slice(0, 12)}${host.expected_code_revision ? ` · 期望 HEAD @${host.expected_code_revision}` : ''}`
                                : host.expected_code_revision
                                  ? `未上报摘要 · 期望 HEAD @${host.expected_code_revision}`
                                  : undefined
                            }
                          >
                            {AGENT_SYNC_LABELS[host.agent_code_sync_status ?? 'unknown']}
                          </span>
                        </div>
                      ) : (
                        <span className="text-xs text-muted-foreground/40">未安装</span>
                      )}
                    </TableCell>

                    <TableCell
                      className="hidden px-2.5 py-1.5 text-right text-xs text-muted-foreground 2xl:table-cell"
                      title={host.last_heartbeat ? formatLocalTime(host.last_heartbeat) : undefined}
                    >
                      {host.last_heartbeat ? formatHeartbeatLabel(host.last_heartbeat) : '—'}
                    </TableCell>

                    <TableCell className="px-2 py-1.5 text-right whitespace-nowrap">
                      <div className="inline-flex items-center gap-1">
                        {host.retired_at ? (
                          <span className="text-xs text-muted-foreground/40">已退役</span>
                        ) : host.status === 'ONLINE' && onHotUpdate ? (
                          <>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                onHotUpdate(host.id);
                              }}
                              disabled={isHotUpdating?.(host.id) || isFlashPrereqs?.(host.id)}
                              aria-label={`${host.name ?? host.id} 热更新 Agent`}
                              className="inline-flex items-center gap-1 rounded-md bg-primary/10 px-1.5 py-1 text-[11px] font-medium text-primary transition-colors hover:bg-primary/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap"
                            >
                              {isHotUpdating?.(host.id) ? '更新中...' : '热更新'}
                            </button>
                            {onFlashPrereqs && host.agent_installed !== false ? (
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  onFlashPrereqs(host.id);
                                }}
                                disabled={isFlashPrereqs?.(host.id) || isHotUpdating?.(host.id)}
                                aria-label={`${host.name ?? host.id} 补齐刷机前置`}
                                title="补齐 dialout / udev / Qt 刷机依赖"
                                className="inline-flex items-center gap-1 rounded-md bg-muted/60 px-1.5 py-1 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap"
                              >
                                {isFlashPrereqs?.(host.id) ? '补齐中...' : '刷机前置'}
                              </button>
                            ) : null}
                          </>
                        ) : host.status !== 'ONLINE' && onInstall ? (
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              onInstall(host.id);
                            }}
                            disabled={isInstalling?.(host.id)}
                            aria-label={
                              host.agent_installed
                                ? `${host.name ?? host.id} 重新安装 Agent`
                                : `${host.name ?? host.id} 首次安装 Agent`
                            }
                            title={
                              host.agent_installed
                                ? 'Agent 曾安装成功，当前离线 — 可重新安装'
                                : '尚未检测到 Agent 安装记录'
                            }
                            className="inline-flex items-center gap-1 rounded-md bg-warning/10 px-1.5 py-1 text-[11px] font-medium text-warning transition-colors hover:bg-warning/20 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap"
                          >
                            {isInstalling?.(host.id)
                              ? '安装中...'
                              : host.agent_installed
                                ? '重新安装'
                                : '首次安装'}
                          </button>
                        ) : (
                          <span className="text-xs text-muted-foreground/40">-</span>
                        )}
                        {isAdmin && (onEdit || onDelete || onRetire || onUnretire) ? (
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <button
                                type="button"
                                onClick={(e) => e.stopPropagation()}
                                aria-label={`${host.name ?? host.id} 更多操作`}
                                className="inline-flex items-center justify-center rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                              >
                                <MoreHorizontal className="h-4 w-4" />
                              </button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end" className="w-32">
                              {onEdit ? (
                                <DropdownMenuItem onClick={() => onEdit(host)}>
                                  <Pencil className="mr-2 h-3.5 w-3.5" />
                                  编辑
                                </DropdownMenuItem>
                              ) : null}
                              {onRetire && !host.retired_at ? (
                                <DropdownMenuItem onClick={() => onRetire(host)}>
                                  <CircleSlash className="mr-2 h-3.5 w-3.5" />
                                  退役
                                </DropdownMenuItem>
                              ) : null}
                              {onUnretire && host.retired_at ? (
                                <DropdownMenuItem onClick={() => onUnretire(host)}>
                                  <RotateCcw className="mr-2 h-3.5 w-3.5" />
                                  解除退役
                                </DropdownMenuItem>
                              ) : null}
                              {onDelete ? (
                                <DropdownMenuItem
                                  disabled={isDeleting?.(host.id)}
                                  onClick={() => onDelete(host)}
                                  className="text-destructive-text focus:bg-destructive/10 focus:text-destructive-text"
                                >
                                  <Trash2 className="mr-2 h-3.5 w-3.5" />
                                  删除
                                </DropdownMenuItem>
                              ) : null}
                            </DropdownMenuContent>
                          </DropdownMenu>
                        ) : null}
                      </div>
                    </TableCell>
                  </ClickableRow>
                );
              })}

              {virtualPaddingBottom > 0 ? (
                <tr aria-hidden="true">
                  <td colSpan={visibleColumnCount} style={{ height: `${virtualPaddingBottom}px`, padding: 0 }} />
                </tr>
              ) : null}

              {filteredHosts.length === 0 ? (
                <TableEmptyRow colSpan={visibleColumnCount}>
                  {serverMode
                    ? (hasAnyActiveFilter ? '没有符合当前筛选条件的主机' : '暂无主机')
                    : (hosts.length === 0 ? '暂无主机' : '没有符合当前筛选条件的主机')}
                </TableEmptyRow>
              ) : null}
            </TableBody>
          </Table>

          {/* B3-2 服务端分页栏（固定在卡片底部，不随表格滚动移出视口） */}
          {serverMode && onPageChange ? (
            <div
              data-testid="hosts-pagination-bar"
              className="shrink-0 flex flex-wrap items-center justify-between gap-2 border-t border-border bg-card px-3 py-2 text-xs text-muted-foreground"
            >
              <div className="flex items-center gap-2">
                <span className="font-mono tabular-nums">
                  {totalMatched === 0
                    ? '共 0 台主机'
                    : `第 ${rangeStart}–${rangeEnd} / 共 ${totalMatched} 台`}
                </span>
                {isFetchingPage ? <span className="text-muted-foreground/70">刷新中…</span> : null}
              </div>
              <div className="flex flex-wrap items-center gap-3">
                {onPageSizeChange ? (
                  <div className="inline-flex items-center gap-1" role="group" aria-label="每页显示条数">
                    <span>每页</span>
                    {pageSizeOptions.map((size) => (
                      <button
                        key={size}
                        type="button"
                        data-testid={`hosts-page-size-${size}`}
                        aria-pressed={pageSize === size}
                        onClick={() => onPageSizeChange(size)}
                        className={cn(
                          'rounded border px-2 py-0.5 font-mono text-xs transition-colors',
                          pageSize === size
                            ? 'border-primary bg-primary/10 font-medium text-primary'
                            : 'border-border text-muted-foreground hover:bg-muted/50 hover:text-foreground',
                        )}
                      >
                        {size}
                      </button>
                    ))}
                  </div>
                ) : null}
                <div className="inline-flex items-center gap-1.5">
                  <button
                    type="button"
                    data-testid="hosts-prev-page"
                    disabled={currentPage <= 1}
                    onClick={() => onPageChange(Math.max(1, currentPage - 1))}
                    className="inline-flex items-center gap-0.5 rounded border border-border px-2 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <ChevronLeft className="h-3.5 w-3.5" />
                    <span>上一页</span>
                  </button>
                  <span className="px-1 font-mono tabular-nums text-foreground" data-testid="hosts-page-indicator">
                    {currentPage} / {totalPages}
                  </span>
                  <button
                    type="button"
                    data-testid="hosts-next-page"
                    disabled={currentPage >= totalPages}
                    onClick={() => onPageChange(Math.min(totalPages, currentPage + 1))}
                    className="inline-flex items-center gap-0.5 rounded border border-border px-2 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <span>下一页</span>
                    <ChevronRight className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            </div>
          ) : null}
        </div>

        {/* 右侧标准化主机详情抽屉（D2 / #465 / #4117 U3-1） */}
        <HostDetailDrawer
          host={activeDrawerHost}
          onClose={() => setActiveDrawerHostId(null)}
          isAdmin={isAdmin}
          canManageWatcherAdminState={canManageWatcherAdminState}
          onWatcherAdminStateChange={onWatcherAdminStateChange}
          isWatcherAdminStateUpdating={isWatcherAdminStateUpdating}
          onNavigateToDevices={onNavigateToDevices}
          onSetDeviceIntent={onSetDeviceIntent}
          onClearDeviceIntent={onClearDeviceIntent}
          isDeviceIntentUpdating={isDeviceIntentUpdating}
          onHotUpdate={onHotUpdate}
          isHotUpdating={isHotUpdating}
          onInstall={onInstall}
          isInstalling={isInstalling}
          hasScriptPresenceLoader={Boolean(onLoadHostScriptPresence)}
          presence={activeDrawerHost ? presenceByHost[String(activeDrawerHost.id)] : undefined}
          refreshingPresence={activeDrawerHost ? presenceRefreshing.has(activeDrawerHost.id) : false}
          onReloadScriptPresence={
            onLoadHostScriptPresence ? (hostId) => void loadHostPresence(hostId, true) : undefined
          }
          onRefreshScriptPresence={
            onRefreshHostScriptPresence ? (hostId) => void refreshHostPresence(hostId) : undefined
          }
          currentIndex={activeDrawerIndex >= 0 ? activeDrawerIndex : undefined}
          totalCount={activeDrawerIndex >= 0 ? filteredHosts.length : undefined}
          onPrev={
            activeDrawerIndex > 0
              ? () => setActiveDrawerHostId(String(filteredHosts[activeDrawerIndex - 1].id))
              : undefined
          }
          onNext={
            activeDrawerIndex >= 0 && activeDrawerIndex < filteredHosts.length - 1
              ? () => setActiveDrawerHostId(String(filteredHosts[activeDrawerIndex + 1].id))
              : undefined
          }
          reasonLabels={REASON_LABELS}
          usbKernelErrorLabels={USB_KERNEL_ERROR_LABELS}
          probeTopologyLabels={PROBE_TOPOLOGY_LABELS}
          probeVerdictLabels={PROBE_VERDICT_LABELS}
          formatHeartbeatLabel={formatHeartbeatLabel}
        />

        {/* 全选后底部悬浮操作条会挡住最后一行；用真实占位撑开滚动 */}
        {selectable && selectedIds && selectedIds.size > 0 ? (
          <BulkBarSpacer testId="host-table-selection-spacer" />
        ) : null}
      </div>
    </TooltipProvider>
  );
}
