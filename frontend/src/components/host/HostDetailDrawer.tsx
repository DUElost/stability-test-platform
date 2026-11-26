import { useState } from 'react';
import { Link, useInRouterContext } from 'react-router-dom';
import {
  Check,
  ChevronLeft,
  ChevronRight,
  ClipboardCheck,
  Clock,
  Copy,
  Cpu,
  ExternalLink,
  FolderSync,
  HardDrive,
  MemoryStick,
  RefreshCw,
  Server,
  Sparkles,
  Usb,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { StatusBadge } from '@/components/ui/status-badge';
import { cn } from '@/lib/utils';
import { resourceUsageBgClass, resourceUsageTextClass, TEXT } from '@/design-system/tokens';
import {
  formatBytesFromGb,
  formatDateTimeFull,
  formatDurationSeconds,
  formatLocalTime,
} from '@/utils/format';
import type {
  HostScriptPresence,
  ScriptPresenceItem,
  ScriptPresenceState,
} from '@/utils/api/types';
import type {
  AgentCodeSyncStatus,
  HostTableData,
} from '@/components/network/ExpandableHostTable';

export interface HostPresenceLoad {
  status: 'loading' | 'ready' | 'error';
  data?: HostScriptPresence;
  error?: string;
}

interface HostDetailDrawerProps {
  host: HostTableData | null;
  onClose: () => void;
  isAdmin?: boolean;
  canManageWatcherAdminState?: boolean;
  onWatcherAdminStateChange?: (hostId: string | number, nextActive: boolean) => void;
  isWatcherAdminStateUpdating?: (hostId: string | number) => boolean;
  onNavigateToDevices?: (hostId: string | number) => void;
  onSetDeviceIntent?: (host: HostTableData) => void;
  onClearDeviceIntent?: (host: HostTableData) => void;
  isDeviceIntentUpdating?: (hostId: string | number) => boolean;
  onHotUpdate?: (hostId: string | number) => void;
  isHotUpdating?: (hostId: string | number) => boolean;
  onInstall?: (hostId: string | number) => void;
  isInstalling?: (hostId: string | number) => boolean;
  /** 按需脚本在位状态（仅抽屉打开时由父组件触发懒加载） */
  hasScriptPresenceLoader?: boolean;
  presence?: HostPresenceLoad;
  refreshingPresence?: boolean;
  onReloadScriptPresence?: (hostId: string | number) => void;
  onRefreshScriptPresence?: (hostId: string | number) => void;
  /** 列表内上下翻页导航（基于当前筛选排序后的主机序列） */
  currentIndex?: number;
  totalCount?: number;
  onPrev?: () => void;
  onNext?: () => void;
  /** 标签与格式化工具回调由抽屉内聚，便于独立复用 */
  reasonLabels: Record<string, string>;
  usbKernelErrorLabels: Record<string, string>;
  probeTopologyLabels: Record<string, string>;
  probeVerdictLabels: Record<string, string>;
  formatHeartbeatLabel: (value?: string) => string;
}

const AGENT_SYNC_LABELS: Record<AgentCodeSyncStatus, string> = {
  matched: '已对齐',
  pending: '待上报',
  drift: '内容漂移',
  unknown: '未知',
};

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

const SCRIPT_PRESENCE_LABELS: Record<ScriptPresenceState, string> = {
  present: '在位',
  missing: '缺失',
  mismatch: '内容不符',
  unknown: '未知',
  n_a: '不适用',
  maintenance: '维护窗',
};

const SCRIPT_PRESENCE_ORDER: Record<ScriptPresenceState, number> = {
  missing: 0,
  mismatch: 1,
  unknown: 2,
  maintenance: 3,
  present: 4,
  n_a: 5,
};

function scriptPresenceStateClass(state: ScriptPresenceState): string {
  switch (state) {
    case 'present':
      return 'bg-success/10 text-success';
    case 'missing':
    case 'mismatch':
      return 'bg-destructive/10 text-destructive-text';
    case 'maintenance':
      return 'bg-warning/10 text-warning';
    default:
      return 'bg-muted/50 text-muted-foreground';
  }
}

function sortScriptPresenceItems(items: ScriptPresenceItem[]): ScriptPresenceItem[] {
  return [...items].sort((a, b) => {
    const byState = SCRIPT_PRESENCE_ORDER[a.state] - SCRIPT_PRESENCE_ORDER[b.state];
    if (byState !== 0) return byState;
    return `${a.name}@${a.version}`.localeCompare(`${b.name}@${b.version}`);
  });
}

function formatUsagePercent(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return '未知';
  return `${value.toFixed(digits)}%`;
}

/**
 * 主机右侧标准详情抽屉（#4117 U3-1 / D2 / #465）。
 *
 * 替代旧表内跨行展开：
 * - 基于标准 `ui/sheet.tsx`（size="lg"，焦点陷阱、Esc 关闭与焦点归位）；
 * - 抽屉内核心标识/路径/异常原因/脚本明细允许完整换行显示，不做二次截断；
 * - 补齐 `mount_status` 呈现并链往 `/storage`，脚本在位区块链往 `/script-management`，底栏提供 AI 诊断深链。
 */
export function HostDetailDrawer({
  host,
  onClose,
  isAdmin = false,
  canManageWatcherAdminState = false,
  onWatcherAdminStateChange,
  isWatcherAdminStateUpdating,
  onNavigateToDevices,
  onSetDeviceIntent,
  onClearDeviceIntent,
  isDeviceIntentUpdating,
  onHotUpdate,
  isHotUpdating,
  onInstall,
  isInstalling,
  hasScriptPresenceLoader = false,
  presence,
  refreshingPresence = false,
  onReloadScriptPresence,
  onRefreshScriptPresence,
  currentIndex,
  totalCount,
  onPrev,
  onNext,
  reasonLabels,
  usbKernelErrorLabels,
  probeTopologyLabels,
  probeVerdictLabels,
  formatHeartbeatLabel,
}: HostDetailDrawerProps) {
  const inRouter = useInRouterContext();
  const [copiedIp, setCopiedIp] = useState<string | null>(null);

  const handleCopyIp = async (ip: string) => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(ip);
      }
      setCopiedIp(ip);
      window.setTimeout(() => {
        setCopiedIp((prev) => (prev === ip ? null : prev));
      }, 1500);
    } catch {
      // ignore clipboard denial
    }
  };

  const presenceItems = presence?.data ? sortScriptPresenceItems(presence.data.items) : [];
  const mountEntries = host?.mount_status ?? [];
  const aiPrompt = host
    ? `请协助诊断主机 ${host.name || host.ip}（ID #${host.id}，IP ${host.ip}，状态 ${host.status}，健康度 ${host.health_status ?? 'HEALTHY'}，在线设备 ${host.device_count ?? 0}，USB ${host.usb_device_count ?? '未知'}）的当前健康、USB/Hub 或挂载异常。`
    : '';

  return (
    <Sheet
      open={Boolean(host)}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) onClose();
      }}
    >
      {host ? (
        <SheetContent
          side="right"
          size="lg"
          hideCloseButton
          overlayTestId="fleet-host-drawer-overlay"
          data-testid="fleet-host-drawer"
          aria-label={`主机 ${host.name || host.ip || host.id} 详情`}
        >
          <SheetHeader className="flex-row items-start justify-between gap-3">
            <div className="min-w-0 flex-1 space-y-1">
              <SheetDescription className="flex flex-wrap items-center gap-1.5 text-xs">
                <span className="font-mono">Host #{host.id}</span>
                {host.ip ? (
                  <>
                    <span>·</span>
                    <span className="font-mono">{host.ip}</span>
                    <button
                      type="button"
                      onClick={() => void handleCopyIp(host.ip)}
                      aria-label="复制主机 IP"
                      title="复制主机 IP"
                      className="inline-flex h-5 w-5 items-center justify-center rounded border border-border text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                    >
                      {copiedIp === host.ip ? (
                        <Check className="h-3 w-3 text-success" />
                      ) : (
                        <Copy className="h-3 w-3" />
                      )}
                    </button>
                  </>
                ) : null}
                {onNavigateToDevices ? (
                  <>
                    <span>·</span>
                    <button
                      type="button"
                      onClick={() => onNavigateToDevices(host.id)}
                      className="inline-flex items-center gap-1 text-primary hover:underline"
                    >
                      在线设备 {host.device_count ?? 0}
                      <ExternalLink className="h-3 w-3" />
                    </button>
                  </>
                ) : null}
              </SheetDescription>

              <div className="flex flex-wrap items-center gap-2">
                <SheetTitle className="break-words text-base font-semibold">
                  {host.name || host.ip}
                </SheetTitle>
                <StatusBadge kind="host" status={host.status} size="sm" />
                {host.health_status && host.health_status !== 'HEALTHY' ? (
                  <span
                    className={cn(
                      'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium',
                      host.health_status === 'UNSCHEDULABLE'
                        ? 'bg-destructive/10 text-destructive-text'
                        : 'bg-warning/15 text-warning',
                    )}
                  >
                    {host.health_status === 'UNSCHEDULABLE' ? '禁调' : '降级'}
                  </span>
                ) : null}
                {host.retired_at ? (
                  <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                    {host.status === 'ONLINE' ? '已退役但仍在心跳' : '已退役'}
                  </span>
                ) : null}
                {host.emptied_at ? (
                  <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                    设备面已处置
                  </span>
                ) : null}
              </div>
            </div>

            <div className="flex shrink-0 items-center gap-1">
              {currentIndex != null && totalCount != null && totalCount > 1 ? (
                <div className="mr-1 flex items-center gap-0.5 rounded-md border border-border bg-muted/40 p-0.5">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label="上一台主机"
                    data-testid="fleet-host-drawer-prev"
                    disabled={!onPrev || currentIndex <= 0}
                    onClick={onPrev}
                    className="h-6 w-6 p-0"
                  >
                    <ChevronLeft className="h-3.5 w-3.5" />
                  </Button>
                  <span className="px-1.5 font-mono text-[11px] tabular-nums text-muted-foreground">
                    {currentIndex + 1}/{totalCount}
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label="下一台主机"
                    data-testid="fleet-host-drawer-next"
                    disabled={!onNext || currentIndex >= totalCount - 1}
                    onClick={onNext}
                    className="h-6 w-6 p-0"
                  >
                    <ChevronRight className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ) : null}
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={onClose}
                data-testid="fleet-host-drawer-close"
                aria-label="关闭主机详情"
                className="h-7 w-7 p-0"
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          </SheetHeader>

          <SheetBody className="space-y-4">
            {/* 健康异常原因完整展示（不截断） */}
            {(host.health_reasons?.length || host.retired_at || host.emptied_at) ? (
              <section className="space-y-1.5 rounded-lg border border-border bg-muted/30 p-3 text-xs">
                {host.health_reasons && host.health_reasons.length > 0 ? (
                  <div className="flex items-start gap-2">
                    <span className={cn('shrink-0 whitespace-nowrap', TEXT.subtitle)}>健康告警</span>
                    <div className="min-w-0 flex-1 break-words whitespace-normal text-foreground">
                      {host.health_reasons.map((r) => reasonLabels[r] || r).join('；')}
                    </div>
                  </div>
                ) : null}
                {host.retired_at ? (
                  <div className="flex items-start gap-2">
                    <span className={cn('shrink-0 whitespace-nowrap', TEXT.subtitle)}>退役记录</span>
                    <div className="min-w-0 flex-1 break-words whitespace-normal text-foreground">
                      {host.retired_by ? `${host.retired_by} · ` : ''}
                      {formatDateTimeFull(host.retired_at)}
                      {host.retire_reason ? ` · ${host.retire_reason}` : ''}
                    </div>
                  </div>
                ) : null}
                {host.emptied_at ? (
                  <div className="flex items-start gap-2">
                    <span className={cn('shrink-0 whitespace-nowrap', TEXT.subtitle)}>设备面豁免</span>
                    <div className="min-w-0 flex-1 break-words whitespace-normal text-foreground">
                      {host.emptied_by ? `${host.emptied_by} · ` : ''}
                      {formatDateTimeFull(host.emptied_at)}
                      {host.emptied_reason ? ` · ${host.emptied_reason}` : ''}
                    </div>
                  </div>
                ) : null}
              </section>
            ) : null}

            {/* 1. 运行资源与调度管理 */}
            <section className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {/* CPU */}
              <div className="rounded-lg border border-border bg-card p-3">
                <div className="mb-2 flex items-center gap-2">
                  <Cpu className="h-4 w-4 text-muted-foreground" />
                  <span className="text-sm font-medium text-foreground">CPU</span>
                </div>
                {host.resources ? (
                  <div className="space-y-1.5 text-xs">
                    <div className="flex justify-between">
                      <span className={TEXT.subtitle}>负载</span>
                      <span className={cn('font-mono', resourceUsageTextClass(host.resources.cpu_load))}>
                        {host.resources.cpu_load.toFixed(1)}%
                      </span>
                    </div>
                    <Progress
                      value={host.resources.cpu_load}
                      className="h-1.5"
                      indicatorClassName={resourceUsageBgClass(host.resources.cpu_load)}
                    />
                    {host.resources.cpu_cores ? (
                      <div className="flex justify-between">
                        <span className={TEXT.subtitle}>核心数</span>
                        <span className="font-mono text-foreground">{host.resources.cpu_cores}</span>
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <span className="text-xs text-muted-foreground">无数据</span>
                )}
              </div>

              {/* 内存 */}
              <div className="rounded-lg border border-border bg-card p-3">
                <div className="mb-2 flex items-center gap-2">
                  <MemoryStick className="h-4 w-4 text-muted-foreground" />
                  <span className="text-sm font-medium text-foreground">内存</span>
                </div>
                {host.resources ? (
                  <div className="space-y-1.5 text-xs">
                    <div className="flex justify-between">
                      <span className={TEXT.subtitle}>使用率</span>
                      <span className={cn('font-mono', resourceUsageTextClass(host.resources.ram_usage))}>
                        {host.resources.ram_usage.toFixed(1)}%
                      </span>
                    </div>
                    <Progress
                      value={host.resources.ram_usage}
                      className="h-1.5"
                      indicatorClassName={resourceUsageBgClass(host.resources.ram_usage)}
                    />
                    {host.resources.ram_total_gb ? (
                      <div className="flex justify-between">
                        <span className={TEXT.subtitle}>总量</span>
                        <span className="font-mono text-foreground">
                          {formatBytesFromGb(host.resources.ram_total_gb)}
                        </span>
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <span className="text-xs text-muted-foreground">无数据</span>
                )}
              </div>

              {/* 磁盘 */}
              <div className="rounded-lg border border-border bg-card p-3">
                <div className="mb-2 flex items-center gap-2">
                  <HardDrive className="h-4 w-4 text-muted-foreground" />
                  <span className="text-sm font-medium text-foreground">磁盘</span>
                </div>
                {host.resources ? (
                  <div className="space-y-1.5 text-xs">
                    <div className="flex justify-between">
                      <span className={TEXT.subtitle}>使用率</span>
                      <span
                        className={cn(
                          'font-mono',
                          host.resources.disk_usage == null
                            ? 'text-muted-foreground'
                            : resourceUsageTextClass(host.resources.disk_usage),
                        )}
                      >
                        {formatUsagePercent(host.resources.disk_usage, 1)}
                      </span>
                    </div>
                    {host.resources.disk_usage != null ? (
                      <Progress
                        value={host.resources.disk_usage}
                        className="h-1.5"
                        indicatorClassName={resourceUsageBgClass(host.resources.disk_usage)}
                      />
                    ) : null}
                    {host.resources.disk_total_gb ? (
                      <div className="flex justify-between">
                        <span className={TEXT.subtitle}>总量</span>
                        <span className="font-mono text-foreground">
                          {formatBytesFromGb(host.resources.disk_total_gb)}
                        </span>
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <span className="text-xs text-muted-foreground">无数据</span>
                )}
              </div>

              {/* 其他与 Watch 管理 */}
              <div className="rounded-lg border border-border bg-card p-3">
                <div className="mb-2 flex items-center gap-2">
                  <Clock className="h-4 w-4 text-muted-foreground" />
                  <span className="text-sm font-medium text-foreground">运行与 Watch</span>
                </div>
                <div className="space-y-1.5 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className={cn('shrink-0 whitespace-nowrap', TEXT.subtitle)}>Watch</span>
                    <div className="flex items-center gap-2">
                      <span className={host.watcher_admin_active !== false ? 'text-success' : 'text-destructive-text'}>
                        {host.watcher_admin_active !== false ? '已激活' : '未激活'}
                      </span>
                      {onWatcherAdminStateChange ? (
                        <button
                          type="button"
                          role="switch"
                          aria-checked={host.watcher_admin_active !== false}
                          aria-label={`${host.name ?? host.id} Watcher 管理开关`}
                          disabled={
                            !canManageWatcherAdminState ||
                            Boolean(isWatcherAdminStateUpdating?.(host.id))
                          }
                          data-testid={`watcher-admin-toggle-${host.id}`}
                          onClick={() =>
                            onWatcherAdminStateChange(host.id, !(host.watcher_admin_active !== false))
                          }
                          className={cn(
                            'relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full',
                            'border-2 border-transparent transition-colors',
                            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
                            'disabled:cursor-not-allowed disabled:opacity-50',
                            host.watcher_admin_active !== false ? 'bg-success' : 'bg-muted',
                          )}
                        >
                          <span
                            className={cn(
                              'pointer-events-none inline-block h-4 w-4 rounded-full bg-card shadow-sm',
                              'ring-0 transition-transform',
                              host.watcher_admin_active !== false ? 'translate-x-4' : 'translate-x-0',
                            )}
                          />
                        </button>
                      ) : null}
                    </div>
                  </div>
                  <div className="flex items-start justify-between gap-2">
                    <span className={cn('shrink-0 whitespace-nowrap', TEXT.subtitle)}>最近心跳</span>
                    <span
                      className="min-w-0 break-words text-right font-mono text-foreground"
                      title={host.last_heartbeat ? formatLocalTime(host.last_heartbeat) : undefined}
                    >
                      {formatHeartbeatLabel(host.last_heartbeat)}
                    </span>
                  </div>
                  {host.resources?.temperature !== undefined ? (
                    <div className="flex justify-between gap-2">
                      <span className={cn('shrink-0 whitespace-nowrap', TEXT.subtitle)}>温度</span>
                      <span
                        className={cn(
                          'font-mono',
                          host.resources.temperature > 80
                            ? 'text-destructive-text'
                            : host.resources.temperature > 60
                              ? 'text-warning'
                              : 'text-foreground',
                        )}
                      >
                        {host.resources.temperature.toFixed(1)}°C
                      </span>
                    </div>
                  ) : null}
                  {host.resources?.uptime_seconds !== undefined ? (
                    <div className="flex justify-between gap-2">
                      <span className={cn('shrink-0 whitespace-nowrap', TEXT.subtitle)}>运行时间</span>
                      <span className="font-mono text-foreground">
                        {formatDurationSeconds(host.resources.uptime_seconds, 'compact')}
                      </span>
                    </div>
                  ) : null}
                </div>
              </div>
            </section>

            {/* 2. 存储挂载状态（[L2] 显性化并链往 /storage） */}
            <section
              data-testid={`host-mount-status-${host.id}`}
              className="rounded-lg border border-border bg-card p-3.5"
            >
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <FolderSync className="h-4 w-4 text-muted-foreground" />
                  <h3 className="text-sm font-medium text-foreground">存储挂载</h3>
                </div>
                {inRouter ? (
                  <Link
                    to="/storage"
                    className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                  >
                    前往文件服务器
                    <ExternalLink className="h-3 w-3" />
                  </Link>
                ) : null}
              </div>
              {mountEntries.length > 0 ? (
                <ul className="divide-y divide-border/60 text-xs">
                  {mountEntries.map((m) => (
                    <li key={m.path} className="flex items-start justify-between gap-3 py-1.5">
                      <span className="min-w-0 flex-1 break-all font-mono text-foreground">
                        {m.path}
                      </span>
                      <div className="flex shrink-0 items-center gap-2">
                        {m.available_gb != null && m.total_gb != null ? (
                          <span className="font-mono text-[11px] text-muted-foreground">
                            可用 {formatBytesFromGb(m.available_gb)} / {formatBytesFromGb(m.total_gb)}
                          </span>
                        ) : null}
                        <span
                          className={cn(
                            'rounded-full px-1.5 py-0.5 text-[11px] font-medium',
                            m.mounted
                              ? 'bg-success/10 text-success'
                              : 'bg-destructive/10 text-destructive-text',
                          )}
                        >
                          {m.mounted ? '已挂载' : '未挂载'}
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-muted-foreground">暂无挂载状态上报</p>
              )}
            </section>

            {/* 3. Agent 版本与对齐 */}
            <section className="rounded-lg border border-border bg-card p-3.5">
              <div className="mb-2 flex items-center gap-2">
                <Server className="h-4 w-4 text-muted-foreground" />
                <h3 className="text-sm font-medium text-foreground">Agent 版本</h3>
              </div>
              {host.agent_installed ? (
                <div className="space-y-1.5 text-xs">
                  <div className="flex items-start justify-between gap-3">
                    <span className={cn('shrink-0 whitespace-nowrap', TEXT.subtitle)}>协议</span>
                    <span className="min-w-0 break-all text-right font-mono text-foreground">
                      {host.agent_protocol_version ?? '—'}
                    </span>
                  </div>
                  <div className="flex items-start justify-between gap-3">
                    <span className={cn('shrink-0 whitespace-nowrap', TEXT.subtitle)}>部署摘要</span>
                    <span
                      className="min-w-0 break-all text-right font-mono text-foreground"
                      title={host.agent_artifact_digest ?? undefined}
                    >
                      {host.agent_artifact_digest
                        ? host.agent_artifact_digest.replace(/^sha256:/, '').slice(0, 12)
                        : '未上报'}
                    </span>
                  </div>
                  <div className="flex items-start justify-between gap-3">
                    <span className={cn('shrink-0 whitespace-nowrap', TEXT.subtitle)}>上报修订</span>
                    <span className="min-w-0 break-all text-right font-mono text-foreground">
                      {host.agent_code_revision ? `@${host.agent_code_revision}` : '—'}
                    </span>
                  </div>
                  <div className="flex items-start justify-between gap-3">
                    <span className={cn('shrink-0 whitespace-nowrap', TEXT.subtitle)}>期望修订</span>
                    <span className="min-w-0 break-all text-right font-mono text-foreground">
                      {host.expected_code_revision ? `@${host.expected_code_revision}` : '—'}
                    </span>
                  </div>
                  <div className="flex items-start justify-between gap-3">
                    <span className={cn('shrink-0 whitespace-nowrap', TEXT.subtitle)}>热更新部署</span>
                    <span className="min-w-0 break-all text-right font-mono text-foreground">
                      {host.agent_code_deployed ? `@${host.agent_code_deployed}` : '—'}
                    </span>
                  </div>
                  {host.agent_code_deployed_at ? (
                    <div className="flex items-start justify-between gap-3">
                      <span className={cn('shrink-0 whitespace-nowrap', TEXT.subtitle)}>部署时间</span>
                      <span className="min-w-0 break-words text-right font-mono text-foreground">
                        {formatDateTimeFull(host.agent_code_deployed_at)}
                      </span>
                    </div>
                  ) : null}
                  <div className="flex items-center justify-between gap-3">
                    <span className={cn('shrink-0 whitespace-nowrap', TEXT.subtitle)}>对齐状态</span>
                    <span
                      className={cn(
                        'rounded-full px-1.5 py-0.5 text-[11px] font-medium',
                        agentSyncBadgeClass(host.agent_code_sync_status),
                      )}
                      title={
                        host.agent_code_sync_status === 'matched' &&
                        host.expected_code_revision &&
                        host.agent_code_revision &&
                        host.expected_code_revision !== host.agent_code_revision
                          ? '期望修订随每次合入前进；载荷摘要未变则无需重推——对齐判据是「部署摘要」，不是修订文本'
                          : undefined
                      }
                    >
                      {AGENT_SYNC_LABELS[host.agent_code_sync_status ?? 'unknown']}
                    </span>
                  </div>
                  <div className="pt-0.5 text-[11px] leading-snug text-muted-foreground">
                    对齐以「部署摘要」判定；修订文本仅作溯源，期望修订随每次合入前进
                  </div>
                </div>
              ) : (
                <span className="text-xs text-muted-foreground">未安装 Agent</span>
              )}
            </section>

            {/* 4. USB / Hub 与对账诊断 */}
            <section
              data-testid={`host-usb-diagnostics-${host.id}`}
              className="rounded-lg border border-border bg-card p-3.5"
            >
              <div className="mb-2.5 flex flex-wrap items-center gap-2">
                <Usb className="h-4 w-4 text-muted-foreground" />
                <h3 className="text-sm font-medium text-foreground">USB / Hub 与对账诊断</h3>
                {host.usb_kernel_log ? (
                  <span
                    className={cn(
                      'rounded-full px-1.5 py-0.5 text-[11px] font-medium',
                      host.usb_kernel_log === 'ok'
                        ? 'bg-success/10 text-success'
                        : host.usb_kernel_log === 'unavailable'
                          ? 'bg-destructive/10 text-destructive-text'
                          : 'bg-muted text-muted-foreground',
                    )}
                  >
                    内核日志通道：
                    {host.usb_kernel_log === 'ok'
                      ? '正常'
                      : host.usb_kernel_log === 'unavailable'
                        ? '不可读'
                        : '未知'}
                  </span>
                ) : null}
                {isAdmin && !host.retired_at && (onSetDeviceIntent || onClearDeviceIntent) ? (
                  <div className="ml-auto flex items-center gap-1.5">
                    {host.emptied_at
                      ? onClearDeviceIntent && (
                          <button
                            type="button"
                            disabled={isDeviceIntentUpdating?.(host.id)}
                            onClick={() => onClearDeviceIntent(host)}
                            data-testid={`host-clear-emptied-${host.id}`}
                            className="inline-flex items-center gap-1 rounded-md bg-muted px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted/80 disabled:opacity-50"
                          >
                            解除设备面已处置
                          </button>
                        )
                      : onSetDeviceIntent && (
                          <button
                            type="button"
                            disabled={isDeviceIntentUpdating?.(host.id)}
                            onClick={() => onSetDeviceIntent(host)}
                            data-testid={`host-set-emptied-${host.id}`}
                            className="inline-flex items-center gap-1 rounded-md bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted/80 hover:text-foreground disabled:opacity-50"
                          >
                            标记设备面已处置（空置）
                          </button>
                        )}
                  </div>
                ) : null}
              </div>

              <div className="grid grid-cols-1 gap-3 text-xs sm:grid-cols-2">
                <div className="space-y-1">
                  <div className="font-medium text-muted-foreground">设备可见性（L1–L4）</div>
                  <div className="flex justify-between gap-2">
                    <span className="shrink-0 whitespace-nowrap text-muted-foreground">
                      USB / ADB 接口 / 在线
                    </span>
                    <span className="font-mono text-foreground">
                      {host.usb_device_count ?? '—'} / {host.adb_interface_count ?? '—'} /{' '}
                      {host.device_count ?? 0}
                    </span>
                  </div>
                  {host.ledger_device_count != null ? (
                    <div className="flex justify-between gap-2">
                      <span className="shrink-0 whitespace-nowrap text-muted-foreground">
                        台账在册设备
                      </span>
                      <span className="font-mono text-foreground">
                        {host.ledger_device_count}
                      </span>
                    </div>
                  ) : null}
                  {host.ledger_latest_seen_at ? (
                    <div className="flex justify-between gap-2 text-[11px]">
                      <span className="shrink-0 whitespace-nowrap text-muted-foreground">
                        台账最近活跃
                      </span>
                      <span className="font-mono text-foreground">
                        {formatDateTimeFull(host.ledger_latest_seen_at)}
                      </span>
                    </div>
                  ) : null}
                  {host.adb_state_counts ? (
                    <div className="flex flex-wrap gap-1.5 text-[11px] text-muted-foreground">
                      <span>device {host.adb_state_counts.device ?? 0}</span>
                      <span
                        className={
                          (host.adb_state_counts.offline ?? 0) > 0
                            ? 'font-medium text-warning'
                            : undefined
                        }
                      >
                        offline {host.adb_state_counts.offline ?? 0}
                      </span>
                      <span
                        className={
                          (host.adb_state_counts.unauthorized ?? 0) > 0
                            ? 'font-medium text-warning'
                            : undefined
                        }
                      >
                        unauth {host.adb_state_counts.unauthorized ?? 0}
                      </span>
                    </div>
                  ) : null}
                </div>

                <div className="space-y-1">
                  <div className="font-medium text-muted-foreground">外接 Hub 拓扑（sysfs）</div>
                  {host.usb_hub_topology ? (
                    <>
                      <div className="flex justify-between gap-2">
                        <span className="shrink-0 whitespace-nowrap text-muted-foreground">
                          级联 Hub / 无驱动半枚举
                        </span>
                        <span
                          className={cn(
                            'font-mono',
                            host.usb_hub_topology.unbound_hubs > 0
                              ? 'font-semibold text-warning'
                              : 'text-foreground',
                          )}
                        >
                          {host.usb_hub_topology.cascade_hubs} / {host.usb_hub_topology.unbound_hubs}
                        </span>
                      </div>
                      {host.usb_hub_topology.unbound_hubs > 0 ? (
                        <div className="break-words text-[11px] text-muted-foreground">
                          {host.usb_hub_topology.unbound_idle_bus_hubs >=
                          host.usb_hub_topology.unbound_hubs
                            ? '仅落在无在用设备的闲置总线（SS 退化分支）'
                            : `含 ${host.usb_hub_topology.unbound_hubs - host.usb_hub_topology.unbound_idle_bus_hubs} 只在用总线 Hub 未绑定驱动`}
                        </div>
                      ) : null}
                    </>
                  ) : (
                    <div className="text-muted-foreground">待 Agent 升级后上报</div>
                  )}
                </div>

                <div className="space-y-1">
                  <div className="font-medium text-muted-foreground">主控与端口体检（sysfs）</div>
                  {host.usb_controller_health ? (
                    (() => {
                      const c = host.usb_controller_health;
                      const items: Array<{ label: string; count: number; danger: boolean }> = [
                        { label: 'xHCI 失绑', count: c.unbound_xhci_controllers, danger: true },
                        { label: '根口禁用', count: c.disabled_root_ports, danger: true },
                        { label: '过流端口', count: c.over_current_ports, danger: true },
                        { label: 'Hub 口禁用', count: c.disabled_hub_ports, danger: false },
                        { label: '卡死(default)', count: c.stuck_default_ports, danger: false },
                      ].filter((item) => item.count > 0);
                      if (items.length === 0) {
                        return <div className="text-success">主控与端口状态正常</div>;
                      }
                      return (
                        <div className="flex flex-wrap gap-1.5">
                          {items.map((item) => (
                            <span
                              key={item.label}
                              className={cn(
                                'rounded px-1.5 py-0.5 font-mono text-[11px]',
                                item.danger
                                  ? 'bg-destructive/10 text-destructive-text'
                                  : 'bg-warning/10 text-warning',
                              )}
                            >
                              {item.label}: {item.count}
                            </span>
                          ))}
                        </div>
                      );
                    })()
                  ) : (
                    <div className="text-muted-foreground">待 Agent 升级后上报</div>
                  )}
                </div>

                <div className="space-y-1">
                  <div className="font-medium text-muted-foreground">近 1 小时内核 USB 错误</div>
                  {host.usb_kernel_errors_1h ? (
                    (() => {
                      const nonZero = Object.entries(host.usb_kernel_errors_1h).filter(
                        ([, v]) => typeof v === 'number' && v > 0,
                      );
                      if (nonZero.length === 0) {
                        return <div className="text-success">0 条错误</div>;
                      }
                      return (
                        <div className="flex flex-wrap gap-1.5">
                          {nonZero.map(([k, v]) => (
                            <span
                              key={k}
                              className="rounded bg-warning/10 px-1.5 py-0.5 font-mono text-[11px] text-warning"
                            >
                              {usbKernelErrorLabels[k] || k}: {v}
                            </span>
                          ))}
                        </div>
                      );
                    })()
                  ) : (
                    <div className="text-muted-foreground">无计数数据</div>
                  )}
                </div>

                <div className="space-y-1">
                  <div className="font-medium text-muted-foreground">SSH 拓扑对账探针</div>
                  {host.health_probe ? (
                    <>
                      <div className="flex items-start justify-between gap-2">
                        <span className="shrink-0 whitespace-nowrap text-muted-foreground">
                          拓扑 / 结论
                        </span>
                        <span
                          className={cn(
                            'min-w-0 break-words text-right font-medium',
                            host.health_probe.strike_open
                              ? 'text-destructive-text'
                              : 'text-foreground',
                          )}
                        >
                          {probeTopologyLabels[host.health_probe.topology ?? ''] ??
                            host.health_probe.topology ??
                            '—'}
                          {' · '}
                          {probeVerdictLabels[host.health_probe.verdict ?? ''] ??
                            host.health_probe.verdict ??
                            '—'}
                        </span>
                      </div>
                      <div className="flex justify-between gap-2 text-[11px] text-muted-foreground">
                        <span className="shrink-0 whitespace-nowrap">连续异常窗口</span>
                        <span
                          className={
                            host.health_probe.strike_open
                              ? 'font-medium text-destructive-text'
                              : undefined
                          }
                        >
                          {host.health_probe.strike_open ? '已触发（需核查）' : '正常'}
                        </span>
                      </div>
                      {(host.health_probe.uhubctl_status ||
                        (host.health_probe.powered_off_ports?.length ?? 0) > 0) ? (
                        <div className="mt-1.5 space-y-1 border-t border-border/60 pt-1.5 text-[11px]">
                          <div className="flex items-start justify-between gap-2">
                            <span className="shrink-0 whitespace-nowrap text-muted-foreground">
                              VBUS 供电（uhubctl）
                            </span>
                            {(host.health_probe.powered_off_ports?.length ?? 0) > 0 ? (
                              <span className="font-medium text-info">
                                {host.health_probe.powered_off_ports!.length} 口断电（0000 off）
                              </span>
                            ) : host.health_probe.uhubctl_status === 'ok' ? (
                              <span className="text-success">全部已供电</span>
                            ) : host.health_probe.uhubctl_status === 'unavailable' ? (
                              <span className="text-muted-foreground">未安装（可点刷机前置补装）</span>
                            ) : host.health_probe.uhubctl_status === 'skipped_unbound_hub' ? (
                              <span className="text-muted-foreground">已跳过（存在半枚举 Hub）</span>
                            ) : host.health_probe.uhubctl_status === 'timeout' ? (
                              <span className="text-warning">巡检超时</span>
                            ) : (
                              <span className="text-muted-foreground">未采集</span>
                            )}
                          </div>
                          {(host.health_probe.powered_off_ports?.length ?? 0) > 0 ? (
                            <>
                              <div className="flex flex-wrap gap-1">
                                {host.health_probe.powered_off_ports!.map((port) => {
                                  const serial = host.usb_power_slot_serials?.[port];
                                  return (
                                    <span
                                      key={port}
                                      className="rounded bg-info/10 px-1.5 py-0.5 font-mono text-[11px] text-info"
                                    >
                                      {serial ? `${port} (${serial})` : port}
                                    </span>
                                  );
                                })}
                              </div>
                              <div className="text-[10px] leading-snug text-muted-foreground">
                                端口供电被 uhubctl 关闭（非端口损坏），可用 uhubctl -a on 恢复供电
                              </div>
                            </>
                          ) : null}
                        </div>
                      ) : null}
                    </>
                  ) : (
                    <div className="text-muted-foreground">暂无探针记录</div>
                  )}
                  {host.self_heal ? (
                    <div className="mt-1.5 space-y-1 border-t border-border/60 pt-1.5 text-[11px]">
                      <div className="flex items-start justify-between gap-2">
                        <span className="shrink-0 whitespace-nowrap text-muted-foreground">
                          自愈熔断态
                        </span>
                        <span
                          className={cn(
                            'min-w-0 break-words text-right',
                            host.self_heal.fuse_open
                              ? 'font-medium text-destructive-text'
                              : 'text-foreground',
                          )}
                        >
                          {host.self_heal.fuse_open
                            ? `${host.self_heal.state === 'hardware_suspect' ? '疑似硬件' : '需人工'} (${host.self_heal.fuse_reason ?? '—'})`
                            : host.self_heal.state ?? 'idle'}
                        </span>
                      </div>
                      {host.self_heal.xhci ? (
                        <div className="flex items-start justify-between gap-2 text-muted-foreground">
                          <span className="shrink-0 whitespace-nowrap">xHCI 计数 (boot/48h)</span>
                          <span className="min-w-0 break-all text-right font-mono">
                            {host.self_heal.xhci.boot_attempts ?? 0} /{' '}
                            {host.self_heal.xhci.window_attempts ?? 0}
                            {host.self_heal.xhci.last_marker
                              ? ` · ${host.self_heal.xhci.last_marker}`
                              : ''}
                          </span>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </div>
            </section>

            {/* 5. 脚本在位（抽屉打开时按需懒加载 + 链往 /script-management） */}
            {hasScriptPresenceLoader ? (
              <section
                data-testid={`host-script-presence-${host.id}`}
                className="rounded-lg border border-border bg-card p-3.5"
              >
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <ClipboardCheck className="h-4 w-4 text-muted-foreground" />
                  <h3 className="text-sm font-medium text-foreground">脚本在位</h3>
                  {presence?.data?.checked_at ? (
                    <span
                      className="text-[11px] text-muted-foreground"
                      title={formatLocalTime(presence.data.checked_at)}
                    >
                      核验于 {formatHeartbeatLabel(presence.data.checked_at)}
                    </span>
                  ) : null}
                  <div className="ml-auto flex items-center gap-2">
                    {inRouter ? (
                      <Link
                        to="/script-management"
                        className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                      >
                        脚本库
                        <ExternalLink className="h-3 w-3" />
                      </Link>
                    ) : null}
                    {isAdmin && onRefreshScriptPresence ? (
                      <button
                        type="button"
                        onClick={() => onRefreshScriptPresence(host.id)}
                        disabled={refreshingPresence}
                        aria-label={`${host.name ?? host.id} 重新核验脚本在位`}
                        className="inline-flex items-center gap-1 rounded-md bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary transition-colors hover:bg-primary/15 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <RefreshCw className={cn('h-3.5 w-3.5', refreshingPresence && 'animate-spin')} />
                        {refreshingPresence ? '核验中...' : '重新核验'}
                      </button>
                    ) : null}
                  </div>
                </div>

                {presence?.status === 'error' ? (
                  <p className="mb-2 break-words text-[11px] text-destructive-text">
                    核验数据加载失败：{presence.error || '未知错误'}
                    {onReloadScriptPresence ? (
                      <button
                        type="button"
                        onClick={() => onReloadScriptPresence(host.id)}
                        className="ml-2 rounded-md px-1.5 py-0.5 font-medium text-primary hover:bg-primary/10"
                      >
                        重试
                      </button>
                    ) : null}
                  </p>
                ) : null}

                {!presence || (presence.status === 'loading' && !presence.data) ? (
                  <span className="text-xs text-muted-foreground">加载中...</span>
                ) : presence.data ? (
                  <div className="space-y-1.5">
                    <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                      <span>在位 {presence.data.counts.present}</span>
                      <span
                        className={
                          presence.data.counts.missing + presence.data.counts.mismatch > 0
                            ? 'text-destructive-text'
                            : undefined
                        }
                      >
                        缺口 {presence.data.counts.missing + presence.data.counts.mismatch}
                        （缺失 {presence.data.counts.missing} · 内容不符{' '}
                        {presence.data.counts.mismatch}）
                      </span>
                      <span>未知 {presence.data.counts.unknown}</span>
                      <span>维护窗 {presence.data.counts.maintenance}</span>
                    </div>
                    {presenceItems.length === 0 ? (
                      presence.data.counts.n_a > 0 ? (
                        <p className="text-xs text-muted-foreground">
                          本轮已 sweep，全部 {presence.data.counts.n_a} 项均「不适用」（可达集与目标版本不相交）。
                        </p>
                      ) : (
                        <p className="text-xs text-muted-foreground">
                          暂无条目——该主机尚未跑过 sweep，可点「重新核验」。
                        </p>
                      )
                    ) : (
                      <ul className="divide-y divide-border/60">
                        {presenceItems.map((item) => (
                          <li
                            key={`${item.name}@${item.version}`}
                            className="flex flex-wrap items-start gap-2 py-1.5"
                          >
                            <span
                              className={cn(
                                'mt-px inline-flex shrink-0 items-center rounded-full px-1.5 py-0.5 text-[11px] font-medium',
                                scriptPresenceStateClass(item.state),
                              )}
                            >
                              {SCRIPT_PRESENCE_LABELS[item.state] ?? item.state}
                            </span>
                            <span className="shrink-0 font-mono text-xs text-foreground">
                              {item.name}@{item.version}
                            </span>
                            {item.detail ? (
                              <span className="min-w-0 flex-1 break-all whitespace-normal text-[11px] text-muted-foreground">
                                {item.detail}
                              </span>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ) : null}
              </section>
            ) : null}
          </SheetBody>

          <SheetFooter className="justify-between">
            {inRouter ? (
              <Button variant="outline" size="sm" asChild className="gap-1.5">
                <Link to={`/assistant?prompt=${encodeURIComponent(aiPrompt)}`}>
                  <Sparkles className="h-3.5 w-3.5 text-primary" />
                  向 AI 助手提问
                </Link>
              </Button>
            ) : (
              <span />
            )}
            <div className="flex items-center gap-2">
              {!host.retired_at && host.status === 'ONLINE' && onHotUpdate ? (
                <Button
                  type="button"
                  size="sm"
                  disabled={isHotUpdating?.(host.id)}
                  onClick={() => onHotUpdate(host.id)}
                >
                  {isHotUpdating?.(host.id) ? '更新中...' : '热更新 Agent'}
                </Button>
              ) : !host.retired_at && host.status !== 'ONLINE' && onInstall ? (
                <Button
                  type="button"
                  size="sm"
                  disabled={isInstalling?.(host.id)}
                  onClick={() => onInstall(host.id)}
                >
                  {isInstalling?.(host.id)
                    ? '安装中...'
                    : host.agent_installed
                      ? '重新安装'
                      : '首次安装'}
                </Button>
              ) : null}
              <Button type="button" variant="outline" size="sm" onClick={onClose}>
                关闭
              </Button>
            </div>
          </SheetFooter>
        </SheetContent>
      ) : null}
    </Sheet>
  );
}
